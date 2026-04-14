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

log = logging.getLogger("market_ctx")

_TTL_SEC = 3600  # 1 小時快取


class MarketContext:
    def __init__(self, client):
        self.client = client
        self._cache: dict = {}

    # ── 快取輔助 ─────────────────────────────────────────────────
    def _get_cached(self, key: str):
        entry = self._cache.get(key)
        if entry and (time.time() - entry["ts"]) < _TTL_SEC:
            return entry["value"]
        return None

    def _set_cached(self, key: str, value):
        self._cache[key] = {"ts": time.time(), "value": value}

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
            raw = self.client.futures_klines(
                symbol="BTCUSDT", interval="1w", limit=30
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
            btc_raw = self.client.futures_klines(
                symbol="BTCUSDT", interval=interval, limit=window + 1
            )
            sym_raw = self.client.futures_klines(
                symbol=symbol, interval=interval, limit=window + 1
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
