"""
mean_reversion.py — 方案 A：RSI 均值回歸策略

核心邏輯：價格過度偏離均值（RSI 極端 + 突破布林帶）時反向開單，
並等待價格回歸布林中軌。與裸K+Fib 互補：在震盪行情中表現好。

選幣條件（滿分 10 分，≥ 6 才入選）：
  - 流動性：USDT 24h 成交量 ≥ 500 萬          2 分
  - 波動適中：ATR(14) 在 1.5%-4% 之間          2 分
  - 非趨勢：ADX(14) < 20 (+2) / < 25 (+1) / ≥ 25 直接淘汰（與 NKF 分離）
  - BB 帶寬適中：3%-15%                        2 分
  - 均值吸引：50 根 K 棒內觸及 BB 中軌 ≥ 5 次  2 分

入場條件（做多）：
  RSI ≤ MR_RSI_OVERSOLD(=25) + 收盤 ≤ BB 下軌 + 止跌K棒
  + 縮量（≤ MR_VOL_MULT(=0.9)x 均量）+ ADX < 20

止盈止損：
  TP1（60%）: +3%（動態調整） → TP2（40%）: +5%
  SL: -2.5%（幣價層面）
  超時：MR_TIMEOUT_BARS 根 K 棒後強制平倉
"""
import logging
import numpy as np
import pandas as pd
import pandas_ta as ta
from typing import Optional, List

from binance.client import Client
from .base_strategy import BaseStrategy, Signal

log = logging.getLogger("strategy.mr")


class MeanReversionStrategy(BaseStrategy):

    def __init__(self, client: Client, market_ctx=None):
        self._client     = client
        self._market_ctx = market_ctx

    @property
    def name(self) -> str:
        return "mean_reversion"

    @property
    def default_timeframe(self) -> str:
        from config import Config
        return Config.MR_TIMEFRAME

    # ── K 線取得 ─────────────────────────────────────────────────

    def _get_klines(self, symbol: str, interval: str,
                    limit: int = 200) -> pd.DataFrame:
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
        均值回歸選幣：從候選幣中找 ADX 低、BB 回歸性好的震盪幣。
        candidates: 全市場 USDT 合約 symbol 列表
        """
        from config import Config
        selected = []
        for symbol in candidates:
            try:
                score = self._score_symbol(symbol, Config)
                if score >= 6:
                    selected.append(symbol)
                    log.debug(f"[MR篩選] {symbol} 得分={score}")
            except Exception as e:
                log.debug(f"[MR篩選] {symbol} 失敗: {e}")
        log.info(f"[MR] 選幣完成：{len(selected)} 支入選，候選 {len(candidates)} 支")
        return selected

    def _score_symbol(self, symbol: str, Config) -> int:
        tf = self.default_timeframe
        df = self._get_klines(symbol, tf, limit=200)
        if len(df) < 60:
            return 0

        score = 0

        # 1. 流動性（24h USDT 成交量，用 qav）
        vol_24h = df["qav"].tail(96).sum()  # 15m × 96 = 24h
        if vol_24h >= 5_000_000:
            score += 2
        elif vol_24h >= 2_000_000:
            score += 1
        else:
            return 0  # 流動性不足直接跳過

        # 2. ATR 在 1.5%-4%
        atr = ta.atr(df["high"], df["low"], df["close"], length=14)
        atr_pct = float(atr.iloc[-1]) / float(df["close"].iloc[-1]) * 100
        if 1.5 <= atr_pct <= 4.0:
            score += 2
        elif 1.0 <= atr_pct < 1.5:
            score += 1

        # 3. ADX < 25（非趨勢；與 NKF 25-45 區間分開，避免重疊）
        adx_df = ta.adx(df["high"], df["low"], df["close"], length=14)
        adx_val = float(adx_df["ADX_14"].iloc[-1])
        if adx_val < 20:
            score += 2
        elif adx_val < 25:
            score += 1
        else:
            return 0  # ADX ≥ 25 直接排除，讓給 NKF

        # 4. BB 帶寬 3%-15%
        bb = ta.bbands(df["close"], length=Config.MR_BB_PERIOD,
                       std=Config.MR_BB_STD)
        if bb is None or bb.empty:
            return score
        col_u = next((c for c in bb.columns if c.startswith("BBU_")), None)
        col_l = next((c for c in bb.columns if c.startswith("BBL_")), None)
        col_m = next((c for c in bb.columns if c.startswith("BBM_")), None)
        if not col_u or not col_l or not col_m:
            return score
        bb_width = float(bb[col_u].iloc[-1] - bb[col_l].iloc[-1])
        bb_mid   = float(bb[col_m].iloc[-1])
        bw_pct   = bb_width / bb_mid * 100 if bb_mid > 0 else 0
        if 3.0 <= bw_pct <= 15.0:
            score += 2
        elif 1.5 <= bw_pct < 3.0:
            score += 1

        # 5. 近 50 根觸及 BB 中軌次數 ≥ 5（均值吸引）
        mid_prices = bb[col_m].tail(50)
        closes     = df["close"].tail(50)
        # 以 BB 帶寬的 10% 作為觸及容差（低波動幣種也能偵測到均值吸引）
        tol = bb_width * 0.1
        touches = int(np.sum(np.abs(closes.values - mid_prices.values) <= tol))
        if touches >= 5:
            score += 2
        elif touches >= 3:
            score += 1

        # 過濾：近 24h 跳空 > 5%（可能有大事件）
        recent_candles = df.tail(96)
        max_gap = float(
            ((recent_candles["open"] - recent_candles["close"].shift(1))
             .abs() / recent_candles["close"].shift(1))
            .dropna().max()
        )
        if max_gap > 0.05:
            return 0

        return score

    # ── 訊號偵測 ─────────────────────────────────────────────────

    def check_signal(self, symbol: str) -> Optional[Signal]:
        from config import Config
        try:
            df = self._get_klines(symbol, self.default_timeframe, limit=200)
        except Exception as e:
            log.warning(f"[{symbol}] MR K 線取得失敗: {e}")
            return None

        if len(df) < 50:
            return None

        # 使用倒數第二根（已收盤確認）
        df_a = df.iloc[:-1].copy().reset_index(drop=True)
        latest = df_a.iloc[-1]

        # ── 計算指標 ─────────────────────────────────────────────
        rsi = ta.rsi(df_a["close"], length=Config.MR_RSI_PERIOD)
        bb  = ta.bbands(df_a["close"], length=Config.MR_BB_PERIOD,
                        std=Config.MR_BB_STD)
        adx = ta.adx(df_a["high"], df_a["low"], df_a["close"], length=14)
        atr_s = ta.atr(df_a["high"], df_a["low"], df_a["close"], length=14)

        if rsi is None or bb is None or adx is None or atr_s is None:
            return None
        atr_val = float(atr_s.iloc[-1])

        # 自動偵測 pandas_ta bbands 欄位名稱
        col_u = next((c for c in bb.columns if c.startswith("BBU_")), None)
        col_l = next((c for c in bb.columns if c.startswith("BBL_")), None)
        col_m = next((c for c in bb.columns if c.startswith("BBM_")), None)
        if not col_u or not col_l or not col_m:
            return None

        rsi_val  = float(rsi.iloc[-1])
        adx_val  = float(adx["ADX_14"].iloc[-1])
        bb_upper = float(bb[col_u].iloc[-1])
        bb_lower = float(bb[col_l].iloc[-1])
        bb_mid   = float(bb[col_m].iloc[-1])
        price    = float(latest["close"])

        # 成交量確認（縮量）
        avg_vol = float(df_a["volume"].tail(21).iloc[:-1].mean())
        last_vol = float(df_a["volume"].iloc[-1])
        vol_ok = last_vol <= avg_vol * Config.MR_VOL_MULT

        # ADX 過濾（MR 只在非趨勢盤運作；與 NKF 的 20-45 區間分開）
        adx_ok = adx_val < 20

        side = None

        # ── 做多判斷 ─────────────────────────────────────────────
        if (rsi_val <= Config.MR_RSI_OVERSOLD and
                price <= bb_lower and adx_ok):
            if vol_ok and self._has_reversal_candle(df_a, "LONG"):
                side = "LONG"

        # ── 做空判斷 ─────────────────────────────────────────────
        elif (rsi_val >= Config.MR_RSI_OVERBOUGHT and
              price >= bb_upper and adx_ok):
            if vol_ok and self._has_reversal_candle(df_a, "SHORT"):
                side = "SHORT"

        if side is None:
            return None

        # ── 訊號評分 ─────────────────────────────────────────────
        score = self._score_signal(df_a, side, rsi_val, bb_upper,
                                   bb_lower, bb_mid, Config)
        if score < Config.MR_MIN_SCORE:
            log.debug(f"[{symbol}] MR 訊號強度 {score} < {Config.MR_MIN_SCORE}")
            return None

        # ── 計算 TP/SL ───────────────────────────────────────────
        tp1, tp2, sl = self._calc_tp_sl(
            price, side, bb_upper, bb_lower, bb_mid, atr_val, Config
        )

        sig = Signal(
            symbol        = symbol,
            side          = side,
            entry_price   = price,
            stop_loss     = sl,
            take_profit_1 = tp1,
            take_profit_2 = tp2,
            score         = score,
            strategy_name = self.name,
            timeframe     = self.default_timeframe,
            pattern       = "MR_REVERSAL",
            metadata      = {
                "rsi":      round(rsi_val, 2),
                "adx":      round(adx_val, 2),
                "bb_upper": round(bb_upper, 4),
                "bb_lower": round(bb_lower, 4),
                "bb_mid":   round(bb_mid, 4),
                "vol_ratio": round(last_vol / avg_vol, 2) if avg_vol > 0 else 0,
            },
        )

        if not self.validate_signal(sig):
            log.debug(f"[{symbol}] MR TP/SL 不合理，捨棄")
            return None

        log.info(
            f"[{symbol}] MR 訊號：{side} RSI={rsi_val:.1f} "
            f"BB={'下' if side=='LONG' else '上'}軌 強度={score}"
        )
        return sig

    # ── 反轉 K 棒偵測 ────────────────────────────────────────────

    def _has_reversal_candle(self, df: pd.DataFrame, side: str) -> bool:
        """偵測止跌/見頂 K 棒形態"""
        c    = df.iloc[-1]
        body = abs(float(c["close"]) - float(c["open"]))
        upper_shadow = float(c["high"]) - max(float(c["close"]), float(c["open"]))
        lower_shadow = min(float(c["close"]), float(c["open"])) - float(c["low"])
        total = float(c["high"]) - float(c["low"])

        if side == "LONG":
            # 長下影線（hammer-like）
            if body > 0 and lower_shadow >= body * 2:
                return True
            # 陽線收盤 + 前 3 根都是陰線
            if float(c["close"]) > float(c["open"]):
                prev3 = df.iloc[-4:-1]
                if len(prev3) == 3 and all(
                    prev3["close"].iloc[i] < prev3["open"].iloc[i]
                    for i in range(3)
                ):
                    return True
            # pandas-ta 形態確認
            try:
                hammer = ta.cdl_pattern(
                    df["open"], df["high"], df["low"], df["close"], name="hammer"
                )
                if hammer is not None and float(hammer.iloc[-1]) > 0:
                    return True
                engulf = ta.cdl_pattern(
                    df["open"], df["high"], df["low"], df["close"], name="engulfing"
                )
                if engulf is not None and float(engulf.iloc[-1]) > 0:
                    return True
            except Exception:
                pass

        elif side == "SHORT":
            # 長上影線（shooting star-like）
            if body > 0 and upper_shadow >= body * 2:
                return True
            # 陰線收盤 + 前 3 根都是陽線
            if float(c["close"]) < float(c["open"]):
                prev3 = df.iloc[-4:-1]
                if len(prev3) == 3 and all(
                    prev3["close"].iloc[i] > prev3["open"].iloc[i]
                    for i in range(3)
                ):
                    return True
            try:
                shooting = ta.cdl_pattern(
                    df["open"], df["high"], df["low"], df["close"],
                    name="shootingstar"
                )
                if shooting is not None and float(shooting.iloc[-1]) < 0:
                    return True
                engulf = ta.cdl_pattern(
                    df["open"], df["high"], df["low"], df["close"], name="engulfing"
                )
                if engulf is not None and float(engulf.iloc[-1]) < 0:
                    return True
            except Exception:
                pass

        return False

    # ── 訊號評分 ─────────────────────────────────────────────────

    def _score_signal(self, df: pd.DataFrame, side: str,
                      rsi_val: float, bb_upper: float,
                      bb_lower: float, bb_mid: float,
                      Config) -> int:
        score = 1  # 基礎分（已通過 RSI + BB + K 棒條件）

        # RSI 極端度
        if (side == "LONG" and rsi_val <= 15) or \
           (side == "SHORT" and rsi_val >= 85):
            score += 1

        # BB 超出範圍（超出 1 個標準差以上）
        bb_width = bb_upper - bb_lower
        if side == "LONG" and float(df["close"].iloc[-1]) < bb_lower - bb_width * 0.1:
            score += 1
        elif side == "SHORT" and float(df["close"].iloc[-1]) > bb_upper + bb_width * 0.1:
            score += 1

        # Stochastic RSI 交叉確認
        try:
            stoch = ta.stochrsi(df["close"])
            if stoch is not None and len(stoch.columns) >= 2:
                k = float(stoch.iloc[-1, 0])
                d = float(stoch.iloc[-1, 1])
                if side == "LONG" and k > d and k < 20:
                    score += 1
                elif side == "SHORT" and k < d and k > 80:
                    score += 1
        except Exception:
            pass

        # MACD 背離（簡化版）
        try:
            macd = ta.macd(df["close"])
            if macd is not None and macd.shape[1] >= 3:
                hist = macd.iloc[:, 2]
                if side == "LONG" and float(hist.iloc[-1]) > float(hist.iloc[-2]) \
                        and float(df["close"].iloc[-1]) < float(df["close"].iloc[-2]):
                    score += 1  # 價格更低但 MACD 回升 = 底背離
                elif side == "SHORT" and float(hist.iloc[-1]) < float(hist.iloc[-2]) \
                        and float(df["close"].iloc[-1]) > float(df["close"].iloc[-2]):
                    score += 1  # 頂背離
        except Exception:
            pass

        return min(score, 5)

    # ── TP/SL 計算 ───────────────────────────────────────────────

    def _calc_tp_sl(self, entry: float, side: str,
                    bb_upper: float, bb_lower: float,
                    bb_mid: float, atr_val: float,
                    Config) -> tuple[float, float, float]:
        """
        使用 BB 結構作為 TP 目標：
          TP1 = BB 中軌（高勝率的回歸目標，~60%-70% 勝率）
          TP2 = BB 對側（低機率高報酬，全幅反轉）
        SL  = min(MR_SL_PCT × entry, 1.0 × ATR)（貼近波動性）
        """
        # 止損距離：取 MR_SL_PCT 與 1×ATR 較小者（保護資金）
        sl_dist = min(Config.MR_SL_PCT * entry, atr_val * 1.0)
        # 下限：止損至少 0.5%；上限：不超過 3%
        sl_dist = max(sl_dist, entry * 0.005)
        sl_dist = min(sl_dist, entry * 0.03)

        if side == "LONG":
            tp1 = bb_mid            # 回歸中軌
            tp2 = bb_upper          # 搏對側反轉
            sl  = entry - sl_dist
            # 合理性保護（BB 異常時 fallback）
            if tp1 <= entry:
                tp1 = entry * 1.010
            if tp2 <= tp1:
                tp2 = tp1 * 1.015
        else:
            tp1 = bb_mid
            tp2 = bb_lower
            sl  = entry + sl_dist
            if tp1 >= entry:
                tp1 = entry * 0.990
            if tp2 >= tp1:
                tp2 = tp1 * 0.985

        return tp1, tp2, sl
