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
    def __init__(self, client: Client, db, executor):
        self.client   = client
        self.db       = db
        self.executor = executor
        self._sync_count = 0          # 用於限制孤兒清理頻率

    # ── 孤兒掛單清理 ─────────────────────────────────────────────
    def cleanup_orphan_orders(self):
        """
        取消幣安上所有「無對應實際倉位」的掛單（TP/SL 孤兒）。
        場景：SL 觸發後 TP 掛單沒被撤銷，積累過多掛單。
        安全：只撤銷沒有真實倉位的 symbol 的掛單。
        """
        try:
            # 取得所有非零實際倉位（幣安端）
            positions = retry_api(self.client.futures_position_information)
            live_symbols: set[str] = set()
            for pos in positions:
                if float(pos["positionAmt"]) != 0:
                    live_symbols.add(pos["symbol"])

            # 取得所有有掛單的 symbol
            open_orders = retry_api(self.client.futures_get_open_orders)
            order_symbols: set[str] = {o["symbol"] for o in open_orders}

            # 孤兒 = 有掛單 但 幣安無持倉
            orphan_symbols = order_symbols - live_symbols
            if not orphan_symbols:
                return

            log.info(f"發現孤兒掛單 symbols：{orphan_symbols}，開始清理")
            for symbol in orphan_symbols:
                try:
                    self.client.futures_cancel_all_open_orders(symbol=symbol)
                    log.info(f"[{symbol}] 孤兒掛單已撤銷")
                except Exception as e:
                    log.warning(f"[{symbol}] 撤孤兒掛單失敗: {e}")
        except Exception as e:
            log.warning(f"孤兒掛單清理失敗: {e}")

    def sync(self):
        """主同步邏輯：比對 DB 和幣安實際倉位"""
        self._sync_count += 1
        # 每 10 次 sync（約 5 分鐘）執行一次孤兒清理，作為撤單保底
        if self._sync_count % 10 == 1:
            self.cleanup_orphan_orders()

        open_trades = self.db.get_open_trades()
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
            symbol    = trade["symbol"]
            trade_id  = trade["id"]
            direction = trade["direction"]
            entry     = trade["entry"]
            qty       = trade["qty"]
            qty_closed = trade["qty_closed"]

            # 幣安端實際持倉量
            actual_amt = position_map.get(symbol, 0)
            # LONG = positionAmt > 0，SHORT = positionAmt < 0
            # 必須檢查方向是否符合，避免反向倉位被誤判為「完全平倉」
            if direction == "LONG":
                # LONG 倉不應計入 SHORT 的負數部分
                actual_qty = actual_amt if actual_amt > 0 else 0
            else:
                # SHORT 倉不應計入 LONG 的正數部分
                actual_qty = abs(actual_amt) if actual_amt < 0 else 0

            expected_remaining = qty - qty_closed

            # ── 情況 1: 完全平倉 ────────────────────────────────
            if actual_qty <= 0 or abs(actual_qty) < 0.0001:
                log.info(f"[{symbol}] #{trade_id} 偵測到完全平倉")
                exit_price = self._get_last_trade_price(symbol)
                fee = self._get_recent_fee(symbol, qty)

                # 平倉後撤銷幣安上剩餘的 TP/SL 掛單（帶重試，避免殘留）
                try:
                    retry_api(self.client.futures_cancel_all_open_orders,
                              symbol=symbol)
                    log.info(f"[{symbol}] 平倉後已撤銷全部掛單")
                except Exception as _e:
                    log.warning(f"[{symbol}] 撤銷剩餘掛單失敗: {_e}")

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
                    self.executor.move_to_breakeven(
                        symbol      = symbol,
                        trade_id    = trade_id,
                        entry_price = entry,
                        direction   = direction,
                    )

                # TP1 後自動啟用追蹤止盈（所有策略通用）
                if Config.TRAILING_ACTIVATE_AFTER_TP1 and \
                        not trade.get("use_trailing"):
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

            # ── 情況 3: 倉位不變 → 檢查是否需要推進追蹤止盈 ─────
            if trade.get("use_trailing"):
                self._update_trailing(trade)

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

        if direction == "LONG":
            peak = latest.get("highest_price") or price
            new_sl = peak - trail_dist
            # 止損只能往上推，且不低於入場價
            if new_sl > entry and (sl is None or new_sl > sl):
                self.executor.move_trailing_sl(
                    symbol, trade_id, new_sl, direction
                )
                log.debug(
                    f"[{symbol}] LONG trailing: peak={peak:.4f} "
                    f"new_sl={new_sl:.4f} (dist={trail_dist:.4f})"
                )
        else:
            trough = latest.get("lowest_price") or price
            new_sl = trough + trail_dist
            if new_sl < entry and (sl is None or new_sl < sl):
                self.executor.move_trailing_sl(
                    symbol, trade_id, new_sl, direction
                )
                log.debug(
                    f"[{symbol}] SHORT trailing: trough={trough:.4f} "
                    f"new_sl={new_sl:.4f} (dist={trail_dist:.4f})"
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
        已走過 partial（TP1）的單，完全平倉時加上 TP1+ 前綴方便辨識。

        注意：move_to_breakeven 後 DB 的 sl 欄位已更新為保本價，
        因此必須在 SL 比對之前先判斷 BREAKEVEN，否則會被誤判為 SL。
        """
        if not exit_price or exit_price <= 0:
            return "UNKNOWN"

        direction   = trade["direction"]
        tp1         = trade.get("tp1") or 0
        tp2         = trade.get("tp2") or 0
        sl          = trade.get("sl") or 0
        entry       = trade.get("entry") or 0
        was_partial = trade.get("status") == "partial"
        is_be       = trade.get("breakeven", False)

        # 容差：價格的 0.3%（避免滑價造成誤判）
        tol = entry * 0.003 if entry > 0 else 0

        # ── 1. BREAKEVEN 先判斷（SL 已移至 BE 價，必須優先於 SL 比對）──
        if is_be and sl > 0 and abs(exit_price - sl) <= tol:
            # TP1 一定已觸發才會有 breakeven，固定前綴 TP1+
            return "TP1+BE"

        # ── 2. TP2（最優先）──────────────────────────────────────
        if direction == "LONG":
            if tp2 > 0 and exit_price >= tp2 - tol:
                return "TP2"
            # TP1 全倉（未曾 partial）
            if tp1 > 0 and not was_partial and exit_price >= tp1 - tol:
                return "TP1"
            # SL
            if sl > 0 and exit_price <= sl + tol:
                return "TP1+SL" if was_partial else "SL"
        else:  # SHORT
            if tp2 > 0 and exit_price <= tp2 + tol:
                return "TP2"
            if tp1 > 0 and not was_partial and exit_price <= tp1 + tol:
                return "TP1"
            if sl > 0 and exit_price >= sl - tol:
                return "TP1+SL" if was_partial else "SL"

        # ── 3. 追蹤止盈 ───────────────────────────────────────────
        if trade.get("use_trailing"):
            return "TP1+TRAILING" if was_partial else "TRAILING"

        return "UNKNOWN"

    # ── 輔助方法 ─────────────────────────────────────────────────

    def _get_last_trade_price(self, symbol: str) -> Optional[float]:
        """取得最近一筆成交價格"""
        try:
            trades = self.client.futures_account_trades(
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
            trades = self.client.futures_account_trades(
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
