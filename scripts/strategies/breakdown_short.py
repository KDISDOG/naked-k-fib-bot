"""
breakdown_short.py — Breakdown Short 策略（熊市趨勢做空）

核心邏輯：在下降趨勢中，價格跌破關鍵支撐位時放量做空，
順勢而為。與 NKF（回撤反彈）和 MR（震盪回歸）互補。

適用場景：
  - ADX 25-50 的趨勢幣（有方向性）
  - EMA20 < EMA50（空頭排列）
  - BTC 週線空頭（大盤環境配合）

選幣條件（滿分 10 分，>= 6 才入選）：
  - 流動性：USDT 24h 成交量 >= 500 萬        2 分
  - 空頭結構：EMA20 < EMA50 + 價格 < EMA50   2 分
  - 趨勢強度：ADX 25-50                       2 分
  - 下降波段：近期 swing high 遞降             2 分
  - 波動適中：ATR 1.5%-8%                     2 分

入場條件（只做空）：
  收盤跌破近 N 根最低點（支撐突破）
  + 放量確認（>= BD_VOL_MULT x 均量）
  + ADX > BD_ADX_MIN
  + 空頭 K 棒形態加分

止盈止損：
  TP1（60%）: Swing 1.272 Fib extension
  TP2（40%）: Swing 1.618 Fib extension
  SL: 突破點上方 + BD_SL_ATR_MULT x ATR
  超時：BD_TIMEOUT_BARS 根 K 棒後強制平倉
"""
import logging
import numpy as np
import pandas as pd
import pandas_ta as ta
from typing import Optional, List

from binance.client import Client
from .base_strategy import BaseStrategy, Signal

log = logging.getLogger("strategy.bd")


class BreakdownShortStrategy(BaseStrategy):

    def __init__(self, client: Client, market_ctx=None):
        self._client = client
        self._market_ctx = market_ctx

    @property
    def name(self) -> str:
        return "breakdown_short"

    @property
    def default_timeframe(self) -> str:
        from config import Config
        return Config.BD_TIMEFRAME

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
        """
        Breakdown Short 選幣：
        從候選幣中找空頭排列、趨勢明確的幣。
        """
        from config import Config
        selected = []
        for symbol in candidates:
            try:
                score = self._score_symbol(symbol, Config)
                if score >= 6:
                    selected.append(symbol)
                    log.debug(f"[BD 篩選] {symbol} 得分={score}")
            except Exception as e:
                log.debug(f"[BD 篩選] {symbol} 失敗: {e}")
        log.info(
            f"[BD] 選幣完成：{len(selected)} 支入選，"
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
        # 15m x 96 = 24h
        vol_24h = df["qav"].tail(96).sum()
        if vol_24h >= 5_000_000:
            score += 2
        elif vol_24h >= 2_000_000:
            score += 1
        else:
            return 0  # 流動性不足直接跳過

        # 2. 空頭結構：EMA20 < EMA50 + 價格 < EMA50
        ema20 = ta.ema(close, length=20)
        ema50 = ta.ema(close, length=50)
        if ema20 is None or ema50 is None:
            return 0
        ema20_val = float(ema20.iloc[-1])
        ema50_val = float(ema50.iloc[-1])
        price = float(close.iloc[-1])

        if ema20_val < ema50_val and price < ema50_val:
            score += 2  # 完美空頭排列
        elif ema20_val < ema50_val:
            score += 1  # EMA 交叉但價格未完全確認
        else:
            return 0  # 非空頭結構，直接排除

        # 3. 趨勢強度：ADX 25-50
        adx_df = ta.adx(df["high"], df["low"], close, length=14)
        if adx_df is None:
            return 0
        adx_val = float(adx_df["ADX_14"].iloc[-1])

        if Config.BD_ADX_MIN <= adx_val <= Config.BD_ADX_MAX:
            score += 2
        elif 20 <= adx_val < Config.BD_ADX_MIN:
            score += 1  # 弱趨勢但有方向
        else:
            return 0  # ADX 不在範圍內

        # 4. 下降波段：近期 swing high 遞降
        swings_h = self._find_swing_highs(df, left=10, right=10)
        if len(swings_h) >= 2:
            # 最近兩個 swing high 遞降 = 下降趨勢確認
            if swings_h[-1]["price"] < swings_h[-2]["price"]:
                score += 2
            else:
                score += 0  # swing high 未遞降，不加分
        elif len(swings_h) == 1:
            # 只有一個 swing high，至少價格在下方
            if price < swings_h[-1]["price"]:
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

        # BTC Dominance > 55% → 山寨流動性被吸乾（即使做空也易遇假突破反殺），扣 1 分
        if self._market_ctx and symbol != "BTCUSDT":
            try:
                if self._market_ctx.is_high_btc_dominance(threshold=55.0):
                    score -= 1
            except Exception:
                pass

        return score

    def _find_swing_highs(self, df: pd.DataFrame,
                          left: int = 5, right: int = 5) -> list:
        """找出所有 swing high"""
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
        """找出所有 swing low"""
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
        try:
            df = self._get_klines(symbol, self.default_timeframe, limit=200)
        except Exception as e:
            log.warning(f"[{symbol}] BD K 線取得失敗: {e}")
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

        # ── 基本條件：空頭結構 ───────────────────────────────────
        if ema20_val >= ema50_val:
            return None  # EMA 未空頭交叉
        if price >= ema50_val:
            return None  # 價格還在 EMA50 上方
        if adx_val < 20:
            return None  # 趨勢太弱

        # ── 突破偵測：收盤跌破近 N 根最低點 ─────────────────────
        lookback = Config.BD_LOOKBACK_BARS
        if len(df_a) < lookback + 1:
            return None

        # 近 N 根的最低點（不含當根）
        recent_lows = df_a["low"].iloc[-(lookback + 1):-1]
        support_level = float(recent_lows.min())

        # 當根收盤必須跌破支撐
        if price >= support_level:
            return None

        # 跌破幅度不能太小（避免假突破）
        break_pct = (support_level - price) / support_level
        if break_pct < 0.001:  # 至少跌破 0.1%
            return None

        # ── 放量確認 ─────────────────────────────────────────────
        avg_vol = float(df_a["volume"].tail(21).iloc[:-1].mean())
        last_vol = float(df_a["volume"].iloc[-1])
        vol_ok = last_vol >= avg_vol * Config.BD_VOL_MULT

        if not vol_ok:
            return None  # 突破無量，可能是假突破

        # ── 訊號評分 ─────────────────────────────────────────────
        score = self._score_signal(
            df_a, price, support_level, adx_val,
            ema20_val, ema50_val, avg_vol, last_vol, Config
        )

        if score < Config.BD_MIN_SCORE:
            log.debug(
                f"[{symbol}] BD 訊號強度 {score} < {Config.BD_MIN_SCORE}"
            )
            return None

        # ── 計算 TP/SL（Fib extension）───────────────────────────
        tp1, tp2, sl = self._calc_tp_sl(
            df_a, price, support_level, atr_val, Config
        )

        # BTC 相關性
        btc_corr = 0.0
        if self._market_ctx:
            c = self._market_ctx.btc_correlation(symbol)
            if c is not None:
                btc_corr = c

        sig = Signal(
            symbol        = symbol,
            side          = "SHORT",
            entry_price   = price,
            stop_loss     = sl,
            take_profit_1 = tp1,
            take_profit_2 = tp2,
            score         = score,
            strategy_name = self.name,
            timeframe     = self.default_timeframe,
            pattern       = "BD_BREAKDOWN",
            # 追蹤止盈：需 TRAILING_ENABLED=true 才生效；否則維持靜態 SL/TP1/TP2
            # 避免 UI 顯示「啟用追蹤」但後台根本沒推進的誤導情況
            use_trailing  = Config.TRAILING_ENABLED and adx_val > 35,
            trailing_atr  = atr_val,
            btc_corr      = btc_corr,
            metadata      = {
                "adx": round(adx_val, 2),
                "ema20": round(ema20_val, 4),
                "ema50": round(ema50_val, 4),
                "support": round(support_level, 4),
                "break_pct": round(break_pct * 100, 2),
                "vol_ratio": round(last_vol / avg_vol, 2)
                             if avg_vol > 0 else 0,
            },
        )

        if not self.validate_signal(sig):
            log.debug(f"[{symbol}] BD TP/SL 不合理，捨棄")
            return None

        log.info(
            f"[{symbol}] BD 訊號：SHORT 突破={support_level:.4f} "
            f"ADX={adx_val:.1f} 強度={score}"
        )
        return sig

    # ── 訊號評分 ─────────────────────────────────────────────────

    def _score_signal(self, df: pd.DataFrame, price: float,
                      support: float, adx_val: float,
                      ema20: float, ema50: float,
                      avg_vol: float, last_vol: float,
                      Config) -> int:
        """
        Breakdown Short 訊號評分（基礎分 1，滿分 5）

        +1 基礎（已通過突破 + 放量條件）
        +1 ADX 強趨勢（> 30）
        +1 巨量突破（> 2x 均量）
        +1 空頭 K 棒形態確認
        +1 BTC 週線空頭（大盤配合）
        """
        score = 1  # 基礎分

        # ADX 強趨勢
        if adx_val > 30:
            score += 1

        # 巨量突破
        if avg_vol > 0 and last_vol >= avg_vol * 2.0:
            score += 1

        # 空頭 K 棒形態（大陰線 / 射擊之星 / 空頭吞噬）
        if self._has_bearish_candle(df):
            score += 1

        # BTC 週線空頭
        if self._market_ctx:
            btc_bull = self._market_ctx.btc_weekly_bullish()
            if btc_bull is False:
                score += 1

        # MACD 空頭確認
        try:
            macd = ta.macd(df["close"])
            if macd is not None and macd.shape[1] >= 3:
                hist = float(macd.iloc[-1, 2])
                hist_prev = float(macd.iloc[-2, 2])
                # MACD 柱加速向下
                if hist < 0 and hist < hist_prev:
                    score += 1
        except Exception:
            pass

        return min(score, 5)

    def _has_bearish_candle(self, df: pd.DataFrame) -> bool:
        """偵測空頭 K 棒形態"""
        c = df.iloc[-1]
        body = abs(float(c["close"]) - float(c["open"]))
        total = float(c["high"]) - float(c["low"])

        if total <= 0:
            return False

        # 大陰線（實體占比 > 70%，且是陰線）
        if float(c["close"]) < float(c["open"]) and body / total > 0.70:
            return True

        # 射擊之星（長上影線）
        upper = float(c["high"]) - max(float(c["close"]), float(c["open"]))
        if body > 0 and upper >= body * 2:
            return True

        # 空頭吞噬
        if len(df) >= 2:
            prev = df.iloc[-2]
            if (float(prev["close"]) > float(prev["open"]) and
                    float(c["close"]) < float(c["open"]) and
                    float(c["open"]) >= float(prev["close"]) and
                    float(c["close"]) <= float(prev["open"])):
                return True

        # pandas-ta 形態
        try:
            engulf = ta.cdl_pattern(
                df["open"], df["high"], df["low"], df["close"],
                name="engulfing"
            )
            if engulf is not None and float(engulf.iloc[-1]) < 0:
                return True
        except Exception:
            pass

        return False

    # ── TP/SL 計算（Fib extension）──────────────────────────────

    def _calc_tp_sl(self, df: pd.DataFrame, entry: float,
                    support: float, atr_val: float,
                    Config) -> tuple[float, float, float]:
        """
        使用 Fib extension 計算做空目標：
          找最近的 swing high → swing low
          TP1 = swing_low - diff * 0.272 (1.272 extension)
          TP2 = swing_low - diff * 0.618 (1.618 extension)
          SL  = support + BD_SL_ATR_MULT * ATR
        """
        # 先算 SL（TP RR 保底要用到 SL 距離）
        # SL：突破點上方 + ATR 緩衝
        sl = support + Config.BD_SL_ATR_MULT * atr_val
        # SL 上限：不超過入場價 + 5%
        sl = min(sl, entry * 1.05)
        # SL 下限：至少在入場價上方 0.5%（避免被一根 K 棒的雜訊影線掃出場）
        sl = max(sl, entry * 1.005)
        sl_dist = sl - entry  # SHORT: sl > entry → sl_dist > 0

        # 找最近的 swing high 和 swing low
        swing_highs = self._find_swing_highs(df, left=10, right=10)
        swing_lows = self._find_swing_lows(df, left=10, right=10)

        # 時序驗證：做空要找「先 high 後 low」的下降結構
        # 若最新 swing 是 higher low（low 在 high 之後），則結構不是下跌 → fallback
        valid_fib = False
        if swing_highs and swing_lows:
            sh_obj = swing_highs[-1]
            sl_obj = swing_lows[-1]
            sh = sh_obj["price"]
            sl_swing = sl_obj["price"]
            # 條件：swing_high 的 idx 必須在 swing_low 之前（先漲後跌的結構）
            if sh_obj["idx"] < sl_obj["idx"] and sh > sl_swing:
                valid_fib = True

        if valid_fib:
            diff = sh - sl_swing
            tp1 = sl_swing - diff * 0.272  # 1.272 extension
            tp2 = sl_swing - diff * 0.618  # 1.618 extension
        else:
            # fallback：ATR 目標，加 RR 保底（TP1≥1.5R、TP2≥2.5R）
            # 避免震盪盤 ATR 小時 RR 撐不起最低 1.2 門檻
            tp1 = entry - max(atr_val * 1.5, sl_dist * 1.5)
            tp2 = entry - max(atr_val * 3.0, sl_dist * 2.5)

        # TP 合理性保護
        if tp1 >= entry:
            tp1 = entry * 0.985
        if tp2 >= tp1:
            tp2 = tp1 * 0.985

        return tp1, tp2, sl
