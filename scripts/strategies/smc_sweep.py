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

        # ── 個別幣排除（v4）─────────────────────────────────────
        # 回測證據：HYPEUSDT 等高 gap risk 幣 SL 平均虧 33% margin（極端）
        # 這類結構性失敗無法靠技術過濾解，直接排除最划算
        excluded = getattr(Config, "SMC_EXCLUDED_SYMBOLS", "")
        if excluded:
            ex_set = {s.strip().upper() for s in excluded.split(",") if s.strip()}
            if symbol.upper() in ex_set:
                log.debug(f"[{symbol}] SMC 在排除清單，跳過")
                return None

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

        # 用倒數第二根作為「觸發 K 棒」(confirmation)，倒數第三根才是 sweep
        # v2 修正：sweep 在前一根 → 等下一根確認方向 → 才入場（避免接刀）
        df_a = df.iloc[:-1].copy().reset_index(drop=True)
        if len(df_a) < 50:
            return None

        # confirmation candle (i)：當前要進場的 K 棒
        trig = df_a.iloc[-1]
        # sweep candle (i-1)：發生 sweep + 反轉 K 棒形態的那根
        sweep = df_a.iloc[-2]
        # sweep 之前一根（給 engulfing 比較用）
        prev_sweep = df_a.iloc[-3] if len(df_a) >= 3 else None

        cur_close = float(trig["close"])
        sw_low_p  = float(sweep["low"])
        sw_high_p = float(sweep["high"])
        sw_close  = float(sweep["close"])
        sw_vol    = float(sweep["volume"])

        # 量能基準：sweep 之前 20 根均量
        avg_vol = float(df_a["volume"].iloc[-22:-2].mean())
        if avg_vol <= 0:
            return None
        vol_ok = sw_vol >= avg_vol * Config.SMC_VOL_MULT

        # ── 找 swing low / high（在 sweep 之前的 lookback 範圍）──
        lookback = int(Config.SMC_SWING_LOOKBACK)
        right_buffer = int(Config.SMC_SWING_RIGHT)
        # 結束位置必須在 sweep 之前 right_buffer 根（fractal 確認）
        end_idx = len(df_a) - 2 - right_buffer
        start_idx = max(0, end_idx - lookback)
        if end_idx - start_idx < 5:
            return None

        win = df_a.iloc[start_idx:end_idx + 1]
        left = int(Config.SMC_SWING_LEFT)
        sw_lows  = self._find_fractal_levels(win, "low", left, right_buffer)
        sw_highs = self._find_fractal_levels(win, "high", left, right_buffer)

        if not sw_lows and not sw_highs:
            return None

        last_sw_low  = sw_lows[-1] if sw_lows else None
        last_sw_high = sw_highs[-1] if sw_highs else None

        side: Optional[str] = None
        sweep_level: float = 0.0
        sweep_min = float(Config.SMC_SWEEP_MIN_PCT)
        sweep_max = float(Config.SMC_SWEEP_MAX_PCT)

        # ── LONG：sweep swing low + reversal candle + 確認上行 ──
        if last_sw_low is not None and last_sw_low > 0:
            sweep_pct = (last_sw_low - sw_low_p) / last_sw_low
            if (sweep_min <= sweep_pct <= sweep_max
                    and sw_close > last_sw_low                      # sweep 收盤回到上方
                    and self._is_strong_bullish_reversal(sweep, prev_sweep)
                    and vol_ok
                    and cur_close > sw_close):                      # confirmation：續漲
                side = "LONG"
                sweep_level = last_sw_low

        # ── SHORT：sweep swing high + reversal candle + 確認下行 ──
        if side is None and last_sw_high is not None and last_sw_high > 0:
            sweep_pct = (sw_high_p - last_sw_high) / last_sw_high
            if (sweep_min <= sweep_pct <= sweep_max
                    and sw_close < last_sw_high
                    and self._is_strong_bearish_reversal(sweep, prev_sweep)
                    and vol_ok
                    and cur_close < sw_close):                      # confirmation：續跌
                side = "SHORT"
                sweep_level = last_sw_high

        if side is None:
            return None

        # ── HTF（4h EMA50）趨勢過濾（v3+v4+v5）─────────────────
        # v3：要求 sweep 方向與 4h 大趨勢同向（close vs EMA 位置）
        # v4：再加「離 EMA 至少 X%」過濾貼 EMA 的 chop
        # v5：再加 EMA 斜率方向確認（趨勢正在進行中，不是末段反彈）
        if getattr(Config, "SMC_HTF_FILTER_ENABLED", True):
            try:
                htf_tf     = getattr(Config, "SMC_HTF_TIMEFRAME", "4h")
                htf_period = int(getattr(Config, "SMC_HTF_EMA_PERIOD", 50))
                min_dist   = float(getattr(Config, "SMC_HTF_MIN_DISTANCE_PCT", 0.005))
                req_slope  = bool(getattr(Config, "SMC_HTF_REQUIRE_SLOPE", True))
                slope_bars = int(getattr(Config, "SMC_HTF_SLOPE_BARS", 5))
                df_htf = self._get_klines(
                    symbol, htf_tf,
                    limit=htf_period + slope_bars + 30,
                )
                if len(df_htf) >= htf_period + slope_bars + 3:
                    htf_ema = ta.ema(df_htf["close"], length=htf_period)
                    htf_close = float(df_htf["close"].iloc[-2])
                    htf_ema_v = float(htf_ema.iloc[-2]) if htf_ema is not None else float("nan")
                    if not (pd.isna(htf_close) or pd.isna(htf_ema_v)) and htf_ema_v > 0:
                        upper_thr = htf_ema_v * (1 + min_dist)
                        lower_thr = htf_ema_v * (1 - min_dist)
                        if side == "LONG" and htf_close < upper_thr:
                            log.debug(
                                f"[{symbol}] SMC LONG HTF 距離過濾："
                                f"4h close={htf_close:.4f} < EMA×1.005={upper_thr:.4f}"
                            )
                            return None
                        if side == "SHORT" and htf_close > lower_thr:
                            log.debug(
                                f"[{symbol}] SMC SHORT HTF 距離過濾："
                                f"4h close={htf_close:.4f} > EMA×0.995={lower_thr:.4f}"
                            )
                            return None

                        # v5 斜率方向 + v6 斜率強度
                        if req_slope:
                            ema_now = htf_ema_v
                            ema_past = float(htf_ema.iloc[-2 - slope_bars])
                            min_slope = float(
                                getattr(Config, "SMC_HTF_MIN_SLOPE_PCT", 0.005)
                            )
                            if not pd.isna(ema_past) and ema_past > 0:
                                slope_pct = (ema_now - ema_past) / ema_past
                                # LONG 要求 EMA 上升 ≥ min_slope
                                # SHORT 要求 EMA 下降 ≥ min_slope（即 slope ≤ -min_slope）
                                if side == "LONG" and slope_pct < min_slope:
                                    log.debug(
                                        f"[{symbol}] SMC LONG HTF 斜率不足："
                                        f"EMA50 {slope_bars} 根 slope={slope_pct*100:+.3f}% "
                                        f"< {min_slope*100:.2f}%"
                                    )
                                    return None
                                if side == "SHORT" and slope_pct > -min_slope:
                                    log.debug(
                                        f"[{symbol}] SMC SHORT HTF 斜率不足："
                                        f"EMA50 {slope_bars} 根 slope={slope_pct*100:+.3f}% "
                                        f"> -{min_slope*100:.2f}%"
                                    )
                                    return None
            except Exception as e:
                log.debug(f"[{symbol}] SMC HTF 檢查失敗（fail-open，略過）: {e}")

        cur_high = float(trig["high"])
        cur_low  = float(trig["low"])
        cur_vol  = float(trig["volume"])

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

    # ── 反轉 K 棒嚴格判定（v2：解決原 39% win 主因之一）─────────
    # 三種型態擇一即視為反轉：
    #   (a) Pin bar / hammer: 反向影線 ≥ 50% range + close 在順向後 60%
    #   (b) 強實體：body ≥ 40% range + close 在順向後 60%
    #   (c) Engulfing：吞噬前一根 + body ≥ 40%
    @staticmethod
    def _is_strong_bullish_reversal(c, prev=None) -> bool:
        o = float(c["open"]); h = float(c["high"])
        l = float(c["low"]);  cl = float(c["close"])
        rng = h - l
        if rng <= 0:
            return False
        body = abs(cl - o)
        lower_wick = min(o, cl) - l
        body_ratio  = body / rng
        lower_ratio = lower_wick / rng
        close_in_upper = (cl - l) / rng  # 0.6+ = 在上 40%

        # (a) Pin bar
        if lower_ratio >= 0.5 and close_in_upper >= 0.6:
            return True
        # (b) 強實體陽線
        if cl > o and body_ratio >= 0.4 and close_in_upper >= 0.6:
            return True
        # (c) Bullish engulfing
        if prev is not None:
            po = float(prev["open"]); pcl = float(prev["close"])
            if pcl < po and cl > o and cl >= po and o <= pcl and body_ratio >= 0.4:
                return True
        return False

    @staticmethod
    def _is_strong_bearish_reversal(c, prev=None) -> bool:
        o = float(c["open"]); h = float(c["high"])
        l = float(c["low"]);  cl = float(c["close"])
        rng = h - l
        if rng <= 0:
            return False
        body = abs(cl - o)
        upper_wick = h - max(o, cl)
        body_ratio  = body / rng
        upper_ratio = upper_wick / rng
        close_in_lower = (h - cl) / rng

        # (a) Shooting star / hanging man
        if upper_ratio >= 0.5 and close_in_lower >= 0.6:
            return True
        # (b) 強實體陰線
        if cl < o and body_ratio >= 0.4 and close_in_lower >= 0.6:
            return True
        # (c) Bearish engulfing
        if prev is not None:
            po = float(prev["open"]); pcl = float(prev["close"])
            if pcl > po and cl < o and cl <= po and o >= pcl and body_ratio >= 0.4:
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
