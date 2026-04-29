"""
Market Context — 共用市場背景資訊

提供:
  1. BTC Dominance（CoinGecko API）— 判斷山寨幣環境
  2. BTC 週線 MA20 趨勢 — 判斷大盤多空背景
  3. BTC 相關性（rolling correlation）— 用於相關性控管

所有方法都有 TTL 快取（1 小時），避免重複請求外部 API。
"""
import logging
import time
from typing import Optional
import pandas as pd
import numpy as np
import urllib.request
import json

from api_retry import weight_aware_call, klines_weight

log = logging.getLogger("market_ctx")

_TTL_SEC = 3600  # 1 小時快取（長期：BTC dom / weekly / correlation / OI）

# K 線 cache 的 TTL 對應 timeframe（約為 timeframe 的一半，避免跨 K 棒取到舊資料）
_KLINE_TTL_MAP = {
    "1m": 20, "3m": 60, "5m": 90,
    "15m": 240, "30m": 480,
    "1h": 900, "2h": 1800, "4h": 3600,
}


class MarketContext:
    def __init__(self, client):
        self.client = client
        self._cache: dict = {}
        self._kline_cache: dict = {}  # key = (symbol, interval, limit)

    # ── 快取輔助 ─────────────────────────────────────────────────
    def _get_cached(self, key: str):
        entry = self._cache.get(key)
        if entry and (time.time() - entry["ts"]) < _TTL_SEC:
            return entry["value"]
        return None

    def _set_cached(self, key: str, value):
        self._cache[key] = {"ts": time.time(), "value": value}

    # ── K 線共用 cache（所有策略/選幣器共用，避免重複 API）────────
    def get_klines(self, symbol: str, interval: str = "15m",
                   limit: int = 200):
        """
        取得 K 線 DataFrame（帶 TTL cache）。
        所有策略/選幣器應優先呼叫此方法，避免同一 K 線被重複抓取。
        回傳 pd.DataFrame，失敗時拋出例外（由呼叫方處理）。
        """
        key = (symbol, interval, limit)
        ttl = _KLINE_TTL_MAP.get(interval, 60)
        entry = self._kline_cache.get(key)
        if entry and (time.time() - entry["ts"]) < ttl:
            # 回傳 copy，避免呼叫方修改 cached DataFrame
            return entry["df"].copy()

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
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
        df = df.reset_index(drop=True)
        self._kline_cache[key] = {"ts": time.time(), "df": df}
        return df.copy()

    def clear_kline_cache(self):
        """選幣/訊號大循環後可選擇清空，避免 memory 持續長大"""
        self._kline_cache.clear()

    # ── BTC Dominance ────────────────────────────────────────────
    def btc_dominance(self) -> float:
        """從 CoinGecko 取得 BTC 市佔率（%）。失敗回傳 -1"""
        cached = self._get_cached("btc_dom")
        if cached is not None:
            return cached
        try:
            req = urllib.request.Request(
                "https://api.coingecko.com/api/v3/global",
                headers={"User-Agent": "naked-k-fib-bot/1.0"}
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode())
            dom = float(data["data"]["market_cap_percentage"]["btc"])
            self._set_cached("btc_dom", dom)
            log.info(f"BTC Dominance = {dom:.2f}%")
            return dom
        except Exception as e:
            log.warning(f"取得 BTC Dominance 失敗: {e}")
            return -1.0

    def is_high_btc_dominance(self, threshold: float = 55.0) -> bool:
        """BTC 市佔率 > threshold → 山寨環境不佳"""
        dom = self.btc_dominance()
        return dom > threshold

    # ── BTC 週線趨勢 ─────────────────────────────────────────────
    def btc_weekly_bullish(self) -> Optional[bool]:
        """
        BTC 週線收盤 > MA20 → 大盤多頭背景
        True=多頭, False=空頭, None=無法判斷
        """
        cached = self._get_cached("btc_weekly")
        if cached is not None:
            return cached
        try:
            raw = weight_aware_call(
                self.client.futures_klines, weight=klines_weight(30),
                symbol="BTCUSDT", interval="1w", limit=30,
            )
            closes = [float(k[4]) for k in raw]
            if len(closes) < 20:
                return None
            ma20 = sum(closes[-20:]) / 20
            last = closes[-1]
            bullish = last > ma20
            self._set_cached("btc_weekly", bullish)
            log.info(
                f"BTC 週線：close={last:.0f} MA20={ma20:.0f} "
                f"{'多頭' if bullish else '空頭'}"
            )
            return bullish
        except Exception as e:
            log.warning(f"取得 BTC 週線失敗: {e}")
            return None

    # ── BTC 相關性 ───────────────────────────────────────────────
    def btc_correlation(self, symbol: str,
                        interval: str = "1h",
                        window: int = 100) -> Optional[float]:
        """
        計算 symbol 與 BTC 的對數收益相關性
        回傳 -1 ~ 1，失敗回傳 None
        """
        key = f"corr_{symbol}_{interval}_{window}"
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        if symbol == "BTCUSDT":
            self._set_cached(key, 1.0)
            return 1.0
        try:
            btc_raw = weight_aware_call(
                self.client.futures_klines, weight=klines_weight(window + 1),
                symbol="BTCUSDT", interval=interval, limit=window + 1,
            )
            sym_raw = weight_aware_call(
                self.client.futures_klines, weight=klines_weight(window + 1),
                symbol=symbol, interval=interval, limit=window + 1,
            )
            btc_close = np.array([float(k[4]) for k in btc_raw])
            sym_close = np.array([float(k[4]) for k in sym_raw])
            n = min(len(btc_close), len(sym_close))
            if n < 20:
                return None
            btc_ret = np.diff(np.log(btc_close[-n:]))
            sym_ret = np.diff(np.log(sym_close[-n:]))
            corr = float(np.corrcoef(btc_ret, sym_ret)[0, 1])
            if np.isnan(corr):
                return None
            self._set_cached(key, corr)
            return corr
        except Exception as e:
            log.debug(f"{symbol} 相關性計算失敗: {e}")
            return None

    def is_high_correlation(self, symbol: str,
                            threshold: float = 0.8) -> bool:
        """|corr| > threshold 視為高相關"""
        corr = self.btc_correlation(symbol)
        if corr is None:
            return False
        return abs(corr) >= threshold

    # ── 市場型態（Regime）──────────────────────────────────────
    def current_regime(self) -> str:
        """
        回傳 BTC 目前市場型態：
            "TREND_UP"   — 4h ADX>=20 且日線收盤 > MA50
            "TREND_DOWN" — 4h ADX>=20 且日線收盤 < MA50
            "RANGE"      — 4h ADX<20 且日線靠 MA50（±3%）
            "CHOPPY"     — 其它（不明朗）
        失敗或資料不足時回傳 "CHOPPY"。
        """
        cached = self._get_cached("btc_regime")
        if cached is not None:
            return cached
        try:
            # 4h ADX
            raw4h = weight_aware_call(
                self.client.futures_klines, weight=klines_weight(60),
                symbol="BTCUSDT", interval="4h", limit=60,
            )
            if len(raw4h) < 30:
                return "CHOPPY"
            import pandas as pd
            import pandas_ta as ta
            df4 = pd.DataFrame(raw4h, columns=[
                "time", "open", "high", "low", "close", "volume",
                "close_time", "qav", "trades", "tbav", "tbqv", "ignore"
            ])
            for c in ["high", "low", "close"]:
                df4[c] = df4[c].astype(float)
            adx_df = ta.adx(df4["high"], df4["low"], df4["close"], length=14)
            adx_val = float(adx_df["ADX_14"].iloc[-1]) if adx_df is not None else 0.0

            # 日線 MA50
            rawD = weight_aware_call(
                self.client.futures_klines, weight=klines_weight(60),
                symbol="BTCUSDT", interval="1d", limit=60,
            )
            if len(rawD) < 50:
                return "CHOPPY"
            closes = [float(k[4]) for k in rawD]
            ma50   = sum(closes[-50:]) / 50
            last   = closes[-1]
            above_ma = last > ma50
            dist_pct = abs(last - ma50) / ma50 if ma50 > 0 else 0

            if adx_val >= 20:
                regime = "TREND_UP" if above_ma else "TREND_DOWN"
            else:
                if dist_pct <= 0.03:
                    regime = "RANGE"
                else:
                    regime = "CHOPPY"

            self._set_cached("btc_regime", regime)
            log.info(
                f"BTC Regime = {regime} (4h ADX={adx_val:.1f} "
                f"last={last:.0f} MA50={ma50:.0f} dist={dist_pct:.1%})"
            )
            return regime
        except Exception as e:
            log.warning(f"BTC regime 判定失敗: {e}")
            return "CHOPPY"

    def regime_allows(self, strategy: str) -> bool:
        """
        根據目前 BTC regime 判斷策略是否可進場。
            TREND_UP   → momentum_long, naked_k_fib（LONG 側）
            TREND_DOWN → breakdown_short, naked_k_fib（SHORT 側）
            RANGE      → mean_reversion
            CHOPPY     → naked_k_fib 仍允許（裸K+Fib 本身不限型態），其它阻擋
        對 naked_k_fib 永遠回 True，由 NKF 內部自有多空過濾。
        """
        regime = self.current_regime()
        if strategy == "naked_k_fib":
            return True
        if regime == "TREND_UP":
            return strategy == "momentum_long"
        if regime == "TREND_DOWN":
            return strategy == "breakdown_short"
        if regime == "RANGE":
            return strategy == "mean_reversion"
        # CHOPPY → 全擋
        return False

    # ── 24h 漲跌幅（用於相對強度過濾）────────────────────────────
    def price_change_pct_24h(self, symbol: str) -> Optional[float]:
        """
        取得 symbol 近 24h 漲跌幅（%）。
        優先用 futures_ticker（含 priceChangePercent）；
        失敗時 fallback 到 futures_klines 1h×24 自行計算。
        快取 120 秒避免重複呼叫。
        """
        key = f"chg24_{symbol}"
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        try:
            # futures_ticker（單 symbol） weight = 2
            t = weight_aware_call(
                self.client.futures_ticker, weight=2, symbol=symbol,
            )
            # futures_ticker 可能回傳 list 或 dict，統一處理
            if isinstance(t, list):
                t = t[0] if t else {}
            pct = t.get("priceChangePercent")
            if pct is not None:
                v = float(pct)
                self._set_cached(key, v)
                return v
        except Exception as e:
            log.debug(f"{symbol} 24h 變化查詢失敗（ticker）: {e}")

        # fallback：用 1h×25 klines 算
        try:
            raw = weight_aware_call(
                self.client.futures_klines, weight=klines_weight(25),
                symbol=symbol, interval="1h", limit=25,
            )
            if not raw or len(raw) < 24:
                return None
            start = float(raw[0][4])
            end   = float(raw[-1][4])
            if start <= 0:
                return None
            v = (end - start) / start * 100
            self._set_cached(key, v)
            return v
        except Exception as e:
            log.debug(f"{symbol} 24h 變化查詢失敗（klines fallback）: {e}")
            return None

    def btc_change_pct_24h(self) -> Optional[float]:
        """BTC 24h 漲跌幅 shortcut"""
        return self.price_change_pct_24h("BTCUSDT")

    # ── Open Interest 異常偵測 ───────────────────────────────────
    def oi_change_pct(self, symbol: str) -> Optional[float]:
        """
        計算 symbol 的 OI 24h 變化百分比。
        使用幣安 futures openInterestHist API（5m 粒度，取最近 288 根 = 24h）。
        回傳百分比（如 25.0 代表 +25%），失敗回傳 None。
        """
        key = f"oi_chg_{symbol}"
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        try:
            # openInterestHist 需要 period 參數（weight = 1，與一般 GET 同級）
            hist = weight_aware_call(
                self.client.futures_open_interest_hist, weight=1,
                symbol=symbol, period="5m", limit=288,
            )
            if not hist or len(hist) < 10:
                return None
            oi_start = float(hist[0]["sumOpenInterest"])
            oi_end   = float(hist[-1]["sumOpenInterest"])
            if oi_start <= 0:
                return None
            change_pct = (oi_end - oi_start) / oi_start * 100
            self._set_cached(key, change_pct)
            return change_pct
        except Exception as e:
            log.debug(f"{symbol} OI 查詢失敗: {e}")
            return None

    def is_oi_anomaly(self, symbol: str,
                      threshold_pct: float = 20.0) -> bool:
        """OI 24h 變動 > threshold_pct% → 視為異常（大戶佈局，技術面容易失效）"""
        change = self.oi_change_pct(symbol)
        if change is None:
            return False  # 查詢失敗不阻擋
        return abs(change) > threshold_pct
