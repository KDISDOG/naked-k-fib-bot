"""
Position Syncer — 倉位同步模組

解決的核心問題：
  機器人開倉後，TP/SL 在幣安端觸發時，本地 DB 不知道。
  這個模組定時輪詢幣安倉位狀態，偵測：
    1. 完全平倉（TP2 或 SL 觸發）→ 更新 DB status=closed
    2. 部分平倉（TP1 觸發）→ 更新 DB status=partial + 觸發 breakeven stop
    3. 手動平倉 → 同步狀態

工作流程（每 30 秒執行一次）：
  for each open trade in DB:
    1. 查幣安實際倉位
    2. 比對數量差異
    3. 若有平倉 → 更新 DB + 計算 PnL + 手續費
    4. 若 TP1 已觸發（qty 減半）→ 觸發 breakeven stop
"""
import logging
from typing import Optional
import pandas_ta as ta
from binance.client import Client
from config import Config
from risk_manager import RiskManager
from api_retry import retry_api
from notifier import notify

log = logging.getLogger("syncer")


class PositionSyncer:
    # 孤兒單掃蕩頻率：每 N 次 sync 跑一次（預設 2 → 每分鐘，SYNC_SEC=30 的情況）
    _ORPHAN_SWEEP_EVERY = 2

    def __init__(self, client: Client, db, executor):
        self.client   = client
        self.db       = db
        self.executor = executor
        self._sync_count = 0
        # 啟動時立刻掃一次孤兒單（清掉重啟前累積的殘留）
        try:
            self._sweep_orphan_orders()
        except Exception as e:
            log.error(f"啟動時孤兒單掃蕩失敗: {e}")

    def sync(self):
        """主同步邏輯：比對 DB 和幣安實際倉位"""
        self._sync_count += 1

        open_trades = self.db.get_open_trades()

        # 孤兒單掃蕩（獨立路徑，即使無 open_trades 也要跑）
        if self._sync_count % self._ORPHAN_SWEEP_EVERY == 0:
            try:
                self._sweep_orphan_orders()
            except Exception as e:
                log.error(f"孤兒單掃蕩失敗: {e}")

        if not open_trades:
            return

        # 取得幣安所有倉位（帶重試）
        try:
            positions = retry_api(self.client.futures_position_information)
        except Exception as e:
            log.error(f"取得幣安倉位失敗: {e}")
            notify.error("倉位同步失敗", f"無法取得幣安倉位: {e}")
            return

        # 建立 symbol → positionAmt 映射
        position_map: dict[str, float] = {}
        for pos in positions:
            amt = float(pos["positionAmt"])
            if amt != 0:
                position_map[pos["symbol"]] = amt

        for trade in open_trades:
            try:
                symbol    = trade["symbol"]
                trade_id  = trade["id"]
                direction = trade["direction"]
                entry     = trade["entry"]
                qty       = trade["qty"]
                qty_closed = trade["qty_closed"]

                # 幣安端實際持倉量
                actual_amt = position_map.get(symbol, 0)
                # LONG = 正，SHORT = 負
                if direction == "LONG":
                    actual_qty = actual_amt
                else:
                    actual_qty = abs(actual_amt)

                expected_remaining = qty - qty_closed

                # ── 情況 1: 完全平倉 ────────────────────────────────
                if actual_qty <= 0 or abs(actual_qty) < 0.0001:
                    log.info(f"[{symbol}] #{trade_id} 偵測到完全平倉")

                    # 先清殘留掛單（reduceOnly TP/SL 不會自動取消）
                    # 放最前面：即使後續 close_trade/notify 丟例外，
                    # 孤兒單已經被清掉
                    try:
                        self.client.futures_cancel_all_open_orders(symbol=symbol)
                        log.info(f"[{symbol}] 已清除平倉後殘留掛單")
                    except Exception as ce:
                        log.warning(f"[{symbol}] 清除殘留掛單失敗: {ce}")

                    exit_price = self._get_last_trade_price(symbol)
                    fee = self._get_recent_fee(symbol, qty)

                    # 判斷平倉原因
                    reason = self._detect_close_reason(trade, exit_price or entry)

                    self.db.close_trade(
                        trade_id     = trade_id,
                        exit_price   = exit_price or entry,
                        fee          = fee,
                        partial      = False,
                        close_reason = reason,
                    )
                    # 計算淨盈虧通知
                    _ep = exit_price or entry
                    if direction == "LONG":
                        _raw_pnl = (_ep - entry) * qty
                    else:
                        _raw_pnl = (entry - _ep) * qty
                    _net = _raw_pnl - fee
                    notify.trade_closed(symbol, direction, _net, reason)
                    continue

                # ── 情況 2: 部分平倉（TP1 觸發）──────────────────────
                qty_diff = expected_remaining - actual_qty
                if qty_diff > 0.0005:
                    # 有一部分被平掉了（很可能是 TP1）
                    log.info(
                        f"[{symbol}] #{trade_id} 偵測到部分平倉："
                        f"預期剩餘={expected_remaining:.4f} "
                        f"實際={actual_qty:.4f} 差異={qty_diff:.4f}"
                    )
                    # 估算平倉價格（用 TP1 價格）
                    exit_price = trade["tp1"] or self._get_last_trade_price(symbol)
                    fee = RiskManager.estimate_fee(qty_diff, exit_price or entry)

                    self.db.close_trade(
                        trade_id     = trade_id,
                        exit_price   = exit_price or entry,
                        fee          = fee,
                        partial      = True,
                        closed_qty   = qty_diff,
                        close_reason = "TP1",
                    )

                    # 觸發 Breakeven Stop（如果還沒移過）
                    if not trade["breakeven"]:
                        try:
                            self.executor.move_to_breakeven(
                                symbol      = symbol,
                                trade_id    = trade_id,
                                entry_price = entry,
                                direction   = direction,
                            )
                        except Exception as be:
                            log.error(f"[{symbol}] #{trade_id} move_to_breakeven 失敗: {be}")

                    # TP1 後自動啟用追蹤止盈（所有策略通用）
                    if Config.TRAILING_ACTIVATE_AFTER_TP1 and \
                            not trade.get("use_trailing"):
                        try:
                            atr_val = self._get_current_atr(
                                symbol, trade.get("timeframe", "15m")
                            )
                            if atr_val and atr_val > 0:
                                self.db.enable_trailing(trade_id, atr_val)
                                log.info(
                                    f"[{symbol}] TP1 後啟用追蹤止盈"
                                    f"（ATR={atr_val:.4f}，"
                                    f"距離={Config.TRAILING_ATR_MULT}×ATR）"
                                )
                        except Exception as te:
                            log.error(f"[{symbol}] #{trade_id} 啟用追蹤止盈失敗: {te}")

                # ── 情況 3: 倉位不變 → 檢查是否需要推進追蹤止盈 ─────
                if trade.get("use_trailing"):
                    try:
                        self._update_trailing(trade)
                    except Exception as ute:
                        log.error(f"[{symbol}] #{trade_id} _update_trailing 失敗: {ute}")

            except Exception as e:
                log.error(
                    f"[{trade.get('symbol', '?')}] "
                    f"#{trade.get('id', '?')} 同步處理失敗，跳過此筆: {e}"
                )

    # ── 孤兒單掃蕩 ───────────────────────────────────────────────
    def _sweep_orphan_orders(self):
        """
        清除孤兒單：幣安上有掛單、但 DB 中沒有對應 open/partial trade
        且幣安實際持倉為 0 的 symbol，全部取消。

        產生孤兒的情境：
          - TP2 觸發全平後，殘留的 reduceOnly SL / TP1 不會自動取消
          - 歷史上因重複下單產生的冗餘訂單
          - 手動在幣安 APP 平倉後未同步
        """
        try:
            all_open = retry_api(self.client.futures_get_open_orders)
        except Exception as e:
            log.warning(f"取得全站掛單失敗: {e}")
            return

        if not all_open:
            return

        # 幣安上有實際持倉的 symbols
        try:
            positions = retry_api(self.client.futures_position_information)
            has_pos = {
                p["symbol"] for p in positions
                if abs(float(p.get("positionAmt", 0))) > 0
            }
        except Exception as e:
            log.warning(f"取得持倉資訊失敗: {e}")
            return

        # DB 中仍 open/partial 的 symbols
        open_trades = self.db.get_open_trades()
        db_symbols = {t["symbol"] for t in open_trades}

        # 按 symbol 分組，找出「有掛單但無持倉 且 DB 無紀錄」的孤兒
        symbols_in_orders: dict[str, int] = {}
        for o in all_open:
            sym = o["symbol"]
            symbols_in_orders[sym] = symbols_in_orders.get(sym, 0) + 1

        cleaned_total = 0
        for sym, cnt in symbols_in_orders.items():
            if sym in has_pos:
                continue  # 有持倉，不動
            if sym in db_symbols:
                continue  # DB 還認為是 open，先不動（避免誤刪正常待觸發單）
            # 孤兒 symbol：無持倉 + DB 無紀錄 → 全清
            try:
                self.client.futures_cancel_all_open_orders(symbol=sym)
                cleaned_total += cnt
                log.warning(f"[{sym}] 孤兒單掃蕩：取消 {cnt} 筆")
            except Exception as e:
                log.warning(f"[{sym}] 取消孤兒單失敗: {e}")

        if cleaned_total:
            log.warning(f"孤兒單掃蕩完成，共清除 {cleaned_total} 筆")

    # ── 追蹤止盈邏輯 ─────────────────────────────────────────────
    def _update_trailing(self, trade: dict):
        """
        追蹤止盈（v5.2 增強版）：
          1. 取當前價格，更新 LONG 最高價 / SHORT 最低價
          2. 啟動條件：
             - TP1 已成交（status=partial）→ 立即啟動（不需等 1R）
             - 尚未 TP1 → 需達 1R 獲利才啟動
          3. 追蹤距離 = TRAILING_ATR_MULT × ATR（預設 1.5×ATR）
          4. 止損只能朝獲利方向推進，不能後退
        """
        symbol = trade["symbol"]
        trade_id = trade["id"]
        direction = trade["direction"]
        entry = trade["entry"]
        sl = trade["sl"]
        atr = trade.get("trailing_atr") or 0
        if not atr or atr <= 0:
            return

        try:
            ticker = retry_api(
                self.client.futures_symbol_ticker, symbol=symbol
            )
            price = float(ticker["price"])
        except Exception:
            return

        # 更新高/低點
        self.db.update_trailing_price(trade_id, price)

        # 啟動門檻：TP1 已成交 → 立即追蹤；否則需 1R 獲利
        is_partial = trade.get("status") == "partial"
        if not is_partial:
            risk = abs(entry - sl)
            if risk <= 0:
                return
            profit = (price - entry) if direction == "LONG" else (entry - price)
            if profit < risk:
                return

        # 從 DB 重讀最新極值
        latest = self.db.get_trade_by_id(trade_id)
        if not latest:
            return

        trail_dist = Config.TRAILING_ATR_MULT * atr

        # 最小推進步長：SL 必須比現值前進 >= min_step 才換單
        # 避免 peak 微幅上移（0.01%）也觸發 → 每 30 秒重複下單
        min_step = Config.TRAILING_MIN_STEP_ATR * atr

        # Breakeven 下限（含來回手續費 + 0.02% buffer ≈ 0.1%）
        # 讓 trailing 至少鎖住「真實保本」價，避免 SL 觸發後還輸手續費
        be_buffer = 0.001  # 0.1%

        if direction == "LONG":
            peak = latest.get("highest_price") or price
            breakeven_price = entry * (1 + be_buffer)
            # TP1 後 trailing 下限 = breakeven；未 TP1 時下限 = entry
            floor_price = breakeven_price if is_partial else entry
            new_sl = max(peak - trail_dist, floor_price)
            # 止損只能往上推，且需大於最小步長
            advance = new_sl - sl if sl is not None else new_sl - floor_price
            if new_sl > floor_price and advance >= min_step:
                self.executor.move_trailing_sl(
                    symbol, trade_id, new_sl, direction
                )
                log.info(
                    f"[{symbol}] LONG trailing: peak={peak:.4f} "
                    f"new_sl={new_sl:.4f} floor={floor_price:.4f} "
                    f"advance={advance:.4f} (min={min_step:.4f})"
                )
        else:
            trough = latest.get("lowest_price") or price
            breakeven_price = entry * (1 - be_buffer)
            floor_price = breakeven_price if is_partial else entry
            new_sl = min(trough + trail_dist, floor_price)
            advance = sl - new_sl if sl is not None else floor_price - new_sl
            if new_sl < floor_price and advance >= min_step:
                self.executor.move_trailing_sl(
                    symbol, trade_id, new_sl, direction
                )
                log.info(
                    f"[{symbol}] SHORT trailing: trough={trough:.4f} "
                    f"new_sl={new_sl:.4f} floor={floor_price:.4f} "
                    f"advance={advance:.4f} (min={min_step:.4f})"
                )

    # ── 即時 ATR 取得（追蹤止盈用）────────────────────────────────
    def _get_current_atr(self, symbol: str,
                         interval: str = "15m") -> Optional[float]:
        """取得當前 ATR(14) 值，用於 TP1 後啟用追蹤止盈"""
        try:
            import pandas as pd
            raw = retry_api(
                self.client.futures_klines,
                symbol=symbol, interval=interval, limit=30
            )
            df = pd.DataFrame(raw, columns=[
                "time", "open", "high", "low", "close", "volume",
                "close_time", "qav", "trades", "tbav", "tbqv", "ignore"
            ])
            for col in ["high", "low", "close"]:
                df[col] = df[col].astype(float)
            atr_s = ta.atr(df["high"], df["low"], df["close"], length=14)
            if atr_s is not None and not atr_s.empty:
                return float(atr_s.iloc[-1])
        except Exception as e:
            log.warning(f"[{symbol}] 取得 ATR 失敗: {e}")
        return None

    # ── 平倉原因判斷 ─────────────────────────────────────────────
    def _detect_close_reason(self, trade: dict, exit_price: float) -> str:
        """
        根據平倉價格與 TP/SL 比較，推斷平倉原因。
        已走過 partial（TP1）的單，完全平倉時比對 TP2/SL。
        """
        if not exit_price or exit_price <= 0:
            return "UNKNOWN"

        direction = trade["direction"]
        tp1 = trade.get("tp1") or 0
        tp2 = trade.get("tp2") or 0
        sl  = trade.get("sl") or 0
        entry = trade.get("entry") or 0
        was_partial = trade.get("status") == "partial"

        # 容差：價格的 0.3%（避免滑價造成誤判）
        tol = entry * 0.003 if entry > 0 else 0

        if direction == "LONG":
            if tp2 > 0 and exit_price >= tp2 - tol:
                return "TP2"
            if tp1 > 0 and not was_partial and exit_price >= tp1 - tol:
                return "TP1"
            if sl > 0 and exit_price <= sl + tol:
                return "SL"
            # breakeven 觸發（止損在入場價附近）
            if trade.get("breakeven") and abs(exit_price - entry) <= tol:
                return "BREAKEVEN"
        else:  # SHORT
            if tp2 > 0 and exit_price <= tp2 + tol:
                return "TP2"
            if tp1 > 0 and not was_partial and exit_price <= tp1 + tol:
                return "TP1"
            if sl > 0 and exit_price >= sl - tol:
                return "SL"
            if trade.get("breakeven") and abs(exit_price - entry) <= tol:
                return "BREAKEVEN"

        # 追蹤止盈
        if trade.get("use_trailing"):
            return "TRAILING"

        return "UNKNOWN"

    # ── 輔助方法 ─────────────────────────────────────────────────

    def _get_last_trade_price(self, symbol: str) -> Optional[float]:
        """取得最近一筆成交價格"""
        try:
            trades = retry_api(
                self.client.futures_account_trades,
                symbol=symbol, limit=5
            )
            if trades:
                return float(trades[-1]["price"])
        except Exception as e:
            log.warning(f"取得 {symbol} 最近成交價失敗: {e}")
        return None

    def _get_recent_fee(self, symbol: str, qty: float) -> float:
        """取得最近交易的實際手續費"""
        try:
            trades = retry_api(
                self.client.futures_account_trades,
                symbol=symbol, limit=10
            )
            total_fee = 0.0
            for t in trades:
                total_fee += float(t.get("commission", 0))
            return total_fee
        except Exception as e:
            log.warning(f"取得 {symbol} 手續費失敗，使用估算值: {e}")
            # fallback: 估算
            return RiskManager.estimate_round_trip_fee(qty, 1.0, 1.0)
