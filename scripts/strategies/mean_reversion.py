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
        # 優先走 MarketContext 共用 cache，避免跨策略/跨呼叫重複抓
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

        # BTC Dominance > 55% → 山寨震盪環境差（資金集中在 BTC），MR 扣 1 分
        if self._market_ctx and symbol != "BTCUSDT":
            try:
                if self._market_ctx.is_high_btc_dominance(threshold=55.0):
                    score -= 1
            except Exception:
                pass

        return score

    # ── 訊號偵測 ─────────────────────────────────────────────────

    def check_signal(self, symbol: str) -> Optional[Signal]:
        from config import Config
        # ── Regime gate：MR 只在 RANGE 放行 ────────────────────
        if self._market_ctx and getattr(Config, "REGIME_GATE_ENABLED", True):
            try:
                if not self._market_ctx.regime_allows("mean_reversion"):
                    log.debug(
                        f"[{symbol}] MR 被 regime "
                        f"{self._market_ctx.current_regime()} 阻擋"
                    )
                    return None
            except Exception:
                pass

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

        # Gap filter（signal 時再檢查一次）：screening 到 signal 間隔可能
        # 發生大事件跳空；且跳空後 RSI 極端特別容易觸發，避免被事件單套牢
        recent_20 = df_a.tail(21)
        gaps = (
            (recent_20["open"] - recent_20["close"].shift(1)).abs()
            / recent_20["close"].shift(1)
        ).dropna()
        if len(gaps) > 0 and float(gaps.max()) > 0.03:
            log.debug(
                f"[{symbol}] MR 近 20 根偵測到 >3% 跳空（{float(gaps.max())*100:.1f}%），跳過"
            )
            return None

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

        # ADX 過濾（MR 只在非趨勢盤運作；ADX < 25 與 NKF 的 15-45 區間部分重疊但不衝突）
        adx_ok = adx_val < 25

        side = None

        # ── 做多判斷 ─────────────────────────────
        if (rsi_val <= Config.MR_RSI_OVERSOLD and
                price <= bb_lower and adx_ok and vol_ok):
            if self._has_reversal_candle(df_a, "LONG"):
                side = "LONG"

        # ── 做空判斷 ─────────────────────────────
        elif (rsi_val >= Config.MR_RSI_OVERBOUGHT and
              price >= bb_upper and adx_ok and vol_ok):
            if self._has_reversal_candle(df_a, "SHORT"):
                side = "SHORT"

        if side is None:
            return None

        # ── 結構性過濾（v5：根因解 MR 邊際負期望）──────────────
        # 之前 MR 44% win × R:R 1.14 → 微負期望，主因「無結構盲接 RSI 訊號」。
        # 加兩道硬門檻：
        #   1. 正規 RSI 背離（價格 LL + RSI HL）→ 動能轉強早期訊號
        #   2. 接近結構性 S/R 區（最近 swing low/high）→ 確認有支撐
        # 預期：訊號量 -60~70%，但勝率從 44% → 50%+，期望值由負轉正。

        if getattr(Config, "MR_REQUIRE_DIVERGENCE", True):
            div_lookback = int(getattr(Config, "MR_DIV_LOOKBACK", 20))
            if not self._has_rsi_divergence(df_a, rsi, side, div_lookback):
                log.debug(f"[{symbol}] MR {side} 無 RSI 背離（lookback={div_lookback}），跳過")
                return None

        if getattr(Config, "MR_REQUIRE_SR_TEST", True):
            sr_lookback = int(getattr(Config, "MR_SR_LOOKBACK", 30))
            sr_tol = float(getattr(Config, "MR_SR_TOLERANCE", 0.015))
            if not self._has_sr_test(df_a, side, sr_lookback, sr_tol):
                log.debug(
                    f"[{symbol}] MR {side} 未測試到結構性 S/R "
                    f"(lookback={sr_lookback} tol={sr_tol*100:.1f}%)，跳過"
                )
                return None

        # ── BTC 週線趨勢過濾（MR 兩邊都不逆大盤）────────────────
        # LONG 被週線空頭過濾、SHORT 被週線多頭過濾，避免小週期反轉訊號
        # 跟大週期方向硬碰硬（牛市急拉時 RSI>75 的山寨做空常被掃）
        if self._market_ctx:
            btc_bull = self._market_ctx.btc_weekly_bullish()
            if side == "LONG" and btc_bull is False:
                log.debug(f"[{symbol}] MR LONG 被 BTC 週線空頭過濾")
                return None
            if side == "SHORT" and btc_bull is True:
                log.debug(f"[{symbol}] MR SHORT 被 BTC 週線多頭過濾")
                return None

        # ── 訊號評分 ─────────────────────────────────────────────
        score = self._score_signal(df_a, side, rsi_val, bb_upper,
                                   bb_lower, bb_mid, Config)
        # Funding rate 方向性加分（順擠壓 +1、逆擠壓 -1、中性 0）
        try:
            from funding_bias import funding_bonus
            fb = funding_bonus(self._client, symbol, side)
            if fb != 0:
                log.debug(f"[{symbol}] MR funding bonus={fb:+d} (side={side})")
            score = max(1, min(score + fb, 5))
        except Exception:
            pass
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

    # ── RSI 背離偵測（v5）────────────────────────────────────────
    def _has_rsi_divergence(
        self,
        df: pd.DataFrame,
        rsi_series: pd.Series,
        side: str,
        lookback: int = 20,
    ) -> bool:
        """
        正規 RSI 背離（比現有「最近 2 根 MACD hist」嚴格得多）：

        LONG bullish divergence：
            價格在 lookback 內創 lower-low，但 RSI 在同位點創 higher-low
            → 動能轉強的早期訊號，反彈成功率高

        SHORT bearish divergence：
            價格 higher-high + RSI lower-high

        實作：
        1. 找 lookback 區間內最近的兩個 swing low/high（彼此間隔 ≥ 3 根）
        2. 比較它們的價格 + 對應 RSI 值
        3. 滿足背離條件 → 回 True
        """
        if df is None or rsi_series is None:
            return False
        try:
            window = df.tail(lookback).reset_index(drop=True)
            rsi_w = rsi_series.tail(lookback).reset_index(drop=True)
            if len(window) < 8 or len(rsi_w) < 8:
                return False

            if side == "LONG":
                # 找最低點（swing low）
                cur_idx = int(window["low"].idxmin())
                # 之前的 swing low（隔 ≥ 3 根，避免相鄰）
                prior_window = window["low"].iloc[:max(0, cur_idx - 3)]
                if len(prior_window) < 3:
                    return False
                prev_idx = int(prior_window.idxmin())
                # 條件：價格 lower-low + RSI higher-low
                price_ll = float(window["low"].iloc[cur_idx]) < \
                           float(window["low"].iloc[prev_idx])
                rsi_hl = float(rsi_w.iloc[cur_idx]) > \
                         float(rsi_w.iloc[prev_idx])
                # 排除過於接近（無意義）：RSI 差至少 2 點
                rsi_meaningful = abs(
                    float(rsi_w.iloc[cur_idx]) - float(rsi_w.iloc[prev_idx])
                ) >= 2.0
                return price_ll and rsi_hl and rsi_meaningful
            else:  # SHORT
                cur_idx = int(window["high"].idxmax())
                prior_window = window["high"].iloc[:max(0, cur_idx - 3)]
                if len(prior_window) < 3:
                    return False
                prev_idx = int(prior_window.idxmax())
                price_hh = float(window["high"].iloc[cur_idx]) > \
                           float(window["high"].iloc[prev_idx])
                rsi_lh = float(rsi_w.iloc[cur_idx]) < \
                         float(rsi_w.iloc[prev_idx])
                rsi_meaningful = abs(
                    float(rsi_w.iloc[cur_idx]) - float(rsi_w.iloc[prev_idx])
                ) >= 2.0
                return price_hh and rsi_lh and rsi_meaningful
        except Exception:
            return False

    # ── 支撐/阻力測試（v5）──────────────────────────────────────
    def _has_sr_test(
        self,
        df: pd.DataFrame,
        side: str,
        lookback: int = 30,
        tolerance: float = 0.015,
    ) -> bool:
        """
        確認當前價格在「結構性 S/R 區」：
          LONG  → 當前 close 接近 lookback 內的最低 low（±tolerance）
          SHORT → 接近最高 high

        防止在「無結構支撐」的下跌中盲接刀子（mean revert 失敗主因）。
        """
        if df is None or len(df) < lookback + 1:
            return False
        try:
            window = df.iloc[-lookback - 1:-1]  # 不含當根（防 self-reference）
            cur = float(df["close"].iloc[-1])
            if side == "LONG":
                key_level = float(window["low"].min())
                # 當前價必須在 key_level 的 ±tolerance 範圍內
                return cur <= key_level * (1 + tolerance) and \
                       cur >= key_level * (1 - tolerance * 2)
            else:
                key_level = float(window["high"].max())
                return cur >= key_level * (1 - tolerance) and \
                       cur <= key_level * (1 + tolerance * 2)
        except Exception:
            return False

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

        # RSI 極端度（階層化）
        if side == "LONG":
            if rsi_val <= 15:
                score += 2
            elif rsi_val <= 20:
                score += 1
        elif side == "SHORT":
            if rsi_val >= 85:
                score += 2
            elif rsi_val >= 80:
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
        MR 核心：快進快出。
          TP1 = min(1.2×ATR, 到BB中軌距離)（短距高勝率）
          TP2 = BB 中軌（回歸目標）
        SL  = min(MR_SL_PCT × entry, 1.0 × ATR)
        分倉 70/30：TP1 取 70% 快出鎖利。
        """
        # 止損距離：取 MR_SL_PCT 與 1×ATR 較小者（保護資金）
        sl_dist = min(Config.MR_SL_PCT * entry, atr_val * 1.0)
        # 下限：止損至少 0.5%；上限：不超過 3%
        sl_dist = max(sl_dist, entry * 0.005)
        sl_dist = min(sl_dist, entry * 0.03)

        # TP1：ATR-based 短目標（高勝率）
        mid_dist = abs(bb_mid - entry)
        tp1_dist = min(atr_val * 1.2, mid_dist) if mid_dist > atr_val * 0.5 else atr_val * 1.0
        # 最少 1.2%：0.5% 扣 round-trip taker fee 0.08% 後淨利僅 0.42%，
        # 勝率要 >70% 才能撐期望值；用 1.2% 保留合理淨利空間
        tp1_dist = max(tp1_dist, entry * 0.012)

        if side == "LONG":
            tp1 = entry + tp1_dist
            tp2 = bb_mid if bb_mid > tp1 else tp1 * 1.015
            sl  = entry - sl_dist
        else:
            tp1 = entry - tp1_dist
            tp2 = bb_mid if bb_mid < tp1 else tp1 * 0.985
            sl  = entry + sl_dist

        return tp1, tp2, sl
