"""
smc_sweep.py — SMC Liquidity Sweep + Reversal 策略（取代 MR）

核心邏輯（Smart Money Concepts）：
  機構通常在 swing high/low 上方/下方掛 stop loss → 形成「流動性」。
  價格刺破該位後反轉 = 機構「掃單後」入場的足跡。
  我們在這個反轉點順著機構方向開倉。

適用場景：
  - 1h timeframe（足夠抓「機構日內動作」，又比 4h 訊號量足）
  - 多空雙向（區別於 BD/ML 的單向）
  - 不依賴趨勢/盤整 regime（與 BD/ML 互補）

選幣條件（滿分 10，>= 6 入選）：
  - 流動性：USDT 24h 成交量 >= 500 萬             2 分
  - 波動適中：ATR(14) 1.0%-5%                      2 分
  - 結構清晰：近 50 根有可辨識 swing 高/低         2 分
  - 量能正常：近 20 根均量 > 5 根均量              2 分（防止冷門幣）
  - 趨勢中性：ADX 15-45（極端趨勢不適合 sweep）   2 分

入場條件（LONG）：
  1. 找最近 SMC_SWING_LOOKBACK 根的 swing low（fractal）
  2. 當前 K 棒 low 刺破該 swing low（差 ≥ SMC_SWEEP_MIN_PCT）
  3. 當前 K 棒 close 回到 swing low 之上（V 反確認）
  4. 反轉 K 棒形態（lower wick ≥ 1.5× body 或 bullish engulfing）
  5. 量能放大（>= SMC_VOL_MULT × 均量）

入場條件（SHORT，鏡像）：
  1. swing high 被刺破
  2. close 回到 swing high 之下
  3. 反轉 K 棒形態（upper wick ≥ 1.5× body）
  4. 量能放大

止盈止損：
  Entry: 當前 K 棒 close
  SL:    刺破點外側 + SMC_SL_BUFFER（再被掃就認虧）
  TP1:   1R（風險倍數）
  TP2:   2R 或最近的反向 liquidity zone（取較近者）
  超時：  SMC_TIMEOUT_BARS 根後強制平倉
"""
import logging
import numpy as np
import pandas as pd
import pandas_ta as ta
from typing import Optional, List, Tuple

from binance.client import Client
from .base_strategy import BaseStrategy, Signal

log = logging.getLogger("strategy.smc")


class SMCSweepStrategy(BaseStrategy):

    def __init__(self, client: Client, market_ctx=None):
        self._client = client
        self._market_ctx = market_ctx

    @property
    def name(self) -> str:
        return "smc_sweep"

    @property
    def default_timeframe(self) -> str:
        from config import Config
        return Config.SMC_TIMEFRAME

    # ── K 線取得 ─────────────────────────────────────────────────
    def _get_klines(self, symbol: str, interval: str,
                    limit: int = 200) -> pd.DataFrame:
        if self._market_ctx is not None and hasattr(
                self._market_ctx, "get_klines"):
            return self._market_ctx.get_klines(symbol, interval, limit)
        raw = self._client.futures_klines(
            symbol=symbol, interval=interval, limit=limit
        )
        df = pd.DataFrame(raw, columns=[
            "time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "trades", "tbav", "tbqv", "ignore"
        ])
        for col in ["open", "high", "low", "close", "volume", "qav"]:
            df[col] = df[col].astype(float)
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
        return df.reset_index(drop=True)

    # ── 選幣 ─────────────────────────────────────────────────────
    def screen_coins(self, candidates: List[str]) -> List[str]:
        from config import Config
        selected = []
        for symbol in candidates:
            try:
                score = self._score_symbol(symbol, Config)
                if score >= 6:
                    selected.append(symbol)
                    log.debug(f"[SMC 篩選] {symbol} 得分={score}")
            except Exception as e:
                log.debug(f"[SMC 篩選] {symbol} 失敗: {e}")
        log.info(
            f"[SMC] 選幣完成：{len(selected)} 支入選，"
            f"候選 {len(candidates)} 支"
        )
        return selected

    def _score_symbol(self, symbol: str, Config) -> int:
        tf = self.default_timeframe
        df = self._get_klines(symbol, tf, limit=200)
        if len(df) < 60:
            return 0
        score = 0

        # 1. 流動性（24h 成交量；1h tf × 24 = 24h）
        bars_per_day = 24 if tf == "1h" else 96  # 預留 15m 切換
        vol_24h = df["qav"].tail(bars_per_day).sum()
        if vol_24h >= 5_000_000:
            score += 2
        elif vol_24h >= 2_000_000:
            score += 1
        else:
            return 0  # 流動性不足

        # 2. 波動適中：ATR%（1.0% - 5.0%）
        atr = ta.atr(df["high"], df["low"], df["close"], length=14)
        atr_pct = float(atr.iloc[-1]) / float(df["close"].iloc[-1]) * 100
        if 1.0 <= atr_pct <= 5.0:
            score += 2
        elif 0.5 <= atr_pct < 1.0 or 5.0 < atr_pct <= 8.0:
            score += 1

        # 3. 結構清晰：近 50 根有可辨識 swing 高/低
        recent = df.tail(50)
        n_swing_h = self._count_swings(recent, "high", left=2, right=2)
        n_swing_l = self._count_swings(recent, "low",  left=2, right=2)
        if n_swing_h >= 2 and n_swing_l >= 2:
            score += 2
        elif n_swing_h >= 1 and n_swing_l >= 1:
            score += 1

        # 4. 量能正常：近 20 vs 近 5 根（避免冷門幣）
        try:
            recent_vol = float(df["qav"].tail(5).mean())
            past_vol   = float(df["qav"].iloc[-25:-5].mean())
            if past_vol > 0 and recent_vol >= past_vol * 0.5:
                score += 2
            elif past_vol > 0 and recent_vol >= past_vol * 0.3:
                score += 1
        except Exception:
            pass

        # 5. 趨勢中性：ADX 15-45（極端趨勢 sweep 失效）
        adx_df = ta.adx(df["high"], df["low"], df["close"], length=14)
        adx_val = float(adx_df["ADX_14"].iloc[-1]) if adx_df is not None else 0.0
        if 15 <= adx_val <= 45:
            score += 2
        elif 10 <= adx_val < 15 or 45 < adx_val <= 55:
            score += 1

        return score

    @staticmethod
    def _count_swings(df: pd.DataFrame, col: str,
                      left: int = 2, right: int = 2) -> int:
        """數 fractal swing 數量"""
        n = 0
        arr = df[col].values
        for i in range(left, len(arr) - right):
            if col == "high":
                if all(arr[i] >= arr[i - k] for k in range(1, left + 1)) and \
                   all(arr[i] >= arr[i + k] for k in range(1, right + 1)):
                    n += 1
            else:
                if all(arr[i] <= arr[i - k] for k in range(1, left + 1)) and \
                   all(arr[i] <= arr[i + k] for k in range(1, right + 1)):
                    n += 1
        return n

    # ── 訊號偵測 ─────────────────────────────────────────────────
    def check_signal(self, symbol: str) -> Optional[Signal]:
        from config import Config

        # SMC 不需要 regime gate（sweep 在所有 regime 都有效）
        # 但仍尊重 REGIME_GATE_ENABLED 全局開關（如果用戶要強制限制）
        # 這裡選擇不讀 regime_allows，因為 SMC 並未在 market_context 註冊。

        try:
            df = self._get_klines(
                symbol, self.default_timeframe,
                limit=max(150, Config.SMC_SWING_LOOKBACK * 3),
            )
        except Exception as e:
            log.warning(f"[{symbol}] SMC K 線取得失敗: {e}")
            return None

        if len(df) < 60:
            return None

        # 用倒數第二根（已收盤）作為「觸發 K 棒」
        df_a = df.iloc[:-1].copy().reset_index(drop=True)
        if len(df_a) < 50:
            return None

        # 觸發 K 棒
        latest = df_a.iloc[-1]
        cur_open  = float(latest["open"])
        cur_high  = float(latest["high"])
        cur_low   = float(latest["low"])
        cur_close = float(latest["close"])
        cur_vol   = float(latest["volume"])

        # 量能基準：前 20 根均量（不含當根）
        avg_vol = float(df_a["volume"].tail(21).iloc[:-1].mean())
        if avg_vol <= 0:
            return None
        vol_ok = cur_vol >= avg_vol * Config.SMC_VOL_MULT

        # ── 找 swing low / high（在當根之前的 lookback 範圍）─────
        lookback = int(Config.SMC_SWING_LOOKBACK)
        # 排除最後 SMC_SWING_RIGHT 根（fractal 需要右側確認，否則
        # 「當根」自己就是 swing 沒意義）
        right_buffer = int(Config.SMC_SWING_RIGHT)
        end_idx = len(df_a) - 1 - right_buffer
        start_idx = max(0, end_idx - lookback)
        if end_idx - start_idx < 5:
            return None

        win = df_a.iloc[start_idx:end_idx + 1]

        # swing low/high 用簡化 fractal：左 N 右 right_buffer 根
        left = int(Config.SMC_SWING_LEFT)
        sw_lows  = self._find_fractal_levels(win, "low", left, right_buffer)
        sw_highs = self._find_fractal_levels(win, "high", left, right_buffer)

        if not sw_lows and not sw_highs:
            return None

        # 取最近的 swing low/high
        last_sw_low  = max(sw_lows)  if sw_lows  else None  # max → 最近
        last_sw_high = min(sw_highs) if sw_highs else None  # min → 最近
        # （上面 max/min 取的是「最高/低的價格」，但我們要最近的「位置」）
        # 改用 list[-1] 取最近一個（已按時間順序）
        last_sw_low  = sw_lows[-1] if sw_lows else None
        last_sw_high = sw_highs[-1] if sw_highs else None

        side: Optional[str] = None
        sweep_level: float = 0.0

        # ── LONG：sweep swing low ────────────────────────────
        if last_sw_low is not None:
            sweep_pct = (last_sw_low - cur_low) / last_sw_low if last_sw_low > 0 else 0
            sweep_min = float(Config.SMC_SWEEP_MIN_PCT)
            sweep_max = float(Config.SMC_SWEEP_MAX_PCT)
            # 條件：刺破幅度在 [min, max] 範圍 + close 回到 sw_low 之上
            #       + 反轉 K 棒（lower wick ≥ 1.5× body 或 bullish engulfing）
            if (sweep_min <= sweep_pct <= sweep_max
                    and cur_close > last_sw_low
                    and self._is_bullish_reversal_candle(df_a)):
                if vol_ok:
                    side = "LONG"
                    sweep_level = last_sw_low

        # ── SHORT：sweep swing high ──────────────────────────
        if side is None and last_sw_high is not None:
            sweep_pct = (cur_high - last_sw_high) / last_sw_high if last_sw_high > 0 else 0
            sweep_min = float(Config.SMC_SWEEP_MIN_PCT)
            sweep_max = float(Config.SMC_SWEEP_MAX_PCT)
            if (sweep_min <= sweep_pct <= sweep_max
                    and cur_close < last_sw_high
                    and self._is_bearish_reversal_candle(df_a)):
                if vol_ok:
                    side = "SHORT"
                    sweep_level = last_sw_high

        if side is None:
            return None

        # ── 訊號評分 ─────────────────────────────────────────────
        score = self._score_signal(
            df_a, side, cur_close, sweep_level, cur_vol, avg_vol, Config
        )
        if score < int(Config.SMC_MIN_SCORE):
            log.debug(f"[{symbol}] SMC 訊號強度 {score} < {Config.SMC_MIN_SCORE}")
            return None

        # ── 計算 SL/TP ───────────────────────────────────────────
        atr_s = ta.atr(df_a["high"], df_a["low"], df_a["close"], length=14)
        atr_val = float(atr_s.iloc[-1]) if atr_s is not None else cur_close * 0.01
        sl, tp1, tp2 = self._calc_sl_tp(
            side, cur_close, sweep_level, atr_val, sw_highs, sw_lows, Config
        )

        sig = Signal(
            symbol        = symbol,
            side          = side,
            entry_price   = cur_close,
            stop_loss     = sl,
            take_profit_1 = tp1,
            take_profit_2 = tp2,
            score         = score,
            strategy_name = self.name,
            timeframe     = self.default_timeframe,
            pattern       = "SMC_SWEEP",
            metadata      = {
                "sweep_level": round(sweep_level, 6),
                "sweep_pct":   round(
                    abs(sweep_level - (cur_low if side == "LONG" else cur_high))
                    / sweep_level * 100,
                    3,
                ) if sweep_level > 0 else 0,
                "vol_ratio":   round(cur_vol / avg_vol, 2),
                "atr":         round(atr_val, 6),
            },
        )

        if not self.validate_signal(sig):
            log.debug(f"[{symbol}] SMC TP/SL 不合理，捨棄")
            return None

        log.info(
            f"[{symbol}] SMC 訊號：{side} sweep={sweep_level:.6f} "
            f"close={cur_close:.6f} 強度={score}"
        )
        return sig

    # ── Fractal swing levels ─────────────────────────────────────
    @staticmethod
    def _find_fractal_levels(df: pd.DataFrame, col: str,
                             left: int, right: int) -> List[float]:
        """回傳 fractal swing 價位列表（時間遞增順序）"""
        out: List[float] = []
        arr = df[col].values
        for i in range(left, len(arr) - right):
            if col == "high":
                if all(arr[i] >= arr[i - k] for k in range(1, left + 1)) and \
                   all(arr[i] >= arr[i + k] for k in range(1, right + 1)):
                    out.append(float(arr[i]))
            else:
                if all(arr[i] <= arr[i - k] for k in range(1, left + 1)) and \
                   all(arr[i] <= arr[i + k] for k in range(1, right + 1)):
                    out.append(float(arr[i]))
        return out

    # ── 反轉 K 棒判定 ────────────────────────────────────────────
    @staticmethod
    def _is_bullish_reversal_candle(df: pd.DataFrame) -> bool:
        """LONG：lower wick ≥ 1.5× body（pinbar）或 bullish engulfing"""
        c = df.iloc[-1]
        o, h, l, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
        body = abs(cl - o)
        lower_wick = min(o, cl) - l
        if body > 0 and lower_wick >= body * 1.5 and cl > o:
            # pinbar / hammer
            return True
        # bullish engulfing
        if len(df) >= 2:
            p = df.iloc[-2]
            po, pcl = float(p["open"]), float(p["close"])
            if pcl < po and cl > o and cl >= po and o <= pcl:
                return True
        # 至少：陽線 + 上引短於下影
        if cl > o and (h - max(o, cl)) < lower_wick:
            return True
        return False

    @staticmethod
    def _is_bearish_reversal_candle(df: pd.DataFrame) -> bool:
        """SHORT：upper wick ≥ 1.5× body 或 bearish engulfing"""
        c = df.iloc[-1]
        o, h, l, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
        body = abs(cl - o)
        upper_wick = h - max(o, cl)
        if body > 0 and upper_wick >= body * 1.5 and cl < o:
            return True
        if len(df) >= 2:
            p = df.iloc[-2]
            po, pcl = float(p["open"]), float(p["close"])
            if pcl > po and cl < o and cl <= po and o >= pcl:
                return True
        # 至少：陰線 + 下引短於上影
        if cl < o and (min(o, cl) - l) < upper_wick:
            return True
        return False

    # ── 訊號評分 ─────────────────────────────────────────────────
    def _score_signal(self, df: pd.DataFrame, side: str, close: float,
                      sweep_level: float, vol: float, avg_vol: float,
                      Config) -> int:
        score = 1  # 基礎分（已通過 sweep + reversal candle）

        # 量能放大階層
        ratio = vol / avg_vol if avg_vol > 0 else 1.0
        if ratio >= 2.5:
            score += 2
        elif ratio >= 1.5:
            score += 1

        # 反轉幅度（close 距 sweep_level 的比例）
        if sweep_level > 0:
            recovery_pct = abs(close - sweep_level) / sweep_level * 100
            if recovery_pct >= 0.3:
                score += 1

        # MACD 動能對齊
        try:
            macd = ta.macd(df["close"])
            if macd is not None and macd.shape[1] >= 3:
                hist = macd.iloc[:, 2]
                if not pd.isna(hist.iloc[-1]) and not pd.isna(hist.iloc[-2]):
                    if side == "LONG" and float(hist.iloc[-1]) > float(hist.iloc[-2]):
                        score += 1
                    elif side == "SHORT" and float(hist.iloc[-1]) < float(hist.iloc[-2]):
                        score += 1
        except Exception:
            pass

        # BTC 週線方向加分（sweep + 順大盤週線方向 = 信心高）
        try:
            if self._market_ctx is not None:
                btc_bull = self._market_ctx.btc_weekly_bullish()
                if btc_bull is True and side == "LONG":
                    score += 1
                elif btc_bull is False and side == "SHORT":
                    score += 1
        except Exception:
            pass

        return min(score, 5)

    # ── SL/TP 計算 ───────────────────────────────────────────────
    def _calc_sl_tp(self, side: str, entry: float, sweep_level: float,
                    atr_val: float, sw_highs: List[float],
                    sw_lows: List[float], Config) -> Tuple[float, float, float]:
        """
        SL：刺破點外側 + buffer（atr 比例）
        TP1：1R
        TP2：2R 或最近的反向 liquidity zone（取較近 + 仍 > 1R 的）
        """
        sl_buffer_pct = float(Config.SMC_SL_BUFFER)
        atr_buf = atr_val * sl_buffer_pct

        if side == "LONG":
            # SL 在 sweep_level 下方
            sl = sweep_level - atr_buf - entry * 0.001
            risk = entry - sl
            if risk <= 0:
                # 退而求其次：用 ATR-based SL
                sl = entry - atr_val * 1.5
                risk = entry - sl
            tp1 = entry + risk * 1.0
            # TP2：尋找上方 swing high 作為 liquidity target
            target = entry + risk * 2.0
            for sh in sorted(sw_highs):
                if sh > entry + risk * 1.2 and sh < entry + risk * 4.0:
                    target = min(target, sh)  # 取較近的合理目標
                    break
            tp2 = target
        else:
            sl = sweep_level + atr_buf + entry * 0.001
            risk = sl - entry
            if risk <= 0:
                sl = entry + atr_val * 1.5
                risk = sl - entry
            tp1 = entry - risk * 1.0
            target = entry - risk * 2.0
            for sl_ in sorted(sw_lows, reverse=True):
                if sl_ < entry - risk * 1.2 and sl_ > entry - risk * 4.0:
                    target = max(target, sl_)
                    break
            tp2 = target

        return sl, tp1, tp2
