"""
Order Executor v2 — 幣安合約下單、撤單、平倉

改進：
  1. 分批止盈下單（TP1 平 50%，TP2 平剩餘）
  2. Breakeven stop：修改止損至入場價
  3. 改善緊急撤單：逐幣種取消
  4. 精度處理：根據幣安 symbol info 調整數量精度
"""
import logging
import math
from typing import Optional
from binance.client import Client
from binance.enums import *
from api_retry import retry_api, create_order_safe
from binance_orders import (
    list_open_orders, cancel_order, cancel_all_for_symbol, extract_id
)
from notifier import notify

log = logging.getLogger("executor")


class OrderExecutor:
    def __init__(self, client: Client, db):
        self.client = client
        self.db     = db
        self._symbol_info_cache: dict = {}

    # ── 精度處理 ─────────────────────────────────────────────────

    def _get_symbol_info(self, symbol: str) -> dict:
        """取得幣種的精度資訊（快取）"""
        if symbol in self._symbol_info_cache:
            return self._symbol_info_cache[symbol]

        try:
            info = retry_api(self.client.futures_exchange_info)
            for s in info["symbols"]:
                if s["symbol"] == symbol:
                    # 找數量精度和價格精度
                    qty_precision = s.get("quantityPrecision", 3)
                    price_precision = s.get("pricePrecision", 2)

                    # 找最小數量 (LOT_SIZE filter)
                    min_qty = 0.001
                    step_size = 0.001
                    for f in s.get("filters", []):
                        if f["filterType"] == "LOT_SIZE":
                            min_qty = float(f["minQty"])
                            step_size = float(f["stepSize"])
                            break

                    result = {
                        "qty_precision": qty_precision,
                        "price_precision": price_precision,
                        "min_qty": min_qty,
                        "step_size": step_size,
                    }
                    self._symbol_info_cache[symbol] = result
                    return result
        except Exception as e:
            log.warning(f"取得 {symbol} 精度資訊失敗: {e}")

        return {
            "qty_precision": 3,
            "price_precision": 2,
            "min_qty": 0.001,
            "step_size": 0.001,
        }

    def _round_qty(self, symbol: str, qty: float) -> float:
        """按幣種精度四捨五入數量"""
        info = self._get_symbol_info(symbol)
        step = info["step_size"]
        if step <= 0:
            return round(qty, info["qty_precision"])
        precision = int(round(-math.log10(step)))
        rounded = math.floor(qty * (10 ** precision)) / (10 ** precision)
        return max(rounded, info["min_qty"])

    def _round_price(self, symbol: str, price: float) -> float:
        info = self._get_symbol_info(symbol)
        return round(price, info["price_precision"])

    # ── 開倉（分批止盈）────────────────────────────────────────

    def open_position(
        self,
        symbol:    str,
        direction: str,         # "LONG" / "SHORT"
        qty:       float,
        qty_tp1:   float,       # TP1 平倉數量
        qty_tp2:   float,       # TP2 平倉數量
        entry:     float,
        sl:        float,
        tp1:       float,
        tp2:       float,
        leverage:  int = 3,
        meta:      dict = None,
        use_trailing: bool = False,
        trailing_atr: float = 0.0,
        btc_corr:    float = 0.0,
        strategy:    str = "naked_k_fib",
    ) -> Optional[dict]:
        """
        開合約倉位並設置：
        - 1 張止損單（全倉 closePosition）
        - 1 張 TP1 止盈（qty_tp1）
        - 1 張 TP2 止盈（qty_tp2）
        """
        # 精度處理
        qty     = self._round_qty(symbol, qty)
        qty_tp1 = self._round_qty(symbol, qty_tp1)
        qty_tp2 = self._round_qty(symbol, qty_tp2)
        sl      = self._round_price(symbol, sl)
        tp1     = self._round_price(symbol, tp1)
        tp2     = self._round_price(symbol, tp2)

        try:
            # 1. 設定槓桿
            retry_api(
                self.client.futures_change_leverage,
                symbol=symbol, leverage=leverage
            )

            # 2. 確保單向持倉模式
            try:
                self.client.futures_change_position_mode(
                    dualSidePosition=False
                )
            except Exception:
                pass

            side       = SIDE_BUY if direction == "LONG" else SIDE_SELL
            close_side = SIDE_SELL if direction == "LONG" else SIDE_BUY

            # 3. 市價開倉（冪等下單，避免網路超時時重複開倉）
            order = create_order_safe(
                self.client, symbol,
                side     = side,
                type     = ORDER_TYPE_MARKET,
                quantity = qty,
            )
            order_id   = str(order.get("orderId", ""))
            # avgPrice 在建立當下可能回傳 "0"（Testnet 常見），多層 fallback
            fill_price = float(order.get("avgPrice") or 0)
            if fill_price <= 0 and order_id:
                try:
                    filled = self.client.futures_get_order(
                        symbol=symbol, orderId=order_id
                    )
                    fill_price = float(filled.get("avgPrice") or 0)
                except Exception as e:
                    log.warning(f"查詢成交價失敗: {e}")
            # 第三層：從成交紀錄取實際成交均價
            if fill_price <= 0:
                try:
                    recent = self.client.futures_account_trades(
                        symbol=symbol, limit=20
                    )
                    # 篩出本次開倉方向的成交
                    my_side = "BUY" if direction == "LONG" else "SELL"
                    fills = [t for t in recent if t.get("side") == my_side]
                    if fills:
                        # 用最近幾筆的加權平均價
                        total_qty = sum(float(f["qty"]) for f in fills[-20:])
                        total_val = sum(float(f["price"]) * float(f["qty"])
                                        for f in fills[-20:])
                        if total_qty > 0:
                            fill_price = total_val / total_qty
                except Exception as e:
                    log.warning(f"查詢成交紀錄失敗: {e}")
            if fill_price <= 0:
                fill_price = entry  # 最終 fallback
                log.warning(
                    f"[{symbol}] 無法取得實際成交價，使用訊號價 {entry}"
                )
            log.info(
                f"[{symbol}] 開倉成功：{direction} qty={qty} @ {fill_price}"
            )

            # 3.4 成交價偏離過大 → 緊急平倉止損
            # 背景：訊號到成交之間若市場急拉 / 急跌 >3%，
            #   原本 SL/TP 結構已失真（例：SHORT 訊號 87→成交 99，
            #   TP1/TP2 全數被 mark 越過 → Binance -2021 immediate trigger，
            #   留下裸倉）。遇此狀況直接平倉放棄，不入 DB。
            MAX_SLIP = 0.03   # 3%
            if fill_price > 0 and entry > 0 and \
                    abs(fill_price - entry) / entry > MAX_SLIP:
                slip_pct = (fill_price - entry) / entry * 100
                log.error(
                    f"[{symbol}] 成交價 {fill_price} 偏離訊號 {entry} "
                    f"({slip_pct:+.2f}%) 超過 {MAX_SLIP:.0%} 門檻，"
                    f"緊急平倉放棄此單"
                )
                try:
                    create_order_safe(
                        self.client, symbol,
                        side       = close_side,
                        type       = ORDER_TYPE_MARKET,
                        quantity   = qty,
                        reduceOnly = True,
                    )
                    cancel_all_for_symbol(self.client, symbol)
                except Exception as e2:
                    log.error(f"[{symbol}] 滑價緊急平倉失敗: {e2}")
                    notify.error("滑價緊急平倉失敗", f"{symbol}: {e2}")
                notify.error(
                    "成交價偏離過大，已放棄開倉",
                    f"{symbol} {direction} 訊號={entry} 成交={fill_price} "
                    f"偏離={slip_pct:+.2f}%"
                )
                return None

            # 3.5 實際成交價偏離訊號預期 → 按相同百分比距離重算 SL/TP
            # 背景：市價單在薄市可能滑價 0.3%~2%；若仍用訊號 entry 算的 SL/TP
            #   絕對值，會讓實際 RR 失真（SL 變近/遠、TP 變遠/近）。
            # 作法：保留原本 (SL/TP 相對 entry 的百分比距離)，套到 fill_price。
            # 門檻：偏離 > 0.1% 才重算（微小誤差沒必要動）。
            if fill_price > 0 and entry > 0 and \
                    abs(fill_price - entry) / entry > 0.001:
                sl_pct  = abs(entry - sl)  / entry
                tp1_pct = abs(tp1 - entry) / entry if tp1 else 0
                tp2_pct = abs(tp2 - entry) / entry if tp2 else 0
                if direction == "LONG":
                    new_sl  = fill_price * (1 - sl_pct)
                    new_tp1 = fill_price * (1 + tp1_pct) if tp1_pct else tp1
                    new_tp2 = fill_price * (1 + tp2_pct) if tp2_pct else tp2
                else:  # SHORT
                    new_sl  = fill_price * (1 + sl_pct)
                    new_tp1 = fill_price * (1 - tp1_pct) if tp1_pct else tp1
                    new_tp2 = fill_price * (1 - tp2_pct) if tp2_pct else tp2
                sl  = self._round_price(symbol, new_sl)
                tp1 = self._round_price(symbol, new_tp1)
                tp2 = self._round_price(symbol, new_tp2)
                log.info(
                    f"[{symbol}] 成交價 {fill_price} 偏離訊號 {entry} "
                    f"({(fill_price - entry) / entry * 100:+.2f}%)，"
                    f"重算 SL={sl} TP1={tp1} TP2={tp2}（保持原 RR）"
                )

            # 4. 止損單（reduceOnly + qty）— 失敗則緊急平倉
            # 注意：不用 closePosition=True，那是「Position-level TP/SL」
            # 特殊 slot，不會出現在 futures_get_open_orders，bot 無法枚舉/管理。
            # 改用 reduceOnly=True + quantity，SL 就是標準訂單，所有查詢端點
            # 都看得到，cancel/枚舉邏輯正常運作。
            # workingType=MARK_PRICE：用標記價觸發而非最後成交價，
            # 避免薄市「尾價瞬時 spike」導致 -2021 Order would immediately trigger。

            # 4.0 防禦性清理：清掉此 symbol 的殘留 reduceOnly STOP/TP 掛單
            # 幣安每 symbol STOP/TP 上限 10 張，殘留會導致 -4045
            # Reach max stop order limit → SL 掛單失敗 → 緊急平倉燒手續費。
            # 已由 has_open_position 擋住同 symbol 重複開倉，此處撈到的都是
            # 前次 trade 殘留 / bot crash 遺留的孤兒，清掉是安全的。
            self._cleanup_residual_reduce_only(symbol)

            sl_order_id = ""
            try:
                sl_order = create_order_safe(
                    self.client, symbol,
                    side        = close_side,
                    type        = FUTURE_ORDER_TYPE_STOP_MARKET,
                    stopPrice   = sl,
                    quantity    = qty,
                    reduceOnly  = True,
                    workingType = "MARK_PRICE",
                )
                sl_order_id, _ = extract_id(sl_order)
            except Exception as e:
                err_s = str(e)
                # 4.1 -4045 stop 限額重試：先 cancel_all_for_symbol 強清
                #     整個 symbol 的掛單（含 Algo），再試一次 SL。
                #     第 4.0 的清理只處理 list_open_orders 能枚舉到的，
                #     有些歷史 Algo 訂單可能枚舉不到但占用限額，需硬清。
                retried_ok = False
                if "-4045" in err_s or "max stop order" in err_s.lower():
                    log.warning(
                        f"[{symbol}] SL 掛單遇 -4045 限額，強清殘留後重試一次"
                    )
                    try:
                        cancel_all_for_symbol(self.client, symbol)
                    except Exception as ce:
                        log.warning(f"[{symbol}] cancel_all 失敗: {ce}")
                    try:
                        sl_order = create_order_safe(
                            self.client, symbol,
                            side        = close_side,
                            type        = FUTURE_ORDER_TYPE_STOP_MARKET,
                            stopPrice   = sl,
                            quantity    = qty,
                            reduceOnly  = True,
                            workingType = "MARK_PRICE",
                        )
                        sl_order_id, _ = extract_id(sl_order)
                        retried_ok = True
                        log.info(f"[{symbol}] -4045 強清後重試成功")
                    except Exception as e2:
                        log.error(f"[{symbol}] -4045 重試仍失敗: {e2}")

                if not retried_ok:
                    log.error(f"[{symbol}] SL 掛單失敗，緊急平倉: {e}")
                    notify.sl_placement_failed(symbol, direction)
                    # 緊急平倉保護資金
                    try:
                        create_order_safe(
                            self.client, symbol,
                            side       = close_side,
                            type       = ORDER_TYPE_MARKET,
                            quantity   = qty,
                            reduceOnly = True,
                        )
                        # 4.2 緊急平倉後清掉此 symbol 所有殘留掛單，
                        # 避免下次開同 symbol 又撞 -4045 燒手續費迴圈
                        try:
                            cancel_all_for_symbol(self.client, symbol)
                        except Exception as ce2:
                            log.warning(
                                f"[{symbol}] 緊急平倉後清殘失敗: {ce2}"
                            )
                    except Exception as e2:
                        log.error(f"[{symbol}] 緊急平倉也失敗: {e2}")
                        notify.error("緊急平倉失敗", f"{symbol}: {e2}")
                    return None

            # 5-6. TP 止盈（先驗證 stopPrice 相對 mark 方向安全，再下單）
            # 防 -2021 Order would immediately trigger：
            #   LONG TP (SELL close)：stopPrice 必須 > mark * (1 + buffer)
            #   SHORT TP (BUY  close)：stopPrice 必須 < mark * (1 - buffer)
            # 若不滿足則跳過，避免 3 次 retry 浪費約 6 秒，也避免寫入不存在的 id
            try:
                mark_now = float(
                    self.client.futures_mark_price(symbol=symbol)["markPrice"]
                )
            except Exception:
                mark_now = fill_price  # fallback
            BUF = 0.002  # 0.2%

            def _tp_ok(px):
                if px <= 0:
                    return False
                if direction == "LONG":
                    return px > mark_now * (1 + BUF)
                else:
                    return px < mark_now * (1 - BUF)

            # TP1
            tp1_order_id = ""
            if qty_tp1 > 0:
                if not _tp_ok(tp1):
                    log.warning(
                        f"[{symbol}] TP1={tp1} 相對 mark={mark_now} 已越過或太近，"
                        f"跳過 TP1 掛單（避免 -2021）"
                    )
                else:
                    try:
                        tp1_order = create_order_safe(
                            self.client, symbol,
                            side        = close_side,
                            type        = FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
                            stopPrice   = tp1,
                            quantity    = qty_tp1,
                            reduceOnly  = True,
                            workingType = "MARK_PRICE",
                        )
                        tp1_order_id, _ = extract_id(tp1_order)
                    except Exception as e:
                        log.warning(f"[{symbol}] TP1 掛單失敗: {e}")

            # TP2
            tp2_order_id = ""
            if qty_tp2 > 0:
                if not _tp_ok(tp2):
                    log.warning(
                        f"[{symbol}] TP2={tp2} 相對 mark={mark_now} 已越過或太近，"
                        f"跳過 TP2 掛單（避免 -2021）"
                    )
                else:
                    try:
                        tp2_order = create_order_safe(
                            self.client, symbol,
                            side        = close_side,
                            type        = FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
                            stopPrice   = tp2,
                            quantity    = qty_tp2,
                            reduceOnly  = True,
                            workingType = "MARK_PRICE",
                        )
                        tp2_order_id, _ = extract_id(tp2_order)
                    except Exception as e:
                        log.warning(f"[{symbol}] TP2 掛單失敗: {e}")

            # 7. 計算保證金（實際成交價 × 數量 / 槓桿）
            margin = round(fill_price * qty / leverage, 2)

            # 8. 寫入資料庫
            self.db.save_trade(
                symbol       = symbol,
                direction    = direction,
                entry        = fill_price,
                sl           = sl,
                tp1          = tp1,
                tp2          = tp2,
                qty          = qty,
                fib_level    = (meta or {}).get("fib_level", ""),
                pattern      = (meta or {}).get("pattern", ""),
                score        = (meta or {}).get("score", 0),
                timeframe    = (meta or {}).get("timeframe", "1h"),
                order_id     = order_id,
                sl_order_id  = sl_order_id,
                tp1_order_id = tp1_order_id,
                tp2_order_id = tp2_order_id,
                use_trailing = use_trailing,
                trailing_atr = trailing_atr,
                btc_corr     = btc_corr,
                strategy     = strategy,
                margin       = margin,
            )

            log.info(
                f"[{symbol}] 掛單完成：SL={sl} TP1={tp1}(qty={qty_tp1}) "
                f"TP2={tp2}(qty={qty_tp2})"
            )
            notify.trade_opened(
                symbol, direction, qty, fill_price, sl, tp1, strategy
            )
            return order

        except Exception as e:
            log.error(f"[{symbol}] 開倉失敗: {e}")
            return None

    # ── 清理殘留 reduceOnly 掛單（避免 -4045 限額）──────────────

    def _cleanup_residual_reduce_only(self, symbol: str) -> int:
        """
        清掉此 symbol 目前所有 reduceOnly STOP_MARKET / TAKE_PROFIT_MARKET
        掛單。呼叫時機：新倉 MARKET 成交後、SL 建立前。

        背景：幣安 USDT-M 合約每 symbol 的 STOP/TP 掛單上限 10 張。
        前次 trade 關閉後若殘留 SL/TP（TP2 觸發平倉但 SL/TP1 未自動取消，
        或 bot crash 遺留）會占用額度，導致本次 SL 掛單 -4045 失敗 →
        被迫緊急平倉 → 燒手續費迴圈。
        """
        cancelled = 0
        try:
            existing = list_open_orders(self.client, symbol=symbol)
        except Exception as e:
            log.warning(f"[{symbol}] 查殘留掛單失敗: {e}")
            return 0
        for o in existing:
            if (o.get("type") in ("STOP_MARKET", "TAKE_PROFIT_MARKET")
                    and o.get("reduceOnly")):
                if cancel_order(self.client, symbol, o):
                    cancelled += 1
        if cancelled:
            log.info(
                f"[{symbol}] 開倉前清掉殘留 reduceOnly STOP/TP 掛單 "
                f"{cancelled} 筆（防 -4045 限額）"
            )
        return cancelled

    # ── 清理既有 SL 掛單（避免重複堆疊）─────────────────────────

    def _cancel_existing_sl_orders(self, symbol: str, close_side: str) -> int:
        """
        枚舉 symbol 當前所有 STOP_MARKET（close_side 方向）掛單並全部取消。
        同時掃標準與 Algo 兩套系統 —— Binance 把 STOP_MARKET 歸到
        Algo Conditional，舊版只看 openOrders 會漏單（見 binance_orders.py）。

        防禦性清理：即使 DB 記錄的 sl_order_id 失效 / 重複，也能確保
        移動 SL 前沒有殘留 SL 掛單。
        """
        cancelled = 0
        try:
            orders = list_open_orders(self.client, symbol=symbol)
        except Exception as e:
            log.warning(f"[{symbol}] 取得掛單列表失敗: {e}")
            return 0

        for o in orders:
            if o["type"] == "STOP_MARKET" and o["side"] == close_side:
                if cancel_order(self.client, symbol, o):
                    cancelled += 1
        if cancelled:
            log.info(f"[{symbol}] 清理殘留 SL 掛單 {cancelled} 筆")
        return cancelled

    # ── Breakeven Stop（移動止損至入場價）────────────────────────

    def move_to_breakeven(self, symbol: str, trade_id: int,
                          entry_price: float, direction: str) -> bool:
        """
        TP1 觸發後，把止損移到入場價（保本）
        1. 取消所有既有 STOP_MARKET 掛單（含原 closePosition SL）
        2. 下新的止損單在入場價
        """
        try:
            trade = self.db.get_trade_by_id(trade_id)
            if not trade:
                return False

            # 冪等檢查：已經搬過保本就不要重複動作
            if trade.get("breakeven"):
                log.debug(f"[{symbol}] #{trade_id} 已 breakeven，跳過")
                return True

            close_side = SIDE_SELL if direction == "LONG" else SIDE_BUY

            # 防禦性清理：取消所有此 symbol/方向的 SL 掛單
            self._cancel_existing_sl_orders(symbol, close_side)

            # 加一點 buffer 避免滑價
            price_precision = self._get_symbol_info(symbol)["price_precision"]
            if direction == "LONG":
                new_sl = round(entry_price * 1.001, price_precision)  # 入場價 +0.1%
            else:
                new_sl = round(entry_price * 0.999, price_precision)  # 入場價 -0.1%

            # 計算剩餘倉位
            remaining = trade["qty"] - trade["qty_closed"]
            remaining = self._round_qty(symbol, remaining)

            if remaining <= 0:
                return True

            # 下新止損（冪等下單）
            new_sl_order = create_order_safe(
                self.client, symbol,
                side        = close_side,
                type        = FUTURE_ORDER_TYPE_STOP_MARKET,
                stopPrice   = new_sl,
                quantity    = remaining,
                reduceOnly  = True,
                workingType = "MARK_PRICE",
            )
            new_sl_id, _ = extract_id(new_sl_order)

            # 更新 DB
            self.db.update_breakeven(
                trade_id, new_sl=new_sl, sl_order_id=new_sl_id
            )

            log.info(
                f"[{symbol}] 止損已移至保本：{new_sl} "
                f"(入場={entry_price})"
            )
            return True

        except Exception as e:
            log.error(f"[{symbol}] 移動止損失敗: {e}")
            return False

    # ── 追蹤止盈 ─────────────────────────────────────────────────
    def move_trailing_sl(self, symbol: str, trade_id: int,
                         new_sl: float, direction: str) -> bool:
        """
        把止損推進到 new_sl（追蹤止盈用）
        只在新止損比現有止損「更保本」時才會執行
        """
        try:
            trade = self.db.get_trade_by_id(trade_id)
            if not trade:
                return False

            current_sl = trade.get("sl")
            # 安全檢查：新止損必須朝入場價推進（不能後退）
            if current_sl is not None:
                if direction == "LONG" and new_sl <= current_sl:
                    return False
                if direction == "SHORT" and new_sl >= current_sl:
                    return False

            close_side = SIDE_SELL if direction == "LONG" else SIDE_BUY
            new_sl = self._round_price(symbol, new_sl)

            # 防禦性清理：取消所有此 symbol/方向的殘留 SL 掛單
            self._cancel_existing_sl_orders(symbol, close_side)

            remaining = trade["qty"] - trade["qty_closed"]
            remaining = self._round_qty(symbol, remaining)
            if remaining <= 0:
                return True

            new_sl_order = create_order_safe(
                self.client, symbol,
                side        = close_side,
                type        = FUTURE_ORDER_TYPE_STOP_MARKET,
                stopPrice   = new_sl,
                quantity    = remaining,
                reduceOnly  = True,
                workingType = "MARK_PRICE",
            )
            new_sl_id, _ = extract_id(new_sl_order)
            self.db.update_sl(trade_id, new_sl=new_sl, sl_order_id=new_sl_id)

            log.info(f"[{symbol}] 追蹤止盈推進：SL={new_sl}")
            return True
        except Exception as e:
            log.error(f"[{symbol}] 追蹤止盈失敗: {e}")
            return False

    # ── 緊急操作 ─────────────────────────────────────────────────

    def cancel_all(self):
        """緊急撤銷所有未成交掛單（含 Algo），逐幣種取消"""
        try:
            positions = self.client.futures_position_information()
            cancelled_symbols = set()
            for pos in positions:
                symbol = pos["symbol"]
                if symbol in cancelled_symbols:
                    continue
                cancel_all_for_symbol(self.client, symbol)
                cancelled_symbols.add(symbol)
            log.warning(
                f"緊急撤單完成：已取消 {len(cancelled_symbols)} 個幣種的掛單"
            )
        except Exception as e:
            log.error(f"緊急撤單失敗: {e}")

    def close_all_positions(self):
        """緊急平倉所有倉位"""
        try:
            positions = self.client.futures_position_information()
            closed = 0
            for pos in positions:
                qty = float(pos["positionAmt"])
                if qty == 0:
                    continue
                symbol = pos["symbol"]
                side   = SIDE_SELL if qty > 0 else SIDE_BUY
                abs_qty = self._round_qty(symbol, abs(qty))
                create_order_safe(
                    self.client, symbol,
                    side       = side,
                    type       = ORDER_TYPE_MARKET,
                    quantity   = abs_qty,
                    reduceOnly = True,
                )
                closed += 1
                log.warning(f"[{symbol}] 緊急平倉：qty={abs_qty}")
            log.warning(f"緊急平倉完成：共平 {closed} 個倉位")
        except Exception as e:
            log.error(f"緊急平倉失敗: {e}")

    def close_position_market(self, symbol: str, trade_id: int,
                              close_reason: str = "MANUAL"):
        """
        對特定 symbol 執行市價平倉。
        平倉後更新資料庫狀態。
        close_reason: TIMEOUT / MANUAL / etc.
        """
        try:
            pos_info = self.client.futures_position_information(symbol=symbol)
            if not pos_info:
                return
            qty = float(pos_info[0]["positionAmt"])
            if qty == 0:
                return

            side = SIDE_SELL if qty > 0 else SIDE_BUY
            abs_qty = self._round_qty(symbol, abs(qty))

            order = create_order_safe(
                self.client, symbol,
                side       = side,
                type       = ORDER_TYPE_MARKET,
                quantity   = abs_qty,
                reduceOnly = True,
            )
            exit_price = float(order.get("avgPrice") or 0)
            if exit_price <= 0:
                try:
                    filled = self.client.futures_get_order(
                        symbol=symbol, orderId=order.get("orderId")
                    )
                    exit_price = float(filled.get("avgPrice") or 0)
                except Exception:
                    pass

            # 第三層 fallback：查最近成交紀錄
            if exit_price <= 0:
                try:
                    recent = self.client.futures_account_trades(
                        symbol=symbol, limit=5
                    )
                    if recent:
                        exit_price = float(recent[-1]["price"])
                except Exception:
                    pass

            # 最終 fallback：用 DB 中的入場價（PnL ≈ 0 好過天文數字）
            if exit_price <= 0:
                trade_rec = self.db.get_trade_by_id(trade_id)
                if trade_rec and trade_rec.get("entry"):
                    exit_price = trade_rec["entry"]
                    log.warning(
                        f"[{symbol}] 無法取得平倉價，fallback 至入場價 {exit_price}"
                    )

            # 撤銷剩餘掛單（含 Algo）
            cancel_all_for_symbol(self.client, symbol)

            self.db.close_trade(trade_id, exit_price=exit_price or 0,
                                close_reason=close_reason)
            log.warning(
                f"[{symbol}] 市價平倉（{close_reason}）：qty={abs_qty} @ {exit_price}"
            )

            # Telegram 通知（TIMEOUT/MANUAL 這類由 bot 主動發起的平倉，
            # syncer 看不到倉位變化 → 這裡自己發，避免用戶以為 bot 沒動作）
            try:
                tr = self.db.get_trade_by_id(trade_id)
                if tr:
                    entry = float(tr.get("entry") or 0)
                    direction = tr.get("direction", "")
                    qty_total = float(tr.get("qty") or 0)
                    if direction == "LONG":
                        raw_pnl = (exit_price - entry) * qty_total
                    else:
                        raw_pnl = (entry - exit_price) * qty_total
                    net = raw_pnl - float(tr.get("fee") or 0)
                    notify.trade_closed(
                        symbol, direction, net, close_reason
                    )
            except Exception as ne:
                log.debug(f"[{symbol}] 平倉通知失敗: {ne}")
        except Exception as e:
            log.error(f"[{symbol}] 市價平倉失敗: {e}")

    def partial_close_market(self, symbol: str, trade_id: int,
                             pct: float = 0.5,
                             close_reason: str = "PARTIAL_TIMEOUT") -> bool:
        """
        市價部分平倉 —— 依剩餘倉位的 pct 比例（0~1）reduceOnly 市價出場。
        用於階段性時間止損（50% 時點先砍一半並移 SL 到保本）。
        """
        try:
            trade = self.db.get_trade_by_id(trade_id)
            if not trade:
                return False
            remaining = float(trade.get("qty", 0)) - float(trade.get("qty_closed", 0))
            if remaining <= 0:
                return False

            part_qty = self._round_qty(symbol, remaining * pct)
            if part_qty <= 0:
                return False

            direction = trade.get("direction", "")
            side = SIDE_SELL if direction == "LONG" else SIDE_BUY

            order = create_order_safe(
                self.client, symbol,
                side       = side,
                type       = ORDER_TYPE_MARKET,
                quantity   = part_qty,
                reduceOnly = True,
            )
            exit_price = float(order.get("avgPrice") or 0)
            if exit_price <= 0:
                try:
                    filled = self.client.futures_get_order(
                        symbol=symbol, orderId=order.get("orderId")
                    )
                    exit_price = float(filled.get("avgPrice") or 0)
                except Exception:
                    pass
            if exit_price <= 0:
                exit_price = float(trade.get("entry") or 0)

            # 寫入 DB（partial）
            self.db.close_trade(
                trade_id,
                exit_price   = exit_price,
                partial      = True,
                closed_qty   = part_qty,
                close_reason = close_reason,
            )
            log.warning(
                f"[{symbol}] 階段時間止損部分平倉 {int(pct*100)}%："
                f"qty={part_qty} @ {exit_price}"
            )
            return True
        except Exception as e:
            log.error(f"[{symbol}] 部分平倉失敗: {e}")
            return False

