"""
funding_bias.py — Funding Rate 方向性加分輔助

用法：
    from funding_bias import funding_bonus
    bonus = funding_bonus(client, symbol, side)   # -1 / 0 / +1
    score += bonus

邏輯（以 8h funding 為基準，百分比）：
    - |fr| < 0.02%    → 中性 0
    - fr >= +0.05%   （多方擠壓）：
          SHORT +1（順擠壓方向，易有資金反噬行情）
          LONG  -1（逆擠壓，需承擔高持倉成本）
    - fr <= -0.05%   （空方擠壓）：
          LONG  +1
          SHORT -1
    - 介於中間不額外加減

為了不爆 API，加入 2 分鐘 in-memory cache。
"""
import time
import logging
import threading

log = logging.getLogger("funding_bias")

_cache: dict[str, tuple[float, float]] = {}   # symbol -> (fr_pct, ts)
_cache_lock = threading.Lock()
_TTL = 120.0   # 秒

# 閾值（百分比，對應 8h funding）
_NEUTRAL_BAND = 0.02     # |fr| < 0.02% 視為中性
_STRONG_BAND  = 0.05     # |fr| >= 0.05% 啟動加分/扣分


def _fetch_funding_pct(client, symbol: str) -> float | None:
    """回傳 funding rate，單位 % / 8h；失敗則 None。"""
    try:
        data = client.futures_funding_rate(symbol=symbol, limit=1)
        if not data:
            return None
        return float(data[-1]["fundingRate"]) * 100.0
    except Exception as e:
        log.debug(f"[{symbol}] funding fetch 失敗：{e}")
        return None


def get_funding_pct(client, symbol: str) -> float | None:
    """有 cache 的 funding 取得。"""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(symbol)
        if hit and (now - hit[1]) < _TTL:
            return hit[0]
    fr = _fetch_funding_pct(client, symbol)
    if fr is None:
        return None
    with _cache_lock:
        _cache[symbol] = (fr, now)
    return fr


def funding_bonus(client, symbol: str, side: str) -> int:
    """
    回傳 -1 / 0 / +1；取不到 funding 時回 0（不影響原分數）。
    side: "LONG" 或 "SHORT"
    """
    fr = get_funding_pct(client, symbol)
    if fr is None:
        return 0
    # 中性帶
    if abs(fr) < _NEUTRAL_BAND:
        return 0
    if fr >= _STRONG_BAND:
        # 多方擠壓
        return +1 if side == "SHORT" else -1 if side == "LONG" else 0
    if fr <= -_STRONG_BAND:
        # 空方擠壓
        return +1 if side == "LONG" else -1 if side == "SHORT" else 0
    return 0
