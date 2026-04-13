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
from binance.client import Client
from risk_manager import RiskManager

log = logging.getLogger("syncer")


class PositionSyncer:
    def __init__(self, client: Client, db, executor):
        self.client   = client
        self.db       = db
        self.executor = executor

    def sync(self):
        """主同步邏輯：比對 DB 和幣安實際倉位"""
        open_trades = self.db.get_open_trades()
        if not open_trades:
            return

        # 取得幣安所有倉位
        try:
            positions = self.client.futures_position_information()
        except Exception as e:
            log.error(f"取得幣安倉位失敗: {e}")
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
            # LONG = 正，SHORT = 負
            if direction == "LONG":
                actual_qty = actual_amt
            else:
                actual_qty = abs(actual_amt)

            expected_remaining = qty - qty_closed

            # ── 情況 1: 完全平倉 ────────────────────────────────
            if actual_qty <= 0 or abs(actual_qty) < 0.0001:
                log.info(f"[{symbol}] #{trade_id} 偵測到完全平倉")
                exit_price = self._get_last_trade_price(symbol)
                fee = self._get_recent_fee(symbol, qty)
                self.db.close_trade(
                    trade_id   = trade_id,
                    exit_price = exit_price or entry,
                    fee        = fee,
                    partial    = False,
                )
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
                    trade_id   = trade_id,
                    exit_price = exit_price or entry,
                    fee        = fee,
                    partial    = True,
                    closed_qty = qty_diff,
                )

                # 觸發 Breakeven Stop（如果還沒移過）
                if not trade["breakeven"]:
                    self.executor.move_to_breakeven(
                        symbol      = symbol,
                        trade_id    = trade_id,
                        entry_price = entry,
                        direction   = direction,
                    )

            # ── 情況 3: 倉位不變 → 什麼都不做 ────────────────────

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
