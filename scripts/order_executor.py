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
            info = self.client.futures_exchange_info()
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
            self.client.futures_change_leverage(
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

            # 3. 市價開倉
            order = self.client.futures_create_order(
                symbol   = symbol,
                side     = side,
                type     = ORDER_TYPE_MARKET,
                quantity = qty,
            )
            order_id   = str(order.get("orderId", ""))
            # avgPrice 在建立當下可能回傳 "0"，需另外查詢實際成交價
            fill_price = float(order.get("avgPrice") or 0)
            if fill_price <= 0 and order_id:
                try:
                    filled = self.client.futures_get_order(
                        symbol=symbol, orderId=order_id
                    )
                    fill_price = float(filled.get("avgPrice") or 0)
                except Exception as e:
                    log.warning(f"查詢成交價失敗: {e}")
            if fill_price <= 0:
                fill_price = entry  # 最終 fallback
            log.info(
                f"[{symbol}] 開倉成功：{direction} qty={qty} @ {fill_price}"
            )

            # 4. 止損單（全倉 closePosition）
            sl_order = self.client.futures_create_order(
                symbol        = symbol,
                side          = close_side,
                type          = FUTURE_ORDER_TYPE_STOP_MARKET,
                stopPrice     = sl,
                closePosition = True,
            )
            sl_order_id = str(sl_order.get("orderId", ""))

            # 5. TP1 止盈（部分平倉）
            tp1_order_id = ""
            if qty_tp1 > 0:
                tp1_order = self.client.futures_create_order(
                    symbol     = symbol,
                    side       = close_side,
                    type       = FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
                    stopPrice  = tp1,
                    quantity   = qty_tp1,
                    reduceOnly = True,
                )
                tp1_order_id = str(tp1_order.get("orderId", ""))

            # 6. TP2 止盈（剩餘平倉）
            tp2_order_id = ""
            if qty_tp2 > 0:
                tp2_order = self.client.futures_create_order(
                    symbol     = symbol,
                    side       = close_side,
                    type       = FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
                    stopPrice  = tp2,
                    quantity   = qty_tp2,
                    reduceOnly = True,
                )
                tp2_order_id = str(tp2_order.get("orderId", ""))

            # 7. 寫入資料庫
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
            )

            log.info(
                f"[{symbol}] 掛單完成：SL={sl} TP1={tp1}(qty={qty_tp1}) "
                f"TP2={tp2}(qty={qty_tp2})"
            )
            return order

        except Exception as e:
            log.error(f"[{symbol}] 開倉失敗: {e}")
            return None

    # ── Breakeven Stop（移動止損至入場價）────────────────────────

    def move_to_breakeven(self, symbol: str, trade_id: int,
                          entry_price: float, direction: str) -> bool:
        """
        TP1 觸發後，把止損移到入場價（保本）
        1. 取消舊的止損單
        2. 下新的止損單在入場價
        3. 取消舊的 closePosition 止損（如果有的話）
        """
        try:
            trade = self.db.get_trade_by_id(trade_id)
            if not trade:
                return False

            close_side = SIDE_SELL if direction == "LONG" else SIDE_BUY

            # 取消舊止損
            old_sl_id = trade.get("sl_order_id")
            if old_sl_id:
                try:
                    self.client.futures_cancel_order(
                        symbol=symbol, orderId=int(old_sl_id)
                    )
                except Exception as e:
                    log.warning(f"取消舊止損失敗（可能已觸發）: {e}")

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

            # 下新止損
            new_sl_order = self.client.futures_create_order(
                symbol     = symbol,
                side       = close_side,
                type       = FUTURE_ORDER_TYPE_STOP_MARKET,
                stopPrice  = new_sl,
                quantity   = remaining,
                reduceOnly = True,
            )
            new_sl_id = str(new_sl_order.get("orderId", ""))

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

            # 取消舊止損
            old_sl_id = trade.get("sl_order_id")
            if old_sl_id:
                try:
                    self.client.futures_cancel_order(
                        symbol=symbol, orderId=int(old_sl_id)
                    )
                except Exception as e:
                    log.warning(f"取消舊止損失敗（可能已觸發）: {e}")

            remaining = trade["qty"] - trade["qty_closed"]
            remaining = self._round_qty(symbol, remaining)
            if remaining <= 0:
                return True

            new_sl_order = self.client.futures_create_order(
                symbol     = symbol,
                side       = close_side,
                type       = FUTURE_ORDER_TYPE_STOP_MARKET,
                stopPrice  = new_sl,
                quantity   = remaining,
                reduceOnly = True,
            )
            new_sl_id = str(new_sl_order.get("orderId", ""))
            self.db.update_sl(trade_id, new_sl=new_sl, sl_order_id=new_sl_id)

            log.info(f"[{symbol}] 追蹤止盈推進：SL={new_sl}")
            return True
        except Exception as e:
            log.error(f"[{symbol}] 追蹤止盈失敗: {e}")
            return False

    # ── 緊急操作 ─────────────────────────────────────────────────

    def cancel_all(self):
        """緊急撤銷所有未成交掛單（逐幣種取消）"""
        try:
            positions = self.client.futures_position_information()
            cancelled_symbols = set()
            for pos in positions:
                symbol = pos["symbol"]
                if symbol in cancelled_symbols:
                    continue
                try:
                    self.client.futures_cancel_all_open_orders(symbol=symbol)
                    cancelled_symbols.add(symbol)
                except Exception:
                    pass
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
                self.client.futures_create_order(
                    symbol     = symbol,
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

    def close_position_market(self, symbol: str, trade_id: int):
        """
        對特定 symbol 執行市價平倉（均值回歸超時用）。
        平倉後更新資料庫狀態。
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

            order = self.client.futures_create_order(
                symbol     = symbol,
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

            # 撤銷剩餘掛單
            try:
                self.client.futures_cancel_all_open_orders(symbol=symbol)
            except Exception:
                pass

            self.db.close_trade(trade_id, exit_price=exit_price or 0)
            log.warning(
                f"[{symbol}] 市價平倉（超時）：qty={abs_qty} @ {exit_price}"
            )
        except Exception as e:
            log.error(f"[{symbol}] 市價平倉失敗: {e}")

