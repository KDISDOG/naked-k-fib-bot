"""
momentum_long.py — Momentum Breakout Long 策略（牛市趨勢做多）

核心邏輯：在上升趨勢中，價格突破關鍵阻力位時放量做多，
順勢而為。與 BD（做空突破）對稱，與 NKF（回撤）和 MR（震盪）互補。

適用場景：
  - ADX 25-50 的趨勢幣（有方向性）
  - EMA20 > EMA50（多頭排列）
  - BTC 週線多頭（大盤環境配合）

選幣條件（滿分 10 分，>= 6 才入選）：
  - 流動性：USDT 24h 成交量 >= 500 萬        2 分
  - 多頭結構：EMA20 > EMA50 + 價格 > EMA50   2 分
  - 趨勢強度：ADX 25-50                       2 分
  - 上升波段：近期 swing low 遞升              2 分
  - 波動適中：ATR 1.5%-8%                     2 分

入場條件（只做多）：
  收盤突破近 N 根最高點（阻力突破）
  + 放量確認（>= ML_VOL_MULT x 均量）
  + ADX > ML_ADX_MIN
  + 多頭 K 棒形態加分

止盈止損：
  TP1（60%）: Swing 1.272 Fib extension
  TP2（40%）: Swing 1.618 Fib extension
  SL: 突破點下方 - ML_SL_ATR_MULT x ATR
  超時：ML_TIMEOUT_BARS 根 K 棒後強制平倉
"""
import logging
import numpy as np
import pandas as pd
import pandas_ta as ta
from typing import Optional, List

from binance.client import Client
from .base_strategy import BaseStrategy, Signal

log = logging.getLogger("strategy.ml")


class MomentumLongStrategy(BaseStrategy):

    def __init__(self, client: Client, market_ctx=None):
        self._client = client
        self._market_ctx = market_ctx

    @property
    def name(self) -> str:
        return "momentum_long"

    @property
    def default_timeframe(self) -> str:
        from config import Config
        return Config.ML_TIMEFRAME

    # ── K 線取得 ─────────────────────────────────────────────────

    def _get_klines(self, symbol: str, interval: str,
                    limit: int = 200) -> pd.DataFrame:
        if self._market_ctx is not None and hasattr(self._market_ctx, "get_klines"):
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
                    log.debug(f"[ML 篩選] {symbol} 得分={score}")
            except Exception as e:
                log.debug(f"[ML 篩選] {symbol} 失敗: {e}")
        log.info(
            f"[ML] 選幣完成：{len(selected)} 支入選，"
            f"候選 {len(candidates)} 支"
        )
        return selected

    def _score_symbol(self, symbol: str, Config) -> int:
        tf = self.default_timeframe
        df = self._get_klines(symbol, tf, limit=200)
        if len(df) < 60:
            return 0

        score = 0
        close = df["close"]

        # 1. 流動性（24h USDT 成交量）
        vol_24h = df["qav"].tail(96).sum()
        if vol_24h >= 5_000_000:
            score += 2
        elif vol_24h >= 2_000_000:
            score += 1
        else:
            return 0

        # 2. 多頭結構：EMA20 > EMA50 + 價格 > EMA50
        ema20 = ta.ema(close, length=20)
        ema50 = ta.ema(close, length=50)
        if ema20 is None or ema50 is None:
            return 0
        ema20_val = float(ema20.iloc[-1])
        ema50_val = float(ema50.iloc[-1])
        price = float(close.iloc[-1])

        if ema20_val > ema50_val and price > ema50_val:
            score += 2  # 完美多頭排列
        elif ema20_val > ema50_val:
            score += 1  # EMA 交叉但價格未完全確認
        else:
            return 0  # 非多頭結構，直接排除

        # 3. 趨勢強度：ADX 25-50
        adx_df = ta.adx(df["high"], df["low"], close, length=14)
        if adx_df is None:
            return 0
        adx_val = float(adx_df["ADX_14"].iloc[-1])

        if Config.ML_ADX_MIN <= adx_val <= Config.ML_ADX_MAX:
            score += 2  # 25-50 甜蜜區
        elif 20 <= adx_val < Config.ML_ADX_MIN:
            score += 1  # 20-25 弱但有方向
        elif Config.ML_ADX_MAX < adx_val <= Config.ML_ADX_EXTREME:
            score += 1  # 50-65 強動能（突破類仍可做，但過熱不加 2 分）
        else:
            return 0  # ADX <20 或 >65

        # 4. 上升波段：近期 swing low 遞升
        swings_l = self._find_swing_lows(df, left=10, right=10)
        if len(swings_l) >= 2:
            if swings_l[-1]["price"] > swings_l[-2]["price"]:
                score += 2  # Higher lows = 上升趨勢確認
        elif len(swings_l) == 1:
            if price > swings_l[-1]["price"]:
                score += 1

        # 5. 波動適中：ATR 1.5%-8%
        atr = ta.atr(df["high"], df["low"], close, length=14)
        if atr is not None and not atr.empty:
            atr_pct = float(atr.iloc[-1]) / price * 100
            if 1.5 <= atr_pct <= 8.0:
                score += 2
            elif 1.0 <= atr_pct < 1.5:
                score += 1

        # 過濾：近 24h 跳空 > 5%（異常事件）
        recent = df.tail(96)
        gaps = (
            (recent["open"] - recent["close"].shift(1)).abs()
            / recent["close"].shift(1)
        ).dropna()
        if len(gaps) > 0 and float(gaps.max()) > 0.05:
            return 0

        # BTC Dominance > 55% → 山寨做多環境惡化（資金集中 BTC），ML 扣 2 分
        # ML 靠突破動能，BTC 獨強時山寨會被吸乾流動性、假突破率升高
        if self._market_ctx and symbol != "BTCUSDT":
            try:
                if self._market_ctx.is_high_btc_dominance(threshold=55.0):
                    score -= 2
            except Exception:
                pass

        # 相對強弱（做多方向）：個幣 24h 跑贏 BTC ≥ MIN_DIFF → +1；
        # 跑輸 BTC ≥ MIN_DIFF → -1（無相對強度支撐的多單，易被震盪洗）
        # 選幣層擋一層，signal 層 ML_REL_STRENGTH_MIN_DIFF 再擋一層，雙保險
        if self._market_ctx and symbol != "BTCUSDT" and \
                getattr(Config, "SCREEN_REL_STRENGTH_ENABLED", True):
            try:
                coin_pct = self._market_ctx.price_change_pct_24h(symbol)
                btc_pct = self._market_ctx.btc_change_pct_24h()
                if coin_pct is not None and btc_pct is not None:
                    diff = coin_pct - btc_pct
                    min_diff = float(
                        getattr(Config, "SCREEN_REL_STRENGTH_MIN_DIFF", 1.0)
                    )
                    if diff >= min_diff:
                        score += 1
                    elif diff <= -min_diff:
                        score -= 1
            except Exception:
                pass

        # 量能趨勢：近 6 根 vs 前 18 根均量
        # 放量 ≥1.2x → +1（突破/上漲放量確認）；縮量 ≤0.7x → -1
        try:
            if len(df) >= 24:
                recent_qav = float(df["qav"].tail(6).mean())
                prev_qav = float(df["qav"].iloc[-24:-6].mean())
                if prev_qav > 0:
                    vratio = recent_qav / prev_qav
                    if vratio >= 1.2:
                        score += 1
                    elif vratio <= 0.7:
                        score -= 1
        except Exception:
            pass

        return score

    def _find_swing_highs(self, df: pd.DataFrame,
                          left: int = 5, right: int = 5) -> list:
        swings = []
        for i in range(left, len(df) - right):
            window = df["high"].iloc[i - left:i + right + 1]
            if df["high"].iloc[i] == window.max():
                swings.append({
                    "idx": i,
                    "price": float(df["high"].iloc[i]),
                })
        return swings

    def _find_swing_lows(self, df: pd.DataFrame,
                         left: int = 5, right: int = 5) -> list:
        swings = []
        for i in range(left, len(df) - right):
            window = df["low"].iloc[i - left:i + right + 1]
            if df["low"].iloc[i] == window.min():
                swings.append({
                    "idx": i,
                    "price": float(df["low"].iloc[i]),
                })
        return swings

    # ── 訊號偵測 ─────────────────────────────────────────────────

    def check_signal(self, symbol: str) -> Optional[Signal]:
        from config import Config
        # ── Regime gate：ML 只在 TREND_UP 放行 ─────────────────
        if self._market_ctx and getattr(Config, "REGIME_GATE_ENABLED", True):
            try:
                if not self._market_ctx.regime_allows("momentum_long"):
                    log.debug(
                        f"[{symbol}] ML 被 regime "
                        f"{self._market_ctx.current_regime()} 阻擋"
                    )
                    return None
            except Exception:
                pass

        # ── B1 相對強度過濾（實證：alt 弱於 BTC 時做多必輸） ──
        # 個幣 24h 漲幅必須強於 BTC 至少 ML_REL_STRENGTH_MIN_DIFF %
        if self._market_ctx and getattr(
            Config, "ML_REL_STRENGTH_ENABLED", True
        ):
            try:
                coin_pct = self._market_ctx.price_change_pct_24h(symbol)
                btc_pct  = self._market_ctx.btc_change_pct_24h()
                if coin_pct is not None and btc_pct is not None:
                    diff = coin_pct - btc_pct
                    min_diff = Config.ML_REL_STRENGTH_MIN_DIFF
                    if diff < min_diff:
                        log.debug(
                            f"[{symbol}] ML 相對強度不足："
                            f"coin 24h={coin_pct:+.2f}% BTC={btc_pct:+.2f}% "
                            f"diff={diff:+.2f}% < {min_diff}%"
                        )
                        return None
            except Exception as e:
                log.debug(f"[{symbol}] ML 相對強度檢查失敗（略過）: {e}")

        try:
            df = self._get_klines(symbol, self.default_timeframe, limit=200)
        except Exception as e:
            log.warning(f"[{symbol}] ML K 線取得失敗: {e}")
            return None

        if len(df) < 60:
            return None

        # 使用倒數第二根（已收盤確認）
        df_a = df.iloc[:-1].copy().reset_index(drop=True)
        latest = df_a.iloc[-1]
        price = float(latest["close"])

        # ── 計算指標 ─────────────────────────────────────────────
        ema20 = ta.ema(df_a["close"], length=20)
        ema50 = ta.ema(df_a["close"], length=50)
        adx_df = ta.adx(df_a["high"], df_a["low"], df_a["close"], length=14)
        atr_s = ta.atr(df_a["high"], df_a["low"], df_a["close"], length=14)

        if ema20 is None or ema50 is None or adx_df is None or atr_s is None:
            return None

        ema20_val = float(ema20.iloc[-1])
        ema50_val = float(ema50.iloc[-1])
        adx_val = float(adx_df["ADX_14"].iloc[-1])
        atr_val = float(atr_s.iloc[-1])

        # ── 基本條件：多頭結構 ───────────────────────────────────
        if ema20_val <= ema50_val:
            return None  # EMA 未多頭交叉
        if price <= ema50_val:
            return None  # 價格還在 EMA50 下方
        if adx_val < 20:
            return None  # 趨勢太弱

        # ── 突破偵測：收盤突破近 N 根最高點 ─────────────────────
        lookback = Config.ML_LOOKBACK_BARS
        if len(df_a) < lookback + 1:
            return None

        # 近 N 根的最高點（不含當根）
        recent_highs = df_a["high"].iloc[-(lookback + 1):-1]
        resistance_level = float(recent_highs.max())

        avg_vol = float(df_a["volume"].tail(21).iloc[:-1].mean())
        last_vol = float(df_a["volume"].iloc[-1])

        # ── 標準路徑：突破 + 放量 ──────────────────────────────
        breakout_ok = price > resistance_level and \
                      (price - resistance_level) / resistance_level >= 0.001
        vol_ok = last_vol >= avg_vol * Config.ML_VOL_MULT

        path_breakout = breakout_ok and vol_ok

        # ── ML v2 路徑：Volume Burst 續勢（無需突破）────────────
        path_burst = False
        if (getattr(Config, "ML_V2_ENABLED", False)
                and getattr(Config, "ML_V2_VOL_BURST_ENABLED", True)):
            burst_mult     = float(getattr(Config, "ML_V2_VOL_BURST_MULT", 3.0))
            close_pct_min  = float(getattr(Config, "ML_V2_VOL_BURST_CLOSE_PCT", 0.7))
            cur_high   = float(latest["high"])
            cur_low    = float(latest["low"])
            cur_range  = cur_high - cur_low
            close_pos  = (price - cur_low) / cur_range if cur_range > 0 else 0
            if last_vol >= avg_vol * burst_mult and close_pos >= close_pct_min:
                # close 在上 30%、vol >= 3× 均量、price > EMA20（已驗證）
                path_burst = True
                log.debug(
                    f"[{symbol}] ML v2 Volume Burst：vol/avg="
                    f"{last_vol/avg_vol:.1f}× close@{close_pos*100:.0f}%"
                )

        if not (path_breakout or path_burst):
            return None  # 既非突破也非爆量續勢

        # ── ML v2 HTF (4h EMA50) 趨勢過濾 ───────────────────────
        if getattr(Config, "ML_V2_ENABLED", False) and \
                getattr(Config, "ML_V2_HTF_ENABLED", True):
            try:
                htf_tf     = getattr(Config, "ML_V2_HTF_TIMEFRAME", "4h")
                htf_period = int(getattr(Config, "ML_V2_HTF_EMA_PERIOD", 50))
                slope_bars = int(getattr(Config, "ML_V2_HTF_SLOPE_BARS", 5))
                min_slope  = float(getattr(Config, "ML_V2_HTF_MIN_SLOPE_PCT", 0.003))
                df_htf = self._get_klines(
                    symbol, htf_tf, limit=htf_period + slope_bars + 30
                )
                if len(df_htf) >= htf_period + slope_bars + 3:
                    htf_ema = ta.ema(df_htf["close"], length=htf_period)
                    htf_close = float(df_htf["close"].iloc[-2])
                    htf_ema_v = float(htf_ema.iloc[-2])
                    htf_ema_past = float(htf_ema.iloc[-2 - slope_bars])
                    if not (pd.isna(htf_close) or pd.isna(htf_ema_v) or
                            pd.isna(htf_ema_past)) and htf_ema_past > 0:
                        # close 必須在 EMA 上方
                        if htf_close <= htf_ema_v:
                            log.debug(
                                f"[{symbol}] ML v2 HTF 過濾："
                                f"4h close={htf_close:.4f} ≤ EMA={htf_ema_v:.4f}"
                            )
                            return None
                        # EMA 必須有上行強度
                        slope_pct = (htf_ema_v - htf_ema_past) / htf_ema_past
                        if slope_pct < min_slope:
                            log.debug(
                                f"[{symbol}] ML v2 HTF 斜率不足："
                                f"slope={slope_pct*100:+.3f}% < {min_slope*100:.2f}%"
                            )
                            return None
            except Exception as e:
                log.debug(f"[{symbol}] ML v2 HTF 檢查失敗（fail-open）: {e}")

        # 後續流程沿用既有邏輯（vol_ok 給 score 計算用）
        if not vol_ok:
            vol_ok = last_vol >= avg_vol * 1.0  # burst 路徑允許量能標準放寬

        # ── 訊號評分 ─────────────────────────────────────────────
        score = self._score_signal(
            df_a, price, resistance_level, adx_val,
            ema20_val, ema50_val, avg_vol, last_vol, Config
        )

        # Funding rate 方向性加分（ML 永遠 LONG）
        try:
            from funding_bias import funding_bonus
            fb = funding_bonus(self._client, symbol, "LONG")
            if fb != 0:
                log.debug(f"[{symbol}] ML funding bonus={fb:+d}")
            score = max(1, min(score + fb, 5))
        except Exception:
            pass

        if score < Config.ML_MIN_SCORE:
            log.debug(
                f"[{symbol}] ML 訊號強度 {score} < {Config.ML_MIN_SCORE}"
            )
            return None

        # ── B3 高 Score 過熱過濾（實證：score=5 WR 僅 14.3%） ──
        # score 越高勝率越差 → 視為「趨勢末端 / 追高」訊號，擋掉
        max_score = getattr(Config, "ML_MAX_SCORE", 4)
        if score > max_score:
            log.debug(
                f"[{symbol}] ML 訊號過熱：score={score} > {max_score}（擋）"
            )
            return None

        # ── 計算 TP/SL（Fib extension）───────────────────────────
        tp1, tp2, sl = self._calc_tp_sl(
            df_a, price, resistance_level, atr_val, Config
        )

        # BTC 相關性
        btc_corr = 0.0
        if self._market_ctx:
            c = self._market_ctx.btc_correlation(symbol)
            if c is not None:
                btc_corr = c

        sig = Signal(
            symbol        = symbol,
            side          = "LONG",
            entry_price   = price,
            stop_loss     = sl,
            take_profit_1 = tp1,
            take_profit_2 = tp2,
            score         = score,
            strategy_name = self.name,
            timeframe     = self.default_timeframe,
            pattern       = "ML_BREAKOUT",
            # 追蹤止盈：需總開關 + ML 專屬開關 + ADX>35 才啟用
            use_trailing  = (Config.TRAILING_ENABLED
                             and Config.TRAILING_ML_ENABLED
                             and adx_val > 35),
            trailing_atr  = atr_val,
            btc_corr      = btc_corr,
            metadata      = {
                "adx": round(adx_val, 2),
                "ema20": round(ema20_val, 4),
                "ema50": round(ema50_val, 4),
                "resistance": round(resistance_level, 4),
                "break_pct": round(break_pct * 100, 2),
                "vol_ratio": round(last_vol / avg_vol, 2)
                             if avg_vol > 0 else 0,
            },
        )

        if not self.validate_signal(sig):
            log.debug(f"[{symbol}] ML TP/SL 不合理，捨棄")
            return None

        log.info(
            f"[{symbol}] ML 訊號：LONG 突破={resistance_level:.4f} "
            f"ADX={adx_val:.1f} 強度={score}"
        )
        return sig

    # ── 訊號評分 ─────────────────────────────────────────────────

    def _score_signal(self, df: pd.DataFrame, price: float,
                      resistance: float, adx_val: float,
                      ema20: float, ema50: float,
                      avg_vol: float, last_vol: float,
                      Config) -> int:
        """
        Momentum Long 訊號評分（基礎分 1，範圍 1-5）

        加分項：
          +1 基礎（已通過突破 + 放量條件）
          +1 ADX 強趨勢（> 30）
          +1 巨量突破（> 2x 均量）
          +1 多頭 K 棒形態確認
          +1 BTC 週線多頭（大盤配合）
          +1 MACD 多頭確認

        扣分項（exhaustion / 追高懲罰，修正 DB 觀察到的
        score=5 反而勝率最差的問題：score=5 = 全部加分觸發 =
        典型 blowoff 追高，常被 fade）：
          -1 價格離 EMA20 過遠（> 3%，追高溢價）
          -1 突破幅度過大（> 2%，gap-past-resistance，FOMO 追單）
          -1 RSI 過熱（> 75，已超買）
        """
        score = 1  # 基礎分

        # ── 加分項 ───────────────────────────────────────
        # ADX 強趨勢
        if adx_val > 30:
            score += 1

        # 巨量突破
        if avg_vol > 0 and last_vol >= avg_vol * 2.0:
            score += 1

        # 多頭 K 棒形態（大陽線 / 錘子 / 多頭吞噬）
        if self._has_bullish_candle(df):
            score += 1

        # BTC 週線多頭
        if self._market_ctx:
            btc_bull = self._market_ctx.btc_weekly_bullish()
            if btc_bull is True:
                score += 1

        # MACD 多頭確認
        try:
            macd = ta.macd(df["close"])
            if macd is not None and macd.shape[1] >= 3:
                hist = float(macd.iloc[-1, 2])
                hist_prev = float(macd.iloc[-2, 2])
                # MACD 柱加速向上
                if hist > 0 and hist > hist_prev:
                    score += 1
        except Exception:
            pass

        # ── 扣分項：exhaustion / 追高懲罰 ────────────────
        # 離 EMA20 過遠（chase premium）
        if ema20 > 0 and (price - ema20) / ema20 > 0.03:
            score -= 1

        # 突破幅度過大（FOMO gap，往往是 1 根暴拉、隨後套頂）
        if resistance > 0 and (price - resistance) / resistance > 0.02:
            score -= 1

        # RSI 過熱（已超買，追單風險高）
        try:
            rsi_s = ta.rsi(df["close"], length=14)
            if rsi_s is not None and not rsi_s.empty:
                rsi_val = float(rsi_s.iloc[-1])
                if rsi_val > 75:
                    score -= 1
        except Exception:
            pass

        # 範圍限制：[1, 5]（扣分不讓它變 0 避免所有訊號被排除）
        return max(1, min(score, 5))

    def _has_bullish_candle(self, df: pd.DataFrame) -> bool:
        """偵測多頭 K 棒形態"""
        c = df.iloc[-1]
        body = abs(float(c["close"]) - float(c["open"]))
        total = float(c["high"]) - float(c["low"])

        if total <= 0:
            return False

        # 大陽線（實體占比 > 70%，且是陽線）
        if float(c["close"]) > float(c["open"]) and body / total > 0.70:
            return True

        # 錘子（長下影線）
        lower = min(float(c["close"]), float(c["open"])) - float(c["low"])
        if body > 0 and lower >= body * 2:
            return True

        # 多頭吞噬
        if len(df) >= 2:
            prev = df.iloc[-2]
            if (float(prev["close"]) < float(prev["open"]) and
                    float(c["close"]) > float(c["open"]) and
                    float(c["open"]) <= float(prev["close"]) and
                    float(c["close"]) >= float(prev["open"])):
                return True

        # pandas-ta 形態
        try:
            engulf = ta.cdl_pattern(
                df["open"], df["high"], df["low"], df["close"],
                name="engulfing"
            )
            if engulf is not None and float(engulf.iloc[-1]) > 0:
                return True
        except Exception:
            pass

        return False

    # ── TP/SL 計算（Fib extension）──────────────────────────────

    def _calc_tp_sl(self, df: pd.DataFrame, entry: float,
                    resistance: float, atr_val: float,
                    Config) -> tuple[float, float, float]:
        """
        使用 Fib extension 計算做多目標：
          找最近的 swing low → swing high
          TP1 = swing_high + diff * 0.272 (1.272 extension)
          TP2 = swing_high + diff * ML_TP2_FIB_MULT (預設 1.382)
          SL  = resistance - ML_SL_ATR_MULT * ATR
        """
        # 先算 SL（TP RR 保底要用到 SL 距離）
        # SL：突破點下方 - ATR 緩衝
        sl = resistance - Config.ML_SL_ATR_MULT * atr_val
        # SL 下限：不低於入場價 - 5%
        sl = max(sl, entry * 0.95)
        # SL 上限：至少在入場價下方 0.5%（避免被一根 K 棒的雜訊影線掃出場）
        sl = min(sl, entry * 0.995)
        sl_dist = entry - sl  # LONG: sl < entry → sl_dist > 0

        swing_highs = self._find_swing_highs(df, left=10, right=10)
        swing_lows = self._find_swing_lows(df, left=10, right=10)

        # 時序驗證：做多要找「先 low 後 high」的上升結構
        valid_fib = False
        if swing_highs and swing_lows:
            sh_obj = swing_highs[-1]
            sl_obj = swing_lows[-1]
            sh = sh_obj["price"]
            sl_swing = sl_obj["price"]
            # 條件：swing_low 的 idx 必須在 swing_high 之前（先跌後漲的結構）
            if sl_obj["idx"] < sh_obj["idx"] and sh > sl_swing:
                valid_fib = True

        if valid_fib:
            diff = sh - sl_swing
            tp1 = sh + diff * 0.272  # 1.272 extension
            tp2 = sh + diff * Config.ML_TP2_FIB_MULT  # 預設 1.382
        else:
            # fallback：ATR 目標，加 RR 保底（TP1≥1.5R、TP2≥2.5R）
            # 避免震盪盤 ATR 小時 RR 撐不起最低 1.2 門檻
            tp1 = entry + max(atr_val * 1.5, sl_dist * 1.5)
            tp2 = entry + max(atr_val * 3.0, sl_dist * 2.5)

        # TP 合理性保護
        if tp1 <= entry:
            tp1 = entry * 1.015
        if tp2 <= tp1:
            tp2 = tp1 * 1.015

        return tp1, tp2, sl
