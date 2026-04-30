"""
coin_features.py — 對 universe 算 8 個特徵

對每個 symbol 抓 N 月日線，算：
  atr_pct_med / adx_med / range_share / whipsaw_idx / gap_freq /
  volume_quote_med / btc_corr_30d / asset_class
+ history_months_actual（實際拿到幾個月資料）

Pickle 到 .cache/coin_features_{months}m.pkl（TTL 7 天）。
"""
import os
import sys
import time
import pickle
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta

sys.path.insert(0, str(Path(__file__).parent))
from backtest import fetch_klines

log = logging.getLogger("coin_features")

CACHE_TTL_SEC = 7 * 24 * 3600


# ── Asset class（規則優先）─────────────────────────────────────
_MAJOR = {"BTC", "ETH"}
_MEME = {"DOGE", "1000PEPE", "1000SHIB", "FLOKI", "BONK"}
_CFD = {"XAU", "XAG", "CL", "CO", "NG"}


def classify_asset(symbol: str) -> str:
    upper = symbol.upper()
    if not upper.endswith("USDT"):
        # 不在 perp USDT 規範內 → unknown，警告
        print(f"  [warn] 請手動標記 asset_class for {symbol}")
        return "unknown"
    base = upper[:-4]  # 拿掉 USDT
    if base in _MAJOR:
        return "crypto_major"
    if base in _MEME:
        return "meme"
    if base in _CFD:
        return "cfd"
    return "crypto_alt"


# ── 特徵計算 ────────────────────────────────────────────────────
def _features_from_daily(df_d: pd.DataFrame, df_btc_close: Optional[np.ndarray],
                          df_btc_time: Optional[np.ndarray]) -> dict:
    """單幣特徵計算（已抓到日線後）"""
    n = len(df_d)
    if n < 30:
        return {
            "atr_pct_med": np.nan, "adx_med": np.nan, "range_share": np.nan,
            "whipsaw_idx": np.nan, "gap_freq": np.nan,
            "volume_quote_med": np.nan, "btc_corr_30d": np.nan,
            "history_months_actual": round(n / 30.0, 1),
        }

    high = df_d["high"]
    low = df_d["low"]
    close = df_d["close"]
    open_ = df_d["open"]

    # 1. ATR%（去 14 根 warmup）
    atr_s = ta.atr(high, low, close, length=14)
    atr_pct = (atr_s / close) * 100
    atr_pct_med = float(atr_pct.iloc[14:].dropna().median())

    # 2. ADX
    adx_df = ta.adx(high, low, close, length=14)
    adx_s = adx_df["ADX_14"] if (adx_df is not None and "ADX_14" in adx_df.columns) else None
    if adx_s is not None:
        adx_clean = adx_s.dropna()
        adx_med = float(adx_clean.median()) if len(adx_clean) > 0 else np.nan
        # 3. range_share
        range_share = float((adx_clean < 20).mean()) if len(adx_clean) > 0 else np.nan
    else:
        adx_med = np.nan
        range_share = np.nan

    # 4. whipsaw_idx：close 穿越 EMA20 的次數 / 總 K 線數
    ema20 = ta.ema(close, length=20)
    if ema20 is not None:
        diff = (close - ema20).dropna()
        sign = np.sign(diff.values)
        # sign 變號的次數
        sign_changes = int(((sign[1:] * sign[:-1]) < 0).sum())
        whipsaw_idx = sign_changes / max(len(sign), 1)
    else:
        whipsaw_idx = np.nan

    # 5. gap_freq：|open[t] - close[t-1]| / close[t-1] > 0.5%
    prev_close = close.shift(1)
    gap = (open_ - prev_close).abs() / prev_close
    gap_freq = float((gap > 0.005).dropna().mean())

    # 6. volume_quote_med（quote_asset_volume = qav 欄位）
    if "qav" in df_d.columns:
        volume_quote_med = float(df_d["qav"].median())
    else:
        # fallback：volume × close 估計
        volume_quote_med = float((df_d["volume"] * close).median())

    # 7. btc_corr_30d：最近 30 日對齊後算 close return Pearson
    btc_corr_30d = np.nan
    if df_btc_close is not None and df_btc_time is not None:
        try:
            sym_time = df_d["time"].values
            sym_close = close.values
            # 取最近 31 天（30 日 return）
            tail_n = min(31, len(sym_close))
            sym_t_tail = sym_time[-tail_n:]
            sym_c_tail = sym_close[-tail_n:]
            # 對齊 BTC 同期間
            mask = np.isin(df_btc_time, sym_t_tail)
            btc_t_aligned = df_btc_time[mask]
            btc_c_aligned = df_btc_close[mask]
            if len(btc_c_aligned) >= 10 and len(sym_c_tail) >= 10:
                # 對齊兩邊：重排到 sym_t_tail 順序
                # 簡化：用 pandas merge
                df1 = pd.DataFrame({"time": sym_t_tail, "sym": sym_c_tail})
                df2 = pd.DataFrame({"time": btc_t_aligned, "btc": btc_c_aligned})
                merged = df1.merge(df2, on="time", how="inner")
                if len(merged) >= 10:
                    sym_ret = merged["sym"].pct_change().dropna()
                    btc_ret = merged["btc"].pct_change().dropna()
                    if len(sym_ret) >= 5:
                        btc_corr_30d = float(sym_ret.corr(btc_ret))
        except Exception:
            pass

    return {
        "atr_pct_med": round(atr_pct_med, 4) if not np.isnan(atr_pct_med) else np.nan,
        "adx_med": round(adx_med, 2) if not np.isnan(adx_med) else np.nan,
        "range_share": round(range_share, 3) if not np.isnan(range_share) else np.nan,
        "whipsaw_idx": round(whipsaw_idx, 3) if not np.isnan(whipsaw_idx) else np.nan,
        "gap_freq": round(gap_freq, 3) if not np.isnan(gap_freq) else np.nan,
        "volume_quote_med": round(volume_quote_med, 0) if not np.isnan(volume_quote_med) else np.nan,
        "btc_corr_30d": round(btc_corr_30d, 3) if not np.isnan(btc_corr_30d) else np.nan,
        "history_months_actual": round(n / 30.0, 1),
    }


# ── 對外主入口 ─────────────────────────────────────────────────
def compute_coin_features(client, symbols: list[str],
                           months: int = 39) -> pd.DataFrame:
    """
    回傳 DataFrame，columns: symbol + 8 features + history_months_actual
    """
    cache_path = Path(__file__).parent.parent / ".cache" / f"coin_features_{months}m.pkl"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < CACHE_TTL_SEC:
            print(f"[coin_features] cache hit ({age/3600:.1f}h old): {cache_path}")
            return pd.read_pickle(cache_path)

    # 預抓 BTC 日線（給 corr 用）
    print(f"[coin_features] 預抓 BTCUSDT 1d 39m 給 corr 對齊...")
    try:
        df_btc = fetch_klines(client, "BTCUSDT", "1d", months)
        btc_time = df_btc["time"].values
        btc_close = df_btc["close"].values
    except Exception as e:
        print(f"  [warn] BTC 拉取失敗：{e}，btc_corr_30d 會是 NaN")
        btc_time = None
        btc_close = None

    rows = []
    for sym in symbols:
        try:
            df_d = fetch_klines(client, sym, "1d", months)
        except Exception as e:
            print(f"  [{sym}] 抓 1d 失敗：{e}")
            rows.append({
                "symbol": sym,
                **{k: np.nan for k in ["atr_pct_med", "adx_med", "range_share",
                                         "whipsaw_idx", "gap_freq",
                                         "volume_quote_med", "btc_corr_30d"]},
                "asset_class": classify_asset(sym),
                "history_months_actual": 0.0,
            })
            continue

        feats = _features_from_daily(df_d, btc_close, btc_time)
        feats["symbol"] = sym
        feats["asset_class"] = classify_asset(sym)
        rows.append(feats)
        print(f"  [{sym:<14}] atr%={feats['atr_pct_med']}  adx={feats['adx_med']}  "
              f"range_share={feats['range_share']}  whip={feats['whipsaw_idx']}  "
              f"gap={feats['gap_freq']}  qav_med={feats['volume_quote_med']}  "
              f"corr={feats['btc_corr_30d']}  class={feats['asset_class']}  "
              f"hist={feats['history_months_actual']}m")

    df = pd.DataFrame(rows)
    cols = ["symbol", "atr_pct_med", "adx_med", "range_share", "whipsaw_idx",
            "gap_freq", "volume_quote_med", "btc_corr_30d", "asset_class",
            "history_months_actual"]
    df = df[cols]

    df.to_pickle(cache_path)
    print(f"[coin_features] saved → {cache_path}")
    return df


# ── 友善 markdown print（按 atr_pct_med 排序）──────────────────
def print_features_markdown(df: pd.DataFrame) -> None:
    df_sorted = df.sort_values("atr_pct_med", na_position="last").reset_index(drop=True)
    cols = list(df_sorted.columns)
    print("\n## Coin features（按 atr_pct_med 升序）\n")
    print("| " + " | ".join(cols) + " |")
    print("| " + " | ".join("---" for _ in cols) + " |")
    for _, row in df_sorted.iterrows():
        vals = []
        for c in cols:
            v = row[c]
            if pd.isna(v):
                vals.append("—")
            elif isinstance(v, float):
                if c == "volume_quote_med":
                    vals.append(f"{v:,.0f}")
                else:
                    vals.append(f"{v:.4f}".rstrip("0").rstrip("."))
            else:
                vals.append(str(v))
        print("| " + " | ".join(vals) + " |")


if __name__ == "__main__":
    from binance.client import Client
    from dotenv import load_dotenv
    load_dotenv()
    SYMBOLS = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
        "1000PEPEUSDT", "SKYAIUSDT", "XAUUSDT", "XAGUSDT", "CLUSDT",
    ]
    client = Client(os.getenv("BINANCE_API_KEY", ""),
                    os.getenv("BINANCE_SECRET", ""), testnet=False)
    df = compute_coin_features(client, SYMBOLS, months=39)
    print_features_markdown(df)
