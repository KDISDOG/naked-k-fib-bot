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
from datetime import datetime
from typing import Optional
import pandas_ta as ta
from binance.client import Client
from config import Config
from risk_manager import RiskManager
from api_retry import retry_api
from binance_orders import list_open_orders, cancel_all_for_symbol
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
        # P12D：post-close callback（bot_main 註冊；用於 strategy.on_position_close）
        # signature: (strategy_name: str, symbol: str, close_reason: str, close_time)
        self._post_close_callback = None
        # 啟動時立刻掃一次孤兒單（清掉重啟前累積的殘留）
        try:
            self._sweep_orphan_orders()
        except Exception as e:
            log.error(f"啟動時孤兒單掃蕩失敗: {e}")

    def set_post_close_callback(self, callback) -> None:
        """P12D：bot_main 註冊 strategy.on_position_close dispatcher。
        callback signature: (strategy_name, symbol, close_reason, close_time) -> None
        callback 失敗不阻塞 sync 主流程（caller 自己 try/except）。
        """
        self._post_close_callback = callback

    def _fire_close_hook(self, strategy_name: str, symbol: str,
                          close_reason: str, close_time=None) -> None:
        """P12D：fire post-close callback if registered。"""
        if self._post_close_callback is None:
            return
        if close_time is None:
            from datetime import datetime
            close_time = datetime.now()
        try:
            self._post_close_callback(strategy_name, symbol,
                                       close_reason or "", close_time)
        except Exception as e:
            log.error(f"[post_close hook] {strategy_name}/{symbol} failed: {e}")

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

                # ── Dust 偵測 ───────────────────────────────────────
                # trailing/TP 平倉後殘留 qty 因幣安 stepSize 截斷常清不掉
                # （例：VVVUSDT trailing 後剩 0.01 = 最小單位本身），DB 會
                # 卡在 partial 狀態無法收尾。若殘留名目 < DUST_CLOSE_NOTIONAL
                # 視為實質已平，best-effort 市價砍掉再走完全平倉流程。
                dust_notional_thr = float(
                    getattr(Config, "DUST_CLOSE_NOTIONAL", 1.0)
                )
                is_dust = (
                    actual_qty > 0
                    and qty_closed > 0
                    and entry > 0
                    and actual_qty * entry < dust_notional_thr
                )

                # ── 情況 1: 完全平倉（含 dust 視為平倉）─────────────
                if actual_qty <= 0 or abs(actual_qty) < 0.0001 or is_dust:
                    if is_dust:
                        log.info(
                            f"[{symbol}] #{trade_id} 偵測 dust 殘留 "
                            f"qty={actual_qty} × entry={entry:.6f} "
                            f"= {actual_qty * entry:.4f} USDT "
                            f"< {dust_notional_thr}，視為完全平倉"
                        )
                        # best-effort 市價砍掉 dust（失敗也繼續寫 closed）
                        try:
                            from binance.enums import (
                                ORDER_TYPE_MARKET, SIDE_BUY, SIDE_SELL
                            )
                            close_side = SIDE_SELL if direction == "LONG" else SIDE_BUY
                            self.client.futures_create_order(
                                symbol=symbol, side=close_side,
                                type=ORDER_TYPE_MARKET,
                                quantity=actual_qty, reduceOnly=True,
                            )
                            log.info(f"[{symbol}] dust 市價平倉送出")
                        except Exception as de:
                            log.debug(
                                f"[{symbol}] dust 市價平倉失敗（仍標 closed）: {de}"
                            )
                    else:
                        log.info(f"[{symbol}] #{trade_id} 偵測到完全平倉")

                    # 先清殘留掛單（含 Algo，reduceOnly TP/SL 不會自動取消）
                    # 放最前面：即使後續 close_trade/notify 丟例外，
                    # 孤兒單已經被清掉
                    try:
                        cancel_all_for_symbol(self.client, symbol)
                        log.info(f"[{symbol}] 已清除平倉後殘留掛單")
                    except Exception as ce:
                        log.warning(f"[{symbol}] 清除殘留掛單失敗: {ce}")

                    # 優先取 Binance 實際平倉 fills：
                    #   exit_price = 加權均價（不是 DB 的 tp1/tp2）
                    #   fee        = 真實平倉手續費
                    #   realized   = 真實 realizedPnl（判定勝負最準）
                    start_ms = self._opened_at_ms(trade)
                    close_info = self._get_recent_close_fills(
                        symbol, target_qty=expected_remaining, start_ms=start_ms
                    )
                    if close_info.get("vwap"):
                        exit_price = close_info["vwap"]
                        fee = close_info.get("fee", 0.0)
                        realized_hint = close_info.get("realized")
                        log.info(
                            f"[{symbol}] #{trade_id} 平倉 VWAP={exit_price:.6f} "
                            f"realized={realized_hint:+.4f} fee={fee:.4f}"
                        )
                    else:
                        # fallback：取不到 fills 時退回舊邏輯
                        exit_price = self._get_last_trade_price(symbol) or entry
                        fee = self._get_recent_fee(symbol, qty)
                        realized_hint = None

                    # 判斷平倉原因（優先用 realized 正負號）
                    reason = self._detect_close_reason(
                        trade, exit_price, realized_hint=realized_hint
                    )

                    self.db.close_trade(
                        trade_id     = trade_id,
                        exit_price   = exit_price,
                        fee          = fee,
                        partial      = False,
                        close_reason = reason,
                    )
                    self._fire_close_hook(
                        trade.get("strategy", ""), symbol, reason,
                    )
                    # 通知淨盈虧：取 DB 累加後的 net_pnl（涵蓋 TP1/TP2/殘餘
                    # 所有 legs）。舊版用 realized_hint 只反映「最後一腿」，
                    # 導致 TP1+TP2+trailing 多段平倉時 Telegram 只顯示最後
                    # 一段的微小淨盈虧（SPORTFUNUSDT #69 = +0.01 而非 +1.23）
                    try:
                        refreshed = self.db.get_trade_by_id(trade_id)
                        _net = refreshed.get("net_pnl", 0.0) if refreshed else 0.0
                    except Exception:
                        # fallback：舊邏輯（單腿交易近似）
                        if realized_hint is not None:
                            _net = realized_hint - fee
                        else:
                            if direction == "LONG":
                                _raw_pnl = (exit_price - entry) * qty
                            else:
                                _raw_pnl = (entry - exit_price) * qty
                            _net = _raw_pnl - fee
                    notify.trade_closed(symbol, direction, _net, reason)
                    continue

                # ── 情況 2: 部分平倉 — 判斷 TP1 / TP2 ─────────────────
                qty_diff = expected_remaining - actual_qty
                if qty_diff > 0.0005:
                    # 判斷觸發的是 TP1 還是 TP2：查當前 Binance 掛單
                    #   若 tp2_order_id 仍在掛 → TP1 先觸發（正常流程）
                    #   若 tp2_order_id 不在了 → TP2 剛觸發（TP1 可能已失敗未掛）
                    #                             剩餘部位應直接平倉，而非移到保本
                    tp2_id = str(trade.get("tp2_order_id") or "")
                    tp1_id = str(trade.get("tp1_order_id") or "")
                    tp2_alive = False
                    tp1_alive = False
                    try:
                        open_o = list_open_orders(self.client, symbol=symbol)
                        alive_ids = {str(o.get("orderId")) for o in open_o}
                        if tp2_id and tp2_id in alive_ids:
                            tp2_alive = True
                        if tp1_id and tp1_id in alive_ids:
                            tp1_alive = True
                    except Exception as oe:
                        log.warning(f"[{symbol}] 查掛單判定 TP 失敗: {oe}")

                    # 若 tp1 也已不在、tp2 也已不在 → TP2 觸發（或兩張都沒掛成）
                    # 若 tp2 仍在 → 一定是 TP1 觸發
                    is_tp2_fired = (not tp2_alive) and (tp2_id != "")
                    # 保守：沒有 tp2_order_id 記錄且 tp1_order_id 也沒 → 無法判定，
                    # 仍當成 TP1 處理（維持舊行為）

                    log.info(
                        f"[{symbol}] #{trade_id} 偵測到部分平倉："
                        f"預期剩餘={expected_remaining:.4f} "
                        f"實際={actual_qty:.4f} 差異={qty_diff:.4f} "
                        f"(tp1_alive={tp1_alive} tp2_alive={tp2_alive} "
                        f"→ {'TP2 觸發' if is_tp2_fired else 'TP1 觸發'})"
                    )

                    if is_tp2_fired:
                        # TP2 「疑似」觸發：優先用 Binance 真實成交 VWAP，
                        # 而非 DB 的 trade["tp2"]（可能與實際成交有偏差）
                        start_ms = self._opened_at_ms(trade)
                        tp2_info = self._get_recent_close_fills(
                            symbol, target_qty=qty_diff, start_ms=start_ms
                        )
                        if tp2_info.get("vwap"):
                            exit_price = tp2_info["vwap"]
                            fee = tp2_info.get("fee", 0.0)
                            realized_hint = tp2_info.get("realized")
                        else:
                            exit_price = trade["tp2"] or \
                                self._get_last_trade_price(symbol) or entry
                            fee = RiskManager.estimate_fee(qty_diff, exit_price)
                            realized_hint = None

                        # ── 防呆：驗證 exit_price 真的接近 TP2 目標 ──
                        # 若 tp2 order 因故不在掛單簿（被取消/替換/API 失敗誤判），
                        # 但實際成交價根本沒到 TP2 → 這其實是 TP1 觸發，
                        # 不能硬寫 "TP2" 也不能市價砍掉殘倉。
                        # 改走 TP1 流程（移保本 + 啟用 trailing）。
                        # （MAVUSDT #49 +0.55% 被誤標 TP2 的根因修正）
                        tp2_target = trade.get("tp2") or 0
                        entry_px   = trade.get("entry") or 0
                        tp2_tol = entry_px * 0.01 if entry_px > 0 else 0
                        tp2_price_ok = False
                        if tp2_target > 0 and exit_price > 0:
                            if direction == "LONG":
                                tp2_price_ok = exit_price >= tp2_target - tp2_tol
                            else:
                                tp2_price_ok = exit_price <= tp2_target + tp2_tol

                        if not tp2_price_ok:
                            # 若此筆已經 TP1 過（prev close_reason 存在），現在又
                            # 發生部分平倉且成交價未達 TP2 → 最可能是 trailing SL
                            # 或 BE SL 觸發收尾，不是「TP1 再觸發一次」。
                            # （MAGMAUSDT #72 被誤標 TP1 的根因修正）
                            prev_reason = (trade.get("close_reason") or "").upper()
                            already_past_tp1 = (
                                prev_reason.startswith("TP1")
                                or prev_reason.startswith("TIMEOUT")
                                or prev_reason == "PARTIAL_TIMEOUT"
                            )

                            if already_past_tp1:
                                # TP1 之後的部分平倉：判斷是 trailing 還是 SL
                                if realized_hint is not None and realized_hint < 0:
                                    post_reason = "SL"
                                elif trade.get("use_trailing"):
                                    post_reason = "TRAILING"
                                else:
                                    post_reason = "TP1+BE" if trade.get("breakeven") else "SL"
                                log.warning(
                                    f"[{symbol}] #{trade_id} 已過 TP1，本次部分平倉"
                                    f"成交價 {exit_price:.6f} 未達 TP2 "
                                    f"{tp2_target:.6f} → 視為 {post_reason}"
                                )
                                self.db.close_trade(
                                    trade_id     = trade_id,
                                    exit_price   = exit_price,
                                    fee          = fee,
                                    partial      = True,
                                    closed_qty   = qty_diff,
                                    close_reason = post_reason,
                                )
                                # 不重做 move_to_breakeven / enable_trailing
                                # （已經設過；重做只會徒增 API 呼叫）
                                continue

                            # 首次部分平倉（未過 TP1）：走原本 TP1 流程
                            log.warning(
                                f"[{symbol}] #{trade_id} tp2_order 已不在掛單簿但"
                                f"成交價 {exit_price:.6f} 未達 TP2 目標 "
                                f"{tp2_target:.6f} → 視為 TP1 觸發，走 TP1 流程"
                            )
                            if realized_hint is not None and realized_hint < 0:
                                tp1_reason = "TP1+BE" if trade.get("breakeven") else "SL"
                            else:
                                tp1_reason = "TP1"

                            self.db.close_trade(
                                trade_id     = trade_id,
                                exit_price   = exit_price,
                                fee          = fee,
                                partial      = True,
                                closed_qty   = qty_diff,
                                close_reason = tp1_reason,
                            )

                            # 走 TP1 後置流程：移保本 + 啟用 trailing
                            if not trade["breakeven"]:
                                try:
                                    self.executor.move_to_breakeven(
                                        symbol      = symbol,
                                        trade_id    = trade_id,
                                        entry_price = entry,
                                        direction   = direction,
                                    )
                                except Exception as be:
                                    log.error(
                                        f"[{symbol}] #{trade_id} move_to_breakeven 失敗: {be}"
                                    )

                            if Config.TRAILING_ENABLED and \
                                    Config.TRAILING_ACTIVATE_AFTER_TP1 and \
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
                                    log.error(
                                        f"[{symbol}] #{trade_id} 啟用追蹤止盈失敗: {te}"
                                    )
                            continue

                        # 真正的 TP2：寫 DB + 砍殘倉
                        self.db.close_trade(
                            trade_id     = trade_id,
                            exit_price   = exit_price,
                            fee          = fee,
                            partial      = True,
                            closed_qty   = qty_diff,
                            close_reason = "TP2",
                        )
                        # 把剩餘 actual_qty 市價平掉，避免裸倉
                        try:
                            from binance.enums import (
                                ORDER_TYPE_MARKET, SIDE_BUY, SIDE_SELL
                            )
                            close_side = SIDE_SELL if direction == "LONG" else SIDE_BUY
                            self.client.futures_create_order(
                                symbol     = symbol,
                                side       = close_side,
                                type       = ORDER_TYPE_MARKET,
                                quantity   = actual_qty,
                                reduceOnly = True,
                            )
                            log.info(
                                f"[{symbol}] TP2 後剩餘 {actual_qty} 單市價平掉"
                            )
                        except Exception as me:
                            log.error(f"[{symbol}] TP2 後殘倉平倉失敗: {me}")
                            notify.error("TP2 後殘倉平倉失敗", f"{symbol}: {me}")
                        # 清掉剩餘 reduceOnly 掛單
                        try:
                            cancel_all_for_symbol(self.client, symbol)
                        except Exception:
                            pass
                        continue

                    # 走到這裡 = TP1 觸發（或無法判定、保守走 TP1 路徑）
                    # 優先用 Binance 真實成交 VWAP 當 exit_price，
                    # 而非 DB 的 trade["tp1"] — 這是 AAVE #2 這類案例的根源修正
                    start_ms = self._opened_at_ms(trade)
                    tp1_info = self._get_recent_close_fills(
                        symbol, target_qty=qty_diff, start_ms=start_ms
                    )
                    if tp1_info.get("vwap"):
                        exit_price = tp1_info["vwap"]
                        fee = tp1_info.get("fee", 0.0)
                        realized_hint = tp1_info.get("realized")
                        # partial 即使 realized < 0（例如 SL 提早打到）也要誠實記錄
                        # 避免把虧損 tick 寫成 TP1 獲利
                        if realized_hint is not None and realized_hint < 0:
                            # 這種情況其實不是 TP1，是 SL 或 BE 先打到
                            # 讓 reason 反映事實
                            if trade.get("breakeven"):
                                tp1_reason = "TP1+BE"  # 之前已 TP1，現在 BE 補刀
                            else:
                                tp1_reason = "SL"
                            log.warning(
                                f"[{symbol}] #{trade_id} 「部分減倉」realized={realized_hint:+.4f} "
                                f"< 0 → 更正為 {tp1_reason}（非 TP1）"
                            )
                        else:
                            tp1_reason = "TP1"
                    else:
                        exit_price = trade["tp1"] or \
                            self._get_last_trade_price(symbol) or entry
                        fee = RiskManager.estimate_fee(qty_diff, exit_price)
                        tp1_reason = "TP1"

                    self.db.close_trade(
                        trade_id     = trade_id,
                        exit_price   = exit_price,
                        fee          = fee,
                        partial      = True,
                        closed_qty   = qty_diff,
                        close_reason = tp1_reason,
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

                    # TP1 後自動啟用追蹤止盈（需總開關開啟）
                    if Config.TRAILING_ENABLED and \
                            Config.TRAILING_ACTIVATE_AFTER_TP1 and \
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

                # ── 情況 3: 仍有持倉 → 更新 MAE/MFE + 檢查追蹤止盈 ─────
                # 先更新 MAE/MFE（所有 open/partial 都要追蹤，不限 use_trailing）
                # 這是策略成效分析的核心資料
                try:
                    ticker = retry_api(
                        self.client.futures_symbol_ticker, symbol=symbol
                    )
                    cur_price = float(ticker["price"])
                    self.db.update_excursion(trade_id, cur_price)
                except Exception as ee:
                    log.debug(
                        f"[{symbol}] #{trade_id} excursion 更新失敗（略過）: {ee}"
                    )

                # 總開關關閉時直接略過，即使 DB 既有紀錄 use_trailing=True
                if Config.TRAILING_ENABLED and trade.get("use_trailing"):
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
        且幣安實際持倉為 0 的 symbol，全部取消（含 Algo Conditional）。

        產生孤兒的情境：
          - TP2 觸發全平後，殘留的 reduceOnly SL / TP1 不會自動取消
          - 歷史上因重複下單產生的冗餘訂單
          - 手動在幣安 APP 平倉後未同步
        """
        try:
            all_open = list_open_orders(self.client)
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
            # 孤兒 symbol：無持倉 + DB 無紀錄 → 全清（含 Algo）
            cancel_all_for_symbol(self.client, sym)
            cleaned_total += cnt
            log.warning(f"[{sym}] 孤兒單掃蕩：取消 {cnt} 筆")

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
    def _detect_close_reason(
        self,
        trade: dict,
        exit_price: float,
        realized_hint: Optional[float] = None,
    ) -> str:
        """
        根據平倉價格 + realizedPnl hint，推斷平倉原因。

        判定優先順序：
          1. BREAKEVEN（SL 已移至 entry 附近；可能 realized 略負於手續費）
          2. 若有 realized_hint：
             - realized > 0  → 找最近的獲利目標 (TP2 / TP1 / TRAILING / WIN)
             - realized < 0 → 優先判 SL（對不上則 EXT_LOSS）
          3. 無 realized_hint：退回舊邏輯（純價格比對）

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

        # ── partial 前綴：依前一次的 close_reason 決定 ──
        # 先前是 TP1   → "TP1+"（最常見）
        # 先前是 PARTIAL_TIMEOUT → "TIMEOUT+"（半時砍半倉）
        # 其他已知來源 → 直接用原 reason 當前綴，誠實標註
        def _partial_prefix() -> str:
            if not was_partial:
                return ""
            prev = (trade.get("close_reason") or "").upper()
            if not prev or prev == "TP1":
                return "TP1+"
            if prev == "PARTIAL_TIMEOUT":
                return "TIMEOUT+"
            # 其他（例如未來新增的 partial 原因）原樣保留
            return f"{prev}+"

        pfx = _partial_prefix()

        # 容差：價格的 1.0%（滑價保護）
        tol = entry * 0.01 if entry > 0 else 0

        # ── 1. BREAKEVEN 最優先 ───────────────────────────────────
        if is_be and sl > 0 and abs(exit_price - sl) <= tol:
            return f"{pfx}BE" if was_partial else "BE"

        # ── 2. 有 realized hint：按勝負分流 ───────────────────────
        if realized_hint is not None:
            if realized_hint > 0:
                # 獲利分支：找最近的正向目標
                if direction == "LONG":
                    if tp2 > 0 and exit_price >= tp2 - tol:
                        return "TP2"
                    if tp1 > 0 and not was_partial and exit_price >= tp1 - tol:
                        return "TP1"
                else:
                    if tp2 > 0 and exit_price <= tp2 + tol:
                        return "TP2"
                    if tp1 > 0 and not was_partial and exit_price <= tp1 + tol:
                        return "TP1"
                if trade.get("use_trailing"):
                    return f"{pfx}TRAILING" if was_partial else "TRAILING"
                # 賺但對不上明確 TP（例如手動停單獲利、trailing 未標記）
                return f"{pfx}WIN" if was_partial else "WIN"
            else:
                # 虧損分支：優先判 SL
                if direction == "LONG":
                    if sl > 0 and exit_price <= sl + tol:
                        return f"{pfx}SL" if was_partial else "SL"
                else:
                    if sl > 0 and exit_price >= sl - tol:
                        return f"{pfx}SL" if was_partial else "SL"
                if trade.get("use_trailing"):
                    return f"{pfx}TRAILING" if was_partial else "TRAILING"
                log.warning(
                    f"[{trade.get('symbol')}] #{trade.get('id')} "
                    f"虧損但對不上 SL: dir={direction} exit={exit_price:.6f} "
                    f"entry={entry:.6f} sl={sl} realized={realized_hint:+.4f}"
                )
                return f"{pfx}EXT_LOSS" if was_partial else "EXT_LOSS"

        # ── 3. 無 realized hint：退回舊邏輯（純價格比對） ─────────
        if direction == "LONG":
            if tp2 > 0 and exit_price >= tp2 - tol:
                return "TP2"
            if tp1 > 0 and not was_partial and exit_price >= tp1 - tol:
                return "TP1"
            if sl > 0 and exit_price <= sl + tol:
                return f"{pfx}SL" if was_partial else "SL"
        else:  # SHORT
            if tp2 > 0 and exit_price <= tp2 + tol:
                return "TP2"
            if tp1 > 0 and not was_partial and exit_price <= tp1 + tol:
                return "TP1"
            if sl > 0 and exit_price >= sl - tol:
                return f"{pfx}SL" if was_partial else "SL"

        if trade.get("use_trailing"):
            return f"{pfx}TRAILING" if was_partial else "TRAILING"

        # Fallback：對不上任何結構化目標（無 realized hint 可用）
        log.warning(
            f"[{trade.get('symbol')}] #{trade.get('id')} 平倉原因無法對應："
            f"dir={direction} exit={exit_price} entry={entry} "
            f"tp1={tp1} tp2={tp2} sl={sl} be={is_be} partial={was_partial}"
        )
        if entry > 0:
            if direction == "LONG":
                win = exit_price > entry
            else:
                win = exit_price < entry
            base = "EXT_WIN" if win else "EXT_LOSS"
            return f"{pfx}{base}" if was_partial else base

        return "UNKNOWN"

    # ── 平倉成交 VWAP / realizedPnl 取得 ─────────────────────────
    @staticmethod
    def _opened_at_ms(trade: dict) -> Optional[int]:
        """把 trade.opened_at (ISO) 轉成 epoch ms；用於過濾 Binance fills"""
        opened_at = trade.get("opened_at")
        if not opened_at:
            return None
        try:
            dt = datetime.fromisoformat(opened_at)
            return int(dt.timestamp() * 1000)
        except Exception:
            return None

    def _get_recent_close_fills(
        self,
        symbol: str,
        target_qty: Optional[float] = None,
        start_ms: Optional[int] = None,
    ) -> dict:
        """
        抓最近的「平倉」成交（realizedPnl != 0）並聚合：
          {
            "vwap":       加權均價,
            "total_qty":  累積成交量,
            "realized":   總 realizedPnl,
            "fee":        總 commission（正值）,
            "ts_latest":  最新成交時間 (ms),
          }

        若 target_qty 給定，由新到舊累積到達 target_qty 為止；
        若 start_ms 給定，只取該時間後的成交（避免誤抓舊的 fills）。
        拿不到或無平倉成交時回傳 {}。
        """
        try:
            params = {"symbol": symbol, "limit": 50}
            if start_ms:
                params["startTime"] = start_ms
            trades = retry_api(
                self.client.futures_account_trades, **params
            )
        except Exception as e:
            log.warning(f"取得 {symbol} 平倉成交失敗: {e}")
            return {}

        if not trades:
            return {}

        # 只要 realizedPnl 非 0（表示這筆是「平倉」方向的成交）
        closing = [
            t for t in trades
            if abs(float(t.get("realizedPnl", 0) or 0)) > 1e-12
        ]
        if not closing:
            return {}
        # 由新到舊排序
        closing.sort(key=lambda x: int(x.get("time", 0)), reverse=True)

        tot_qty = 0.0
        tot_notional = 0.0
        realized = 0.0
        fee = 0.0
        ts_latest = int(closing[0].get("time", 0))
        for tr in closing:
            q = float(tr.get("qty", 0) or 0)
            p = float(tr.get("price", 0) or 0)
            if q <= 0 or p <= 0:
                continue
            tot_qty += q
            tot_notional += q * p
            realized += float(tr.get("realizedPnl", 0) or 0)
            fee += float(tr.get("commission", 0) or 0)
            if target_qty is not None and tot_qty >= target_qty - 1e-9:
                break

        if tot_qty <= 0:
            return {}
        vwap = tot_notional / tot_qty
        return {
            "vwap":       vwap,
            "total_qty":  tot_qty,
            "realized":   realized,
            "fee":        fee,
            "ts_latest":  ts_latest,
        }

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
