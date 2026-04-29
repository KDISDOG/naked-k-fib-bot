"""
coin_screener_granville.py — Granville 葛蘭碧策略專屬選幣器

設計目的：與 NKF/MASR 等通用篩選不同，Granville 需要的是「明確趨勢方向」的幣。
評分標準（滿分 10）：
  ADX > 25                                    →  3 分（趨勢明確）
  價格與 EMA60 同側 ≥ 5 根 K                   →  2 分（趨勢穩定）
  EMA20 斜率 |slope/price| > 0.5%              →  2 分（均線本身有方向）
  24h 成交量 > 100M USDT                       →  2 分（流動性）
  ATR/price 在 1.5%~4%                         →  1 分（波動適中）

門檻：score ≥ 7 才入候選池。最多取 top N（預設 4 支）。

注意：用 4H K 線（與策略 timeframe 一致），透過 market_ctx 共用 cache。
"""
import logging
import pandas as pd
import pandas_ta as ta
from typing import List

from binance.client import Client
from api_retry import weight_aware_call, klines_weight, get_exchange_info_cached

log = logging.getLogger("coin_screener_granville")

_LEVERAGE_TAGS = ("UP", "DOWN", "BULL", "BEAR")
_STABLE_PREFIX = ("USDC", "FDUSD", "TUSD", "BUSD", "DAI")


class GranvilleScreener:
    """Granville 趨勢友好選幣器。"""

    def __init__(self, client: Client, market_ctx=None):
        self.client = client
        self.market_ctx = market_ctx

    # ── K 線：優先走 market_ctx cache ─────────────────────────
    def _get_klines(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        if self.market_ctx is not None and hasattr(self.market_ctx, "get_klines"):
            return self.market_ctx.get_klines(symbol, interval, limit)
        raw = weight_aware_call(
            self.client.futures_klines, weight=klines_weight(limit),
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

    # ── 24h 成交量（用 1d K 線最近一根的 qav 估算）────────────
    def _get_24h_quote_volume(self, df_1d: pd.DataFrame) -> float:
        """用 1d 線最後一根 quote asset volume 取 24h 成交量（USDT）。"""
        try:
            return float(df_1d["qav"].iloc[-1])
        except Exception:
            return 0.0

    # ── 評分核心 ─────────────────────────────────────────────
    def _score(self, symbol: str) -> int:
        from config import Config

        try:
            df = self._get_klines(
                symbol, Config.GRANVILLE_TIMEFRAME, limit=120
            )
            if len(df) < 80:
                return 0

            close = df["close"]
            high = df["high"]
            low = df["low"]

            ema60 = ta.ema(close, length=Config.GRANVILLE_EMA_PERIOD)
            ema20 = ta.ema(close, length=Config.GRANVILLE_EMA_SHORT)
            atr_s = ta.atr(high, low, close, length=Config.GRANVILLE_ATR_PERIOD)
            adx_df = ta.adx(high, low, close, length=Config.GRANVILLE_ADX_PERIOD)
            if any(x is None for x in (ema60, ema20, atr_s, adx_df)):
                return 0
            adx_v = float(adx_df["ADX_14"].iloc[-1]) if "ADX_14" in adx_df.columns else 0.0
            ema60_v = float(ema60.iloc[-1])
            ema20_v = float(ema20.iloc[-1])
            atr_v = float(atr_s.iloc[-1])
            cur_close = float(close.iloc[-1])
            if any(pd.isna(x) for x in (adx_v, ema60_v, ema20_v, atr_v, cur_close)):
                return 0

            score = 0

            # 1. ADX > 25（3 分）
            if adx_v > 25:
                score += 3

            # 2. 連續 5 根與 EMA60 同側（2 分）
            same_bars = int(Config.GRANVILLE_SCREEN_PRICE_SAME_SIDE_BARS)
            recent_close = close.iloc[-same_bars:]
            recent_ema = ema60.iloc[-same_bars:]
            same_side_up = bool((recent_close > recent_ema).all())
            same_side_down = bool((recent_close < recent_ema).all())
            if same_side_up or same_side_down:
                score += 2

            # 3. EMA20 斜率 |slope/price| > 0.5%（2 分）
            ema20_lookback = ema20.iloc[-same_bars:]
            if len(ema20_lookback) >= 2 and cur_close > 0:
                slope = (float(ema20_lookback.iloc[-1])
                         - float(ema20_lookback.iloc[0]))
                slope_pct = abs(slope / cur_close)
                if slope_pct > float(Config.GRANVILLE_SCREEN_SLOPE_MIN_PCT):
                    score += 2

            # 4. 24h 量 > 100M（2 分）— 用 1d qav
            try:
                df_1d = self._get_klines(symbol, "1d", limit=2)
                qav_24h = self._get_24h_quote_volume(df_1d)
                if qav_24h > float(Config.GRANVILLE_SCREEN_VOL_M) * 1_000_000:
                    score += 2
            except Exception as e:
                log.debug(f"[{symbol}] Granville 24h vol 取得失敗: {e}")

            # 5. ATR/price 在 1.5-4%（1 分）
            atr_pct = atr_v / cur_close * 100 if cur_close > 0 else 0
            if (float(Config.GRANVILLE_SCREEN_ATR_MIN_PCT)
                    <= atr_pct
                    <= float(Config.GRANVILLE_SCREEN_ATR_MAX_PCT)):
                score += 1

            return score
        except Exception as e:
            log.debug(f"[{symbol}] Granville 評分失敗: {e}")
            return 0

    # ── 對外介面 ─────────────────────────────────────────────
    def screen(self, candidates: List[str]) -> List[str]:
        """
        從 candidates 中選出 Granville 適合的趨勢幣。
        score ≥ MIN_SCORE，按分數由高到低取 top N。
        """
        from config import Config

        if not candidates:
            return []

        min_score = int(Config.GRANVILLE_SCREEN_MIN_SCORE)
        top_n = int(Config.GRANVILLE_SCREEN_TOP_N)

        # 上市時間過濾（≥ 90 天，趨勢策略不適合新幣）
        onboard_map: dict[str, int] = {}
        try:
            info = get_exchange_info_cached(self.client)
            for s in info.get("symbols", []):
                onboard_map[s["symbol"]] = int(s.get("onboardDate", 0))
        except Exception as e:
            log.debug(f"[Granville 篩選] onboardDate 失敗（略過該過濾）: {e}")

        listing_cutoff_ms = int(
            (pd.Timestamp.utcnow() - pd.Timedelta(days=90)).timestamp() * 1000
        )

        scored: list[tuple[str, int]] = []
        for sym in candidates:
            try:
                upper = sym.upper()
                if any(tag in upper for tag in _LEVERAGE_TAGS):
                    continue
                base = upper.replace("USDT", "")
                if any(base.startswith(p) for p in _STABLE_PREFIX):
                    continue
                if onboard_map and sym in onboard_map:
                    if onboard_map[sym] > listing_cutoff_ms:
                        continue
                s = self._score(sym)
                if s >= min_score:
                    scored.append((sym, s))
            except Exception as e:
                log.debug(f"[Granville 篩選] {sym} 失敗: {e}")

        scored.sort(key=lambda x: x[1], reverse=True)
        selected = [s[0] for s in scored[:top_n]]
        log.info(
            f"[Granville] 選幣完成：{len(selected)} 支入選 "
            f"（{len(candidates)} 候選 → {len(scored)} 通過門檻 → top {top_n}）"
        )
        if selected:
            score_map = dict(scored[:top_n])
            log.info(
                f"[Granville] 入選幣 + 分數："
                + ", ".join(f"{s}={score_map[s]}" for s in selected)
            )
        return selected
