"""
granville.py — 葛蘭碧 Granville 策略（精簡 4 法則：1, 2, 5, 6）

設計目的：< 1000 USDT 帳戶趨勢跟隨備案，與 NKF/MASR 形成互補。
規格嚴格遵守，不做 3/4/7/8（在加密貨幣上勝率 < 40% 或會被連環爆）。

進場條件（4H + EMA60 + 嚴格 AND）：
  法則 1（多單）：
    1. 上一根 close ≤ EMA60
    2. 當前 close > EMA60，且突破幅度 > 0.3 × ATR(14)
    3. EMA60 過去 5 根斜率 ≥ 0
    4. 量 > 過去 20 根均量
    5. ADX(14) > 20
  法則 5（空單）：對稱反向
  法則 2（加碼多）：
    1. 已持有該幣的法則 1 多單，浮盈 > 0
    2. 價格回測至 EMA60 ± 0.5×ATR
    3. 回測過程未跌破 EMA60 超過 1 根 K
    4. 出現陽線（close > open）
    5. 加碼倉位 = 原倉位 50%
  法則 6（加碼空）：對稱反向

出場：
  SL  = entry - 1.5×ATR（多） / entry + 1.5×ATR（空）
  TP1 = entry + 2.0×ATR，平 50%
  TP2 = entry + 4.0×ATR，平剩餘 50%
  TP1 後啟用 trailing：跌破 EMA60 全平
  max_hold_bars = 30 根 4H（5 天）強制平倉

風控：
  倉位數受全局 MAX_POSITIONS 約束（不另設 per-strategy 上限）
  倉位大小由 RiskManager.calc_position 算（MARGIN_USDT × leverage）
  連續虧 3 筆暫停 12 小時
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, List
import pandas as pd
import pandas_ta as ta

from binance.client import Client
from .base_strategy import BaseStrategy, Signal

log = logging.getLogger("strategy.granville")


class GranvilleStrategy(BaseStrategy):

    def __init__(self, client: Client, market_ctx=None, db=None,
                  granville_screener=None):
        self._client = client
        self._market_ctx = market_ctx
        self._db = db
        self._screener = granville_screener  # GranvilleScreener instance

    @property
    def name(self) -> str:
        return "granville"

    @property
    def default_timeframe(self) -> str:
        from config import Config
        return Config.GRANVILLE_TIMEFRAME

    # ── K 線取得 ─────────────────────────────────────────────────
    def _get_klines(self, symbol: str, interval: str,
                    limit: int = 200) -> pd.DataFrame:
        if self._market_ctx is not None and hasattr(
                self._market_ctx, "get_klines"):
            return self._market_ctx.get_klines(symbol, interval, limit)
        from api_retry import weight_aware_call, klines_weight
        raw = weight_aware_call(
            self._client.futures_klines, weight=klines_weight(limit),
            symbol=symbol, interval=interval, limit=limit,
        )
        df = pd.DataFrame(raw, columns=[
            "time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "trades", "tbav", "tbqv", "ignore"
        ])
        for col in ["open", "high", "low", "close", "volume", "qav"]:
            df[col] = df[col].astype(float)
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        return df.reset_index(drop=True)

    # ── 選幣（委託 GranvilleScreener）────────────────────────
    def screen_coins(self, candidates: List[str]) -> List[str]:
        if self._screener is None:
            log.warning("Granville 未注入 screener，回傳空候選")
            return []
        return self._screener.screen(candidates)

    # ── 指標預計算 ───────────────────────────────────────────
    def prepare_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """計算 EMA60、EMA20、ATR(14)、ADX(14)、20 根均量。"""
        from config import Config
        out = df.copy()
        out["ema60"] = ta.ema(out["close"], length=Config.GRANVILLE_EMA_PERIOD)
        out["ema20"] = ta.ema(out["close"], length=Config.GRANVILLE_EMA_SHORT)
        out["atr"] = ta.atr(out["high"], out["low"], out["close"],
                             length=Config.GRANVILLE_ATR_PERIOD)
        adx_df = ta.adx(out["high"], out["low"], out["close"],
                         length=Config.GRANVILLE_ADX_PERIOD)
        if adx_df is not None and "ADX_14" in adx_df.columns:
            out["adx"] = adx_df["ADX_14"]
        else:
            out["adx"] = float("nan")
        out["vol_ma20"] = out["volume"].rolling(20).mean().shift(1)
        return out

    # ── 連虧暫停檢查 ─────────────────────────────────────────
    def _is_paused_by_consecutive_loss(self) -> bool:
        from config import Config
        if self._db is None:
            return False
        limit = int(Config.GRANVILLE_CONSEC_LOSS_LIMIT)
        pause_h = float(Config.GRANVILLE_PAUSE_HOURS)
        try:
            outcomes = self._db.get_strategy_recent_outcomes(
                strategy=self.name, limit=limit
            )
        except Exception as e:
            log.debug(f"Granville 查連虧失敗: {e}")
            return False
        # outcomes 是 list[(net_pnl, closed_at)]，最新先
        if len(outcomes) < limit:
            return False
        if any(p > 0 for p, _ in outcomes):
            return False
        # 全虧 → 看最近一筆 closed_at 是否在 pause_h 內
        last_close = outcomes[0][1]
        if last_close is None:
            return False
        try:
            last_dt = (last_close if isinstance(last_close, datetime)
                       else datetime.fromisoformat(str(last_close)))
        except Exception:
            return False
        elapsed = datetime.utcnow() - last_dt
        if elapsed < timedelta(hours=pause_h):
            log.info(
                f"[Granville] 連虧 {limit} 筆，暫停中（剩 "
                f"{(timedelta(hours=pause_h)-elapsed).total_seconds()/3600:.1f}h）"
            )
            return True
        return False

    # ── 出場價計算 ───────────────────────────────────────────
    def calculate_exit_levels(self, entry_price: float, atr: float,
                               side: str) -> dict:
        """根據 ATR 計算 SL / TP1 / TP2。"""
        from config import Config
        sl_d = float(Config.GRANVILLE_SL_ATR_MULT) * atr
        tp1_d = float(Config.GRANVILLE_TP1_ATR_MULT) * atr
        tp2_d = float(Config.GRANVILLE_TP2_ATR_MULT) * atr
        if side == "LONG":
            return {
                "sl": entry_price - sl_d,
                "tp1": entry_price + tp1_d,
                "tp2": entry_price + tp2_d,
            }
        else:
            return {
                "sl": entry_price + sl_d,
                "tp1": entry_price - tp1_d,
                "tp2": entry_price - tp2_d,
            }

    # ── 法則 1：主多單 ───────────────────────────────────────
    def check_rule_1(self, df: pd.DataFrame,
                     symbol: str = "") -> Optional[Signal]:
        from config import Config
        if len(df) < int(Config.GRANVILLE_EMA_PERIOD) + 10:
            return None

        # 用倒數第二根（已收盤確認）為「當前」
        # i = -1 是最新（可能未收盤），所以用 -2/-3
        try:
            cur = df.iloc[-2]
            prev = df.iloc[-3]
        except IndexError:
            return None

        ema60_v = float(cur["ema60"])
        atr_v = float(cur["atr"])
        adx_v = float(cur["adx"])
        cur_close = float(cur["close"])
        prev_close = float(prev["close"])
        cur_vol = float(cur["volume"])
        vol_ma = float(cur["vol_ma20"]) if not pd.isna(cur["vol_ma20"]) else 0

        if any(pd.isna(x) for x in (ema60_v, atr_v, adx_v)):
            return None

        # 條件 1: prev close ≤ EMA60
        if prev_close > ema60_v:
            return None

        # 條件 2: 突破幅度 > 0.3×ATR
        breakout = cur_close - ema60_v
        if breakout <= float(Config.GRANVILLE_BREAKOUT_ATR_MULT) * atr_v:
            return None

        # 條件 3: EMA60 過去 5 根斜率 ≥ 0
        slope_n = int(Config.GRANVILLE_SLOPE_LOOKBACK)
        ema60_window = df["ema60"].iloc[-slope_n - 1:-1]   # 取「至 cur」前 N+1 根去掉最新一根
        if len(ema60_window) >= 2:
            if float(ema60_window.iloc[-1]) < float(ema60_window.iloc[0]):
                return None  # 下降中

        # 條件 4: 量 > 20 根均量
        if vol_ma <= 0 or cur_vol <= vol_ma * float(Config.GRANVILLE_VOL_MIN_MULT):
            return None

        # 條件 5: ADX > 20
        if adx_v <= float(Config.GRANVILLE_ADX_MIN):
            return None

        # 出場
        levels = self.calculate_exit_levels(cur_close, atr_v, "LONG")
        score = self._score_signal(adx_v, cur_vol, vol_ma, atr_v,
                                    breakout, "rule_1")

        sig = Signal(
            symbol=symbol, side="LONG",
            entry_price=cur_close,
            stop_loss=levels["sl"],
            take_profit_1=levels["tp1"],
            take_profit_2=levels["tp2"],
            score=score,
            strategy_name=self.name,
            timeframe=self.default_timeframe,
            pattern="GRANVILLE_RULE_1",
            use_trailing=bool(Config.GRANVILLE_TRAILING_AFTER_TP1),
            trailing_atr=atr_v,
            metadata={
                "ema60": round(ema60_v, 6),
                "atr": round(atr_v, 6),
                "adx": round(adx_v, 2),
                "breakout_atr": round(breakout / atr_v, 2),
                "vol_ratio": round(cur_vol / vol_ma, 2) if vol_ma > 0 else 0,
                "rule": "1",
            },
        )
        return sig if self.validate_signal(sig) else None

    # ── 法則 5：主空單（對稱反向）───────────────────────────
    def check_rule_5(self, df: pd.DataFrame,
                     symbol: str = "") -> Optional[Signal]:
        from config import Config
        if len(df) < int(Config.GRANVILLE_EMA_PERIOD) + 10:
            return None

        try:
            cur = df.iloc[-2]
            prev = df.iloc[-3]
        except IndexError:
            return None

        ema60_v = float(cur["ema60"])
        atr_v = float(cur["atr"])
        adx_v = float(cur["adx"])
        cur_close = float(cur["close"])
        prev_close = float(prev["close"])
        cur_vol = float(cur["volume"])
        vol_ma = float(cur["vol_ma20"]) if not pd.isna(cur["vol_ma20"]) else 0

        if any(pd.isna(x) for x in (ema60_v, atr_v, adx_v)):
            return None

        # 條件 1: prev close ≥ EMA60
        if prev_close < ema60_v:
            return None
        # 條件 2: 跌破幅度 > 0.3×ATR
        breakdown = ema60_v - cur_close
        if breakdown <= float(Config.GRANVILLE_BREAKOUT_ATR_MULT) * atr_v:
            return None
        # 條件 3: EMA60 過去 5 根斜率 ≤ 0（不上揚）
        slope_n = int(Config.GRANVILLE_SLOPE_LOOKBACK)
        ema60_window = df["ema60"].iloc[-slope_n - 1:-1]
        if len(ema60_window) >= 2:
            if float(ema60_window.iloc[-1]) > float(ema60_window.iloc[0]):
                return None
        # 條件 4: 量
        if vol_ma <= 0 or cur_vol <= vol_ma * float(Config.GRANVILLE_VOL_MIN_MULT):
            return None
        # 條件 5: ADX
        if adx_v <= float(Config.GRANVILLE_ADX_MIN):
            return None

        levels = self.calculate_exit_levels(cur_close, atr_v, "SHORT")
        score = self._score_signal(adx_v, cur_vol, vol_ma, atr_v,
                                    breakdown, "rule_5")

        sig = Signal(
            symbol=symbol, side="SHORT",
            entry_price=cur_close,
            stop_loss=levels["sl"],
            take_profit_1=levels["tp1"],
            take_profit_2=levels["tp2"],
            score=score,
            strategy_name=self.name,
            timeframe=self.default_timeframe,
            pattern="GRANVILLE_RULE_5",
            use_trailing=bool(Config.GRANVILLE_TRAILING_AFTER_TP1),
            trailing_atr=atr_v,
            metadata={
                "ema60": round(ema60_v, 6),
                "atr": round(atr_v, 6),
                "adx": round(adx_v, 2),
                "breakdown_atr": round(breakdown / atr_v, 2),
                "vol_ratio": round(cur_vol / vol_ma, 2) if vol_ma > 0 else 0,
                "rule": "5",
            },
        )
        return sig if self.validate_signal(sig) else None

    # ── 法則 2：加碼多 ───────────────────────────────────────
    def check_rule_2(self, df: pd.DataFrame, existing_position: dict,
                      symbol: str = "") -> Optional[Signal]:
        """
        existing_position: dict（從 db 取得的 open trade）需有 entry / direction / unrealized_pnl
        """
        from config import Config
        if not existing_position:
            return None
        if existing_position.get("direction") != "LONG":
            return None
        # 浮盈 > 0
        upnl = float(existing_position.get("unrealized_pnl", 0)
                     or existing_position.get("upnl", 0))
        if upnl <= 0:
            return None

        try:
            cur = df.iloc[-2]
        except IndexError:
            return None
        ema60_v = float(cur["ema60"])
        atr_v = float(cur["atr"])
        cur_close = float(cur["close"])
        cur_open = float(cur["open"])
        if any(pd.isna(x) for x in (ema60_v, atr_v)):
            return None

        # 回測在 EMA60 ± 0.5×ATR
        retrace_band = float(Config.GRANVILLE_RETRACE_ATR_MULT) * atr_v
        if abs(cur_close - ema60_v) > retrace_band:
            return None

        # 過程中未跌破 EMA60 超過 1 根（檢查 retrace 看的最近 N 根）
        retrace_lookback = 5
        recent = df.iloc[-retrace_lookback - 1:-1]
        if len(recent) >= 2:
            below = (recent["close"] < recent["ema60"]).sum()
            if below > 1:
                return None

        # 陽線
        if cur_close <= cur_open:
            return None

        # 加碼用同樣 ATR 算 SL/TP（但實際下單 qty 由 caller × 0.5）
        levels = self.calculate_exit_levels(cur_close, atr_v, "LONG")
        sig = Signal(
            symbol=symbol, side="LONG",
            entry_price=cur_close,
            stop_loss=levels["sl"],
            take_profit_1=levels["tp1"],
            take_profit_2=levels["tp2"],
            score=2,  # 加碼為次級訊號
            strategy_name=self.name,
            timeframe=self.default_timeframe,
            pattern="GRANVILLE_RULE_2_ADD",
            use_trailing=bool(Config.GRANVILLE_TRAILING_AFTER_TP1),
            trailing_atr=atr_v,
            metadata={
                "ema60": round(ema60_v, 6),
                "atr": round(atr_v, 6),
                "rule": "2",
                "size_mult": float(Config.GRANVILLE_ADD_SIZE_MULT),
            },
        )
        return sig if self.validate_signal(sig) else None

    # ── 法則 6：加碼空 ───────────────────────────────────────
    def check_rule_6(self, df: pd.DataFrame, existing_position: dict,
                      symbol: str = "") -> Optional[Signal]:
        from config import Config
        if not existing_position:
            return None
        if existing_position.get("direction") != "SHORT":
            return None
        upnl = float(existing_position.get("unrealized_pnl", 0)
                     or existing_position.get("upnl", 0))
        if upnl <= 0:
            return None

        try:
            cur = df.iloc[-2]
        except IndexError:
            return None
        ema60_v = float(cur["ema60"])
        atr_v = float(cur["atr"])
        cur_close = float(cur["close"])
        cur_open = float(cur["open"])
        if any(pd.isna(x) for x in (ema60_v, atr_v)):
            return None

        retrace_band = float(Config.GRANVILLE_RETRACE_ATR_MULT) * atr_v
        if abs(cur_close - ema60_v) > retrace_band:
            return None

        # 過程中未漲破 EMA60 超過 1 根
        retrace_lookback = 5
        recent = df.iloc[-retrace_lookback - 1:-1]
        if len(recent) >= 2:
            above = (recent["close"] > recent["ema60"]).sum()
            if above > 1:
                return None

        # 陰線
        if cur_close >= cur_open:
            return None

        levels = self.calculate_exit_levels(cur_close, atr_v, "SHORT")
        sig = Signal(
            symbol=symbol, side="SHORT",
            entry_price=cur_close,
            stop_loss=levels["sl"],
            take_profit_1=levels["tp1"],
            take_profit_2=levels["tp2"],
            score=2,
            strategy_name=self.name,
            timeframe=self.default_timeframe,
            pattern="GRANVILLE_RULE_6_ADD",
            use_trailing=bool(Config.GRANVILLE_TRAILING_AFTER_TP1),
            trailing_atr=atr_v,
            metadata={
                "ema60": round(ema60_v, 6),
                "atr": round(atr_v, 6),
                "rule": "6",
                "size_mult": float(Config.GRANVILLE_ADD_SIZE_MULT),
            },
        )
        return sig if self.validate_signal(sig) else None

    # ── 評分（max 5）───────────────────────────────────────
    def _score_signal(self, adx: float, cur_vol: float, vol_ma: float,
                       atr: float, move: float, rule: str) -> int:
        score = 1  # 基礎（已通過所有 hard filter）
        if adx > 30:
            score += 1
        if vol_ma > 0 and cur_vol >= vol_ma * 2.0:
            score += 1
        if atr > 0 and move >= 0.6 * atr:
            score += 1
        if rule in ("rule_1", "rule_5"):
            score += 1   # 主訊號（非加碼）多 1 分
        return min(score, 5)

    # ── 主入口：依序檢查 4 個法則 ─────────────────────────
    def generate_signal(self, df: pd.DataFrame, position_state: dict,
                         symbol: str = "") -> Optional[Signal]:
        """
        position_state: dict[str, dict]
          - "current_position": 該 symbol 既有開倉（或 None）→ 用於法則 2/6 加碼
        """
        prepped = self.prepare_indicators(df)

        # 連虧暫停 → 不檢查任何法則
        if self._is_paused_by_consecutive_loss():
            return None

        existing = (position_state or {}).get("current_position")

        # 1. 加碼優先（已持倉時）
        if existing:
            if existing.get("direction") == "LONG":
                sig = self.check_rule_2(prepped, existing, symbol=symbol)
                if sig:
                    return sig
            elif existing.get("direction") == "SHORT":
                sig = self.check_rule_6(prepped, existing, symbol=symbol)
                if sig:
                    return sig
            # 已持倉時不檢查反向主訊號（避免亂開單）
            return None

        # 2. 主訊號（無持倉）— 倉位上限交由全局 MAX_POSITIONS 約束，不再 per-strategy
        sig = self.check_rule_1(prepped, symbol=symbol)
        if sig:
            return sig
        sig = self.check_rule_5(prepped, symbol=symbol)
        if sig:
            return sig
        return None

    # ── BaseStrategy 介面 ─────────────────────────────────
    def check_signal(self, symbol: str) -> Optional[Signal]:
        from config import Config
        try:
            df = self._get_klines(
                symbol, self.default_timeframe,
                limit=max(120, Config.GRANVILLE_EMA_PERIOD + 30),
            )
        except Exception as e:
            log.warning(f"[{symbol}] Granville K 線取得失敗: {e}")
            return None

        if len(df) < Config.GRANVILLE_EMA_PERIOD + 10:
            return None

        # 取既有倉位（讓 generate_signal 決定要不要走加碼路徑）
        existing = None
        if self._db is not None:
            try:
                rows = self._db.get_open_by_strategy(self.name)
                for r in rows:
                    if r.get("symbol") == symbol:
                        existing = r
                        break
            except Exception as e:
                log.debug(f"[{symbol}] 查既有 Granville 倉位失敗: {e}")

        sig = self.generate_signal(
            df, position_state={"current_position": existing}, symbol=symbol,
        )
        if sig:
            log.info(
                f"[{symbol}] Granville {sig.pattern} {sig.side}  "
                f"entry={sig.entry_price:.4f}  SL={sig.stop_loss:.4f}  "
                f"TP1={sig.take_profit_1:.4f}  score={sig.score}"
            )
        return sig
