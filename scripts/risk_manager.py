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

# 幣安合約手續費（taker）— 從 Config 取得，支援 .env 覆蓋（VIP 階級不同可調）
TAKER_FEE_RATE = Config.TAKER_FEE_RATE
MAKER_FEE_RATE = Config.MAKER_FEE_RATE


class RiskManager:
    def __init__(self, client: Client, db, market_ctx=None):
        self.client       = client
        self.db           = db
        self.market_ctx   = market_ctx
        self.leverage     = Config.MAX_LEVERAGE          # 固定 3x
        self.margin_usdt  = Config.MARGIN_USDT           # 每筆固定保證金（USDT）
        self.max_loss_pct = Config.MAX_DAILY_LOSS
        # 同方向高相關倉位上限（預設 2，小資金更嚴格）
        self.max_same_direction_high_corr = int(
            os.getenv("MAX_SAME_DIR_HIGH_CORR", 2)
        )
        self.high_corr_threshold = float(
            os.getenv("HIGH_CORR_THRESHOLD", 0.6)
        )

    # ── 單邊倉位上限 ─────────────────────────────────────────────
    def can_open_more_in_direction(self, direction: str) -> bool:
        """
        單邊倉位上限護欄：避免 MAX_POSITIONS=6 被全押同向。
        direction: "LONG" / "SHORT"
        預設 MAX_LONGS=MAX_SHORTS=4。
        """
        try:
            same_dir = self.db.get_open_trades_by_direction(direction)
        except Exception as e:
            log.warning(f"查 {direction} 倉位數失敗: {e}")
            return True  # 查不到就放行，由 MAX_POSITIONS 兜底
        count = len(same_dir)
        cap = Config.MAX_LONGS if direction == "LONG" else Config.MAX_SHORTS
        if count >= cap:
            log.warning(
                f"同向({direction})持倉已達上限 {count}/{cap}，拒絕開倉"
            )
            return False
        return True

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

    # ── Layer 2：方向中性護欄（多空名目 3:1 不能再開同向）─────
    def check_directional_balance(self, direction: str) -> bool:
        """
        檢查現有未平倉的多/空名目（USDT）比例。
        若擬開方向會讓比例超過 DIRECTIONAL_BALANCE_RATIO_MAX（預設 3:1），
        拒絕開倉。讓帳戶在 strategy-mix 下保持自動 beta 中性。

        例：總多 600U / 總空 100U = 6:1，再開多會更失衡 → 拒絕；
            開空會收斂 → 允許。
        """
        try:
            longs = self.db.get_open_trades_by_direction("LONG")
            shorts = self.db.get_open_trades_by_direction("SHORT")
        except Exception as e:
            log.warning(f"查多空倉位失敗（放行）: {e}")
            return True

        notional_long = sum(
            float(t.get("entry", 0)) * float(t.get("qty", 0)) for t in longs
        )
        notional_short = sum(
            float(t.get("entry", 0)) * float(t.get("qty", 0)) for t in shorts
        )

        ratio_max = float(getattr(Config, "DIRECTIONAL_BALANCE_RATIO_MAX", 3.0))

        # 雙邊都很小（< 50 USDT）無實質風險，放行
        if notional_long < 50 and notional_short < 50:
            return True

        if direction == "LONG":
            # 開多會讓 long 變更多；只有當 short==0 或 long/short 已超 ratio 才擋
            if notional_short == 0 and notional_long > 0:
                log.warning(
                    f"[directional-balance] 已有 {notional_long:.0f}U 多單但 0U 空單，"
                    f"再開多會單向過度集中（拒絕）"
                )
                return False
            if (notional_short > 0
                    and notional_long / notional_short >= ratio_max):
                log.warning(
                    f"[directional-balance] long/short = "
                    f"{notional_long:.0f}/{notional_short:.0f} = "
                    f"{notional_long/notional_short:.2f} ≥ {ratio_max}，拒絕再開多"
                )
                return False
        else:   # SHORT
            if notional_long == 0 and notional_short > 0:
                log.warning(
                    f"[directional-balance] 已有 {notional_short:.0f}U 空單但 0U 多單，"
                    f"再開空會單向過度集中（拒絕）"
                )
                return False
            if (notional_long > 0
                    and notional_short / notional_long >= ratio_max):
                log.warning(
                    f"[directional-balance] short/long = "
                    f"{notional_short:.0f}/{notional_long:.0f} = "
                    f"{notional_short/notional_long:.2f} ≥ {ratio_max}，拒絕再開空"
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
        tp1_split_pct: float = 0.5,
        signal_score: Optional[int] = None,
    ) -> Optional[dict]:
        """
        計算倉位大小（考慮手續費）
        分批止盈：tp1_split_pct 在 TP1，其余在 TP2
        signal_score: 訊號分數（供 SL 災區過濾使用，None 則不啟用）

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

        # 止損幅度（先算以便 risk-based sizing 使用）
        sl_pct = abs(entry - stop_loss) / entry
        if sl_pct < 0.005:
            log.warning(
                f"止損幅度過小 ({sl_pct:.2%}) < 0.5%，略過"
                f"（crypto 波動大，過緊會被雜訊掃出場）"
            )
            return None
        if sl_pct > 0.12:
            log.warning(f"止損幅度過大 ({sl_pct:.2%})，略過")
            return None

        # SL 1.5-3% 災區過濾：DB 證據顯示此區間 30 單勝率 26.7%、虧 -300 USDT
        # （最大虧損單一 bucket），不夠近不會被雜訊掃、不夠遠沒耐心跑。
        # 此區間只放行 score ≥ 4 的高強度訊號；低分訊號在此 SL 範圍是純虧。
        if (signal_score is not None
                and 0.015 <= sl_pct <= 0.03
                and signal_score < 4):
            log.warning(
                f"SL 落在災區 {sl_pct:.2%}（1.5-3%）且訊號分數 "
                f"{signal_score} < 4，略過（此 bucket 歷史勝率僅 26.7%）"
            )
            return None

        # ── 倉位計算：Risk-based sizing（base = 固定保證金）──────
        # 每筆最多虧損 = MARGIN_USDT × RISK_PCT_PER_TRADE
        #   qty = target_risk / abs(entry - sl)
        # 高波動幣種 SL 距離大 → qty 自動變小；低波動幣種反之。
        # MARGIN_USDT 同時做為「單筆保證金上限」(qty×entry/leverage ≤ MARGIN_USDT)，
        # 避免 SL 極小時 qty 爆炸、實際保證金超過用戶設定。
        # 設 RISK_PCT_PER_TRADE=0 則退回固定保證金邏輯。
        # 信心分層倉位：score 越高，敢壓越多（平均值不變）
        #   score ≤ 3 → 0.7× （邊緣訊號，縮小暴險）
        #   score = 4 → 1.0× （基準）
        #   score ≥ 5 → 1.3× （高信心，集中火力）
        # 錢集中在期望值大的訊號，低分訊號只是試單。
        score_mult = 1.0
        if signal_score is not None:
            if signal_score <= 3:
                score_mult = 0.7
            elif signal_score >= 5:
                score_mult = 1.3

        risk_pct = Config.RISK_PCT_PER_TRADE
        if risk_pct > 0:
            target_risk_usdt = self.margin_usdt * risk_pct * score_mult
            qty_by_risk      = target_risk_usdt / abs(entry - stop_loss)
            notional_by_risk = qty_by_risk * entry
            margin_by_risk   = notional_by_risk / self.leverage
            # 上限：不超過固定 MARGIN_USDT × score_mult
            #   高分訊號允許放大到 1.3×MARGIN_USDT；低分縮到 0.7×
            margin = min(margin_by_risk, self.margin_usdt * score_mult)
            log.debug(
                f"Risk-based sizing: MARGIN_USDT={self.margin_usdt} "
                f"× RISK_PCT={risk_pct:.0%} × score_mult={score_mult} "
                f"= target_risk={target_risk_usdt:.2f} "
                f"→ margin_by_risk={margin_by_risk:.2f} "
                f"→ margin={margin:.2f}"
            )
        else:
            margin = self.margin_usdt * score_mult

        # 極小保證金守門：小於 MARGIN_USDT × 10% 時不值得下（訊號邊緣雜訊）
        if margin < self.margin_usdt * 0.1:
            log.warning(
                f"計算保證金 {margin:.2f} < MARGIN_USDT×10% "
                f"({self.margin_usdt*0.1:.2f})，倉位過小，略過"
            )
            return None
        if balance < margin:
            log.warning(f"可用餘額 {balance:.2f} < 每筆保證金 {margin:.2f} USDT，略過")
            return None

        # 計算倉位：保證金 → 名義值 → 數量
        notional  = margin * self.leverage
        qty       = notional / entry
        risk_usdt = sl_pct * notional  # 止損時的預期虧損（供參考）

        # 精度處理
        qty = max(round(qty, 3), 0.001)

        # 分批止盈：依 tp1_split_pct 分配
        qty_tp1 = round(qty * tp1_split_pct, 3)
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
