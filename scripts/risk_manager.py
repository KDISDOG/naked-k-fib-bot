"""
Risk Manager v2 — 倉位計算、手續費、分批止盈、breakeven stop

改進：
  1. 加入手續費計算（taker 0.04%）
  2. 分批止盈：50% 在 TP1 出場，50% 跑到 TP2
  3. Breakeven stop：TP1 觸發後止損移至入場價
  4. 護欄：考慮手續費後的真實風險
"""
import os
import sys
import logging
from datetime import date
from pathlib import Path
from typing import Optional
from binance.client import Client

sys.path.insert(0, str(Path(__file__).parent))
from config import Config
from api_retry import retry_api

log = logging.getLogger("risk")

# 幣安合約手續費（taker）
TAKER_FEE_RATE = 0.0004   # 0.04%
MAKER_FEE_RATE = 0.0002   # 0.02%


class RiskManager:
    def __init__(self, client: Client, db, market_ctx=None):
        self.client       = client
        self.db           = db
        self.market_ctx   = market_ctx
        self.leverage     = Config.MAX_LEVERAGE          # 固定 3x
        self.risk_pct     = Config.RISK_PER_TRADE
        self.max_loss_pct = Config.MAX_DAILY_LOSS
        self.max_notional_pct = Config.MAX_NOTIONAL_PCT
        # 同方向高相關倉位上限（預設 2，小資金更嚴格）
        self.max_same_direction_high_corr = int(
            os.getenv("MAX_SAME_DIR_HIGH_CORR", 2)
        )
        self.high_corr_threshold = float(
            os.getenv("HIGH_CORR_THRESHOLD", 0.8)
        )

    # ── 相關性控管 ───────────────────────────────────────────────
    def can_open_direction(self, symbol: str, direction: str) -> bool:
        """
        BTC 相關性控管：
          - 若此 symbol 與 BTC 高相關（|corr| > threshold）
          - 且同方向已有 >= max_same_direction_high_corr 個高相關倉位
          - 則拒絕開倉（保留倉位給低相關幣種分散風險）
        """
        if not self.market_ctx:
            return True
        if symbol == "BTCUSDT":
            return True
        if not self.market_ctx.is_high_correlation(
            symbol, threshold=self.high_corr_threshold
        ):
            return True

        # 統計同方向、已是高相關的未平倉數
        same_dir_trades = self.db.get_open_trades_by_direction(direction)
        high_corr_count = 0
        for t in same_dir_trades:
            corr = t.get("btc_corr")
            if corr is not None and abs(corr) >= self.high_corr_threshold:
                high_corr_count += 1

        if high_corr_count >= self.max_same_direction_high_corr:
            log.warning(
                f"[{symbol}] 同方向({direction})高相關倉位已達上限 "
                f"{high_corr_count}/{self.max_same_direction_high_corr}，拒絕開倉"
            )
            return False
        return True

    # ── 餘額查詢 ─────────────────────────────────────────────────

    def _get_available_balance(self) -> float:
        try:
            account = retry_api(self.client.futures_account)
            for asset in account["assets"]:
                if asset["asset"] == "USDT":
                    return float(asset["availableBalance"])
        except Exception as e:
            log.error(f"取得餘額失敗: {e}")
        return 0.0

    def _get_total_balance(self) -> float:
        try:
            account = retry_api(self.client.futures_account)
            for asset in account["assets"]:
                if asset["asset"] == "USDT":
                    return float(asset["walletBalance"])
        except Exception as e:
            log.error(f"取得總餘額失敗: {e}")
        return 0.0

    # ── 每日虧損檢查 ─────────────────────────────────────────────

    def daily_loss_exceeded(self) -> bool:
        total = self._get_total_balance()
        today_pnl = self.db.get_today_pnl()
        if total <= 0:
            return False
        loss_pct = abs(min(today_pnl, 0)) / total
        if loss_pct >= self.max_loss_pct:
            log.error(
                f"每日最大虧損觸發！今日 P&L={today_pnl:.2f} USDT，"
                f"虧損比例={loss_pct:.1%} >= 護欄 {self.max_loss_pct:.1%}"
            )
            return True
        return False

    # ── 手續費估算 ───────────────────────────────────────────────

    @staticmethod
    def estimate_fee(qty: float, price: float,
                     is_maker: bool = False) -> float:
        """估算單邊手續費"""
        rate = MAKER_FEE_RATE if is_maker else TAKER_FEE_RATE
        return qty * price * rate

    @staticmethod
    def estimate_round_trip_fee(qty: float, entry: float,
                                exit_price: float) -> float:
        """估算來回手續費（開 + 平）"""
        open_fee  = qty * entry * TAKER_FEE_RATE
        close_fee = qty * exit_price * TAKER_FEE_RATE
        return open_fee + close_fee

    # ── 倉位計算（含手續費）──────────────────────────────────────

    def calc_position(
        self,
        entry:     float,
        stop_loss: float,
        tp1:       float,
        tp2:       float,
        min_rr:    float = 1.2,
    ) -> Optional[dict]:
        """
        計算倉位大小（考慮手續費）
        分批止盈：50% qty 在 TP1，50% 在 TP2

        回傳:
            {
                qty, qty_tp1, qty_tp2,
                notional, margin,
                sl, tp1, tp2,
                leverage, risk_usdt,
                est_fee_open, est_fee_total,
                net_rr  (考慮手續費後的真實 R:R)
            }
        """
        balance = self._get_available_balance()
        if balance <= 0:
            log.warning("可用餘額為 0，略過")
            return None

        # 每筆最大可承受虧損（含手續費預留）
        risk_usdt = balance * self.risk_pct

        # 止損幅度
        sl_pct = abs(entry - stop_loss) / entry
        if sl_pct < 0.003:
            log.warning(f"止損幅度過小 ({sl_pct:.2%})，略過")
            return None
        if sl_pct > 0.12:
            log.warning(f"止損幅度過大 ({sl_pct:.2%})，略過")
            return None

        # 考慮手續費後的真實風險
        # 止損時的來回手續費 = 2 * qty * entry * taker_rate（近似）
        # 真實虧損 = (sl_pct * qty * entry) + (2 * qty * entry * taker_rate)
        # risk_usdt = qty * entry * (sl_pct + 2 * taker_rate)
        effective_sl_pct = sl_pct + 2 * TAKER_FEE_RATE
        qty = risk_usdt / (effective_sl_pct * entry)

        # 所需保證金
        notional = qty * entry
        margin   = notional / self.leverage

        # 護欄：單筆保證金不超過帳戶 max_notional_pct
        if margin > balance * self.max_notional_pct:
            margin   = balance * self.max_notional_pct
            notional = margin * self.leverage
            qty      = notional / entry
            log.info(f"倉位縮減至保證金上限：margin={margin:.2f} USDT")

        # 精度處理
        qty = max(round(qty, 3), 0.001)

        # 分批止盈：50/50
        qty_tp1 = round(qty * 0.5, 3)
        qty_tp2 = qty - qty_tp1
        # 確保最小下單量
        if qty_tp1 < 0.001:
            qty_tp1 = qty
            qty_tp2 = 0.0

        # 手續費估算
        est_fee_open = self.estimate_fee(qty, entry)

        # 情境 1: 止損 → 來回手續費
        fee_if_sl = self.estimate_round_trip_fee(qty, entry, stop_loss)

        # 情境 2: 全部止盈 → 分兩批平倉
        fee_tp1_close = self.estimate_fee(qty_tp1, tp1) if qty_tp1 > 0 else 0
        fee_tp2_close = self.estimate_fee(qty_tp2, tp2) if qty_tp2 > 0 else 0
        fee_if_tp = est_fee_open + fee_tp1_close + fee_tp2_close

        # 計算考慮手續費後的真實 R:R
        raw_risk = abs(entry - stop_loss) * qty
        raw_reward_tp1 = abs(tp1 - entry) * qty_tp1 if qty_tp1 else 0
        raw_reward_tp2 = abs(tp2 - entry) * qty_tp2 if qty_tp2 else 0
        raw_reward = raw_reward_tp1 + raw_reward_tp2

        net_risk   = raw_risk + fee_if_sl
        net_reward = raw_reward - fee_if_tp
        net_rr = net_reward / net_risk if net_risk > 0 else 0

        # 護欄：考慮手續費後 R:R < min_rr 就不值得做（per-strategy）
        if net_rr < min_rr:
            log.warning(
                f"考慮手續費後 R:R={net_rr:.2f} < {min_rr}，不划算，略過"
            )
            return None

        result = {
            "qty":          qty,
            "qty_tp1":      qty_tp1,
            "qty_tp2":      qty_tp2,
            "notional":     round(notional, 2),
            "margin":       round(margin, 2),
            "sl":           round(stop_loss, 6),
            "tp1":          round(tp1, 6),
            "tp2":          round(tp2, 6),
            "leverage":     self.leverage,
            "risk_usdt":    round(risk_usdt, 2),
            "est_fee_open": round(est_fee_open, 4),
            "est_fee_total":round(fee_if_tp, 4),
            "net_rr":       round(net_rr, 2),
        }
        log.info(
            f"倉位計算：qty={qty} (TP1={qty_tp1} + TP2={qty_tp2}) "
            f"margin={margin:.2f}  SL={stop_loss:.4f}  "
            f"TP1={tp1:.4f}  TP2={tp2:.4f}  "
            f"NetR:R={net_rr:.2f}  EstFee={fee_if_tp:.4f}"
        )
        return result
