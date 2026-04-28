"""
回測腳本 — 裸K + Fibonacci 策略
Walk-forward 模擬：逐根 K 棒重放歷史，使用與真實 bot 完全相同的訊號邏輯

執行方式：
    c:/python312/python.exe scripts/backtest.py
    c:/python312/python.exe scripts/backtest.py --symbol ETHUSDT --tf 4h --months 6
    c:/python312/python.exe scripts/backtest.py --tf 1h --tf 4h   （同時測兩個時間框架）

輸出：
    - 每筆模擬交易結果
    - 勝率、期望值、最大連虧、最大回撤
    - 月度損益摘要
"""
import os
import sys
import math
import argparse
import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import pandas_ta as ta
import numpy as np
from dotenv import load_dotenv
from binance.client import Client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signal_engine import (
    SignalEngine, KEY_FIB_LEVELS, FIB_TOLERANCE,
    FIB_LONG_MAP, FIB_SHORT_MAP, _normalize_fib_key
)
from config import Config

load_dotenv()

logging.basicConfig(
    level=logging.WARNING,   # 回測時只顯示 WARNING 以上，避免太雜
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("backtest")

TAKER_FEE = 0.0004   # 0.04%

# ── 回測參數（從 .env 讀取，與真實 bot 一致）──────────────────────
DEFAULT_SYMBOL    = "BTCUSDT"
DEFAULT_TF        = ["1h", "4h"]
DEFAULT_MONTHS    = 6         # 抓幾個月歷史資料
INITIAL_BALANCE   = 1000.0    # 模擬起始資金（.env 沒有此項，手動設定）
MARGIN_USDT       = float(os.getenv("MARGIN_USDT",       "50"))   # 每筆固定保證金（USDT）
LEVERAGE          = int(os.getenv("MAX_LEVERAGE",        "2"))
MIN_SCORE         = int(os.getenv("MIN_SIGNAL_SCORE",    "3"))
TAKER_FEE_RATE    = TAKER_FEE
COOLDOWN_BARS     = int(os.getenv("COOLDOWN_BARS",       "6"))

# ── NKF 回測參數（同步 Config，確保與正式 bot 一致）──────────────
BT_FIB_TOL        = Config.NKF_FIB_TOL       # 同正式 (0.005)
BT_VOL_MULT       = Config.NKF_VOL_RATIO     # 同正式 (1.3)
BT_SKIP_VOL_RISE  = not Config.NKF_VOL_RISING # 正式要求 vol rising
BT_SKIP_BAD_FIB   = False                     # 同正式（不跳過任何 Fib 位）


# ── 資料結構 ──────────────────────────────────────────────────────
@dataclass
class BtTrade:
    symbol:    str
    direction: str
    entry:     float
    sl:        float
    tp1:       float
    tp2:       float
    qty:       float
    qty_tp1:   float
    qty_tp2:   float
    fib_level: str
    pattern:   str
    score:     int
    timeframe: str
    open_bar:  int             # 開倉的 bar index
    open_time: datetime = None

    # 結果（收盤後填入）
    result:    str = ""        # "TP1", "TP2", "TP1+TP2", "SL", "TIMEOUT"
    exit_price: float = 0.0
    close_bar:  int = 0
    pnl:        float = 0.0
    fee:        float = 0.0
    net_pnl:    float = 0.0
    close_time: datetime = None
    tp1_hit:    bool = False
    strategy:   str  = "naked_k_fib"


# ── MR 結構性過濾 helper（v5：對齊 mean_reversion.py 的 _has_*）────
def _bt_has_rsi_divergence(df, rsi_series, i: int, side: str,
                           lookback: int = 20) -> bool:
    """
    RSI 背離偵測（bar i 為當前根，回看 lookback 根找 swing）：
      LONG  bullish：價格 lower-low + RSI higher-low（差 ≥ 2 點）
      SHORT bearish：價格 higher-high + RSI lower-high
    """
    import numpy as np
    if i < lookback or rsi_series is None:
        return False
    try:
        start = i - lookback + 1
        if side == "LONG":
            arr  = df["low"].iloc[start:i + 1].values
            rarr = rsi_series.iloc[start:i + 1].values
            cur_pos = int(np.argmin(arr))
            if cur_pos < 3:
                return False
            prev_pos = int(np.argmin(arr[:cur_pos - 2]))
            if np.isnan(rarr[cur_pos]) or np.isnan(rarr[prev_pos]):
                return False
            price_ll = arr[cur_pos] < arr[prev_pos]
            rsi_hl   = rarr[cur_pos] > rarr[prev_pos]
            rsi_meaningful = abs(rarr[cur_pos] - rarr[prev_pos]) >= 2.0
            return bool(price_ll and rsi_hl and rsi_meaningful)
        else:
            arr  = df["high"].iloc[start:i + 1].values
            rarr = rsi_series.iloc[start:i + 1].values
            cur_pos = int(np.argmax(arr))
            if cur_pos < 3:
                return False
            prev_pos = int(np.argmax(arr[:cur_pos - 2]))
            if np.isnan(rarr[cur_pos]) or np.isnan(rarr[prev_pos]):
                return False
            price_hh = arr[cur_pos] > arr[prev_pos]
            rsi_lh   = rarr[cur_pos] < rarr[prev_pos]
            rsi_meaningful = abs(rarr[cur_pos] - rarr[prev_pos]) >= 2.0
            return bool(price_hh and rsi_lh and rsi_meaningful)
    except Exception:
        return False


def _bt_has_sr_test(df, i: int, side: str,
                    lookback: int = 30, tolerance: float = 0.015) -> bool:
    """
    結構性 S/R 測試：bar i 的 close 必須接近 lookback 內最近 swing low/high。
    """
    if i < lookback + 1:
        return False
    try:
        cur = float(df["close"].iloc[i])
        if side == "LONG":
            key_level = float(df["low"].iloc[i - lookback:i].min())
            return cur <= key_level * (1 + tolerance) and \
                   cur >= key_level * (1 - tolerance * 2)
        else:
            key_level = float(df["high"].iloc[i - lookback:i].max())
            return cur >= key_level * (1 - tolerance) and \
                   cur <= key_level * (1 + tolerance * 2)
    except Exception:
        return False


# ── BTC Regime 時間序列（回測用，模擬 live MarketContext.current_regime）─
# Module-level cache：避免每幣回測都重抓
_BTC_REGIME_CACHE: dict[int, "pd.Series"] = {}


def _build_btc_regime_series(
    client: Client, months: int
) -> Optional["pd.Series"]:
    """
    建構 BTC regime 時間序列（與 live MarketContext.current_regime 同邏輯）：
      4h ADX>=20 + 日線收盤 > MA50  → "TREND_UP"
      4h ADX>=20 + 日線收盤 < MA50  → "TREND_DOWN"
      4h ADX<20 + 收盤靠 MA50(±3%) → "RANGE"
      其餘                          → "CHOPPY"

    回傳：以 4h K 棒 close_time 為 index 的 Series（值為 regime 字串）
    給策略 backtest 在每根 K 棒入場前查當下 BTC regime 用。
    """
    if months in _BTC_REGIME_CACHE:
        return _BTC_REGIME_CACHE[months]

    print(f"  預計算 BTC regime 時間序列（4h × {months}m + daily）...",
          end="", flush=True)
    try:
        # 多抓 60 天 warmup 給 ADX/MA50
        warmup_months = months + 2
        df_4h    = fetch_klines(client, "BTCUSDT", "4h", warmup_months)
        df_daily = fetch_klines(client, "BTCUSDT", "1d", warmup_months)
    except Exception as e:
        print(f" 失敗：{e}")
        return None

    if len(df_4h) < 30 or len(df_daily) < 50:
        print(" 資料不足，跳過 regime gate")
        return None

    # 4h ADX
    adx_df = ta.adx(df_4h["high"], df_4h["low"], df_4h["close"], length=14)
    df_4h["adx"] = adx_df["ADX_14"] if adx_df is not None else float("nan")
    # 日線 MA50
    df_daily["ma50"] = df_daily["close"].rolling(50).mean()
    # 對齊：每根 4h 取「最近的日線 MA50」（forward fill）
    df_daily_idx = df_daily.set_index("time")[["close", "ma50"]]
    df_4h_idx = df_4h.set_index("time")
    aligned = df_4h_idx.join(
        df_daily_idx.rename(columns={"close": "daily_close",
                                       "ma50": "daily_ma50"}),
        how="left"
    )
    aligned[["daily_close", "daily_ma50"]] = (
        aligned[["daily_close", "daily_ma50"]].ffill()
    )

    def classify(row):
        adx = row.get("adx")
        d_close = row.get("daily_close")
        d_ma = row.get("daily_ma50")
        if pd.isna(adx) or pd.isna(d_close) or pd.isna(d_ma) or d_ma <= 0:
            return "CHOPPY"
        diff_pct = abs(d_close - d_ma) / d_ma
        if adx >= 20:
            return "TREND_UP" if d_close > d_ma else "TREND_DOWN"
        if adx < 20 and diff_pct <= 0.03:
            return "RANGE"
        return "CHOPPY"

    series = aligned.apply(classify, axis=1)
    series.name = "regime"
    print(f" 完成（{len(series)} 點）")
    _BTC_REGIME_CACHE[months] = series
    return series


def _regime_at(series: Optional["pd.Series"], bar_time) -> str:
    """查 bar_time 當下的 BTC regime（取最近一筆 ≤ bar_time 的 4h regime）。

    series None 或查無資料時回 "CHOPPY"（保守，與 live fallback 一致）。
    """
    if series is None:
        return "CHOPPY"
    try:
        # asof：取 ≤ bar_time 的最後一筆
        idx = series.index.asof(bar_time)
        if pd.isna(idx):
            return "CHOPPY"
        return series.loc[idx]
    except Exception:
        return "CHOPPY"


def _regime_allows(regime: str, strategy: str) -> bool:
    """與 live MarketContext.regime_allows 同邏輯。"""
    if strategy == "naked_k_fib":
        return True
    if regime == "TREND_UP":
        return strategy == "momentum_long"
    if regime == "TREND_DOWN":
        return strategy == "breakdown_short"
    if regime == "RANGE":
        return strategy == "mean_reversion"
    return False


# ── K 線下載（幣安期貨）──────────────────────────────────────────
def fetch_klines(client: Client, symbol: str, interval: str,
                 months: int) -> pd.DataFrame:
    """下載最近 N 個月的期貨 K 線"""
    start = datetime.now(timezone.utc) - timedelta(days=30 * months)
    start_ms = int(start.timestamp() * 1000)
    all_klines = []
    limit = 1500

    print(f"  下載 {symbol} {interval} {months}個月歷史資料...", end="", flush=True)
    while True:
        raw = client.futures_klines(
            symbol=symbol, interval=interval,
            startTime=start_ms, limit=limit
        )
        if not raw:
            break
        all_klines.extend(raw)
        last_t = raw[-1][0]
        if len(raw) < limit:
            break
        start_ms = last_t + 1

    print(f" {len(all_klines)} 根")

    df = pd.DataFrame(all_klines, columns=[
        "time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "trades", "tbav", "tbqv", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume", "qav"]:
        df[col] = df[col].astype(float)
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    return df.reset_index(drop=True)


# ── 複用 SignalEngine 的核心方法（不需真實 client）───────────────
class BacktestSignalEngine(SignalEngine):
    """繼承 SignalEngine，覆寫 _get_klines 使用本地資料"""

    def __init__(self, fib_tol: float = 0.008, vol_mult: float = 1.1,
                 skip_vol_rise: bool = False, skip_bad_fib: bool = True):
        super().__init__(client=None)
        self._fib_tol      = fib_tol
        self._vol_mult     = vol_mult
        self._skip_vol_rise = skip_vol_rise
        self._skip_bad_fib  = skip_bad_fib
        # 低 R:R 的 Fib 位（LONG 0.236 TP1=0%, SHORT 0.786 TP1=1.0）
        self._bad_fib_levels = {"0.236", "0.786"}

    def _price_near_fib(self, price: float, fib_levels: dict) -> Optional[str]:
        """覆寫：使用回測專用的 fib_tol"""
        for level, fib_price in fib_levels.items():
            if fib_price == 0:
                continue
            if abs(price - fib_price) / fib_price <= self._fib_tol:
                return level
        return None

    def check_on_bar(self, df_tf: pd.DataFrame, df_daily: pd.DataFrame,
                     bar_idx: int, timeframe: str) -> Optional[object]:
        """
        在 bar_idx 這根 K 棒（已收盤）上跑訊號偵測
        df_tf：已過濾到 bar_idx 之前的資料（不含未來）
        """
        df = df_tf.iloc[:bar_idx + 1].copy().reset_index(drop=True)
        if len(df) < 50:
            return None

        # ── 複用 SignalEngine 的各個方法 ──
        swing_result = self._get_latest_swing_pair(df_daily)
        if not swing_result:
            return None
        swing_h, swing_l, swing_trend = swing_result
        if swing_h <= swing_l:
            return None

        fib_levels = self._calc_fib(swing_h, swing_l)
        price = df["close"].iloc[-1]
        fib_hit = self._price_near_fib(price, fib_levels)
        if not fib_hit:
            return None

        # 跳過低 R:R Fib 位
        if self._skip_bad_fib and fib_hit in self._bad_fib_levels:
            return None

        direction = self._determine_direction(df_daily, swing_trend)
        if not direction:
            return None

        if self._swing_structure_broken(df, direction, swing_h, swing_l):
            return None

        fib_price = fib_levels.get(fib_hit)
        if fib_price and not self._is_fib_fresh(df, fib_price):
            return None

        pattern_result = self._detect_pattern(df, direction)
        if not pattern_result:
            return None
        pattern_name, pattern_strength = pattern_result

        if not self._volume_confirmed(df, period=20, ratio=self._vol_mult):
            return None
        if not self._skip_vol_rise and not self._volume_rising(df):
            return None

        tp_sl = self._calc_fib_tp_sl(direction, fib_hit, swing_h, swing_l)

        if direction == "LONG":
            if tp_sl["sl"] >= price or tp_sl["tp1"] <= price:
                return None
        else:
            if tp_sl["sl"] <= price or tp_sl["tp1"] >= price:
                return None

        # 成交量評分
        vol_score = self._volume_ratio_score(df, period=20)
        exhaustion_bonus = 0
        if direction == "LONG" and self._volume_exhaustion_long(df):
            exhaustion_bonus = 1
        elif direction == "SHORT" and self._volume_exhaustion_short(df):
            exhaustion_bonus = 1

        # btc_weekly_penalty：回測無 market_ctx，設為 0（與正式差異已記錄）
        btc_weekly_penalty = 0

        score = (
            pattern_strength
            + (1 if float(fib_hit) == 0.618 else 0)
            + (1 if swing_trend == ("up" if direction == "LONG" else "down") else 0)
            + vol_score
            + exhaustion_bonus
            + btc_weekly_penalty
        )
        score = max(0, min(5, score))

        from signal_engine import Signal
        return Signal(
            symbol     = "BT",
            direction  = direction,
            entry      = price,
            sl         = tp_sl["sl"],
            tp1        = tp_sl["tp1"],
            tp2        = tp_sl["tp2"],
            fib_level  = fib_hit,
            pattern    = pattern_name,
            score      = score,
            timeframe  = timeframe,
            swing_high = swing_h,
            swing_low  = swing_l,
        )


# ── MR 回測引擎 ──────────────────────────────────────────────────
class BacktestMREngine:
    """
    均值回歸策略回測引擎：直接在本地 DataFrame slice 上跑訊號邏輯，
    複用 MeanReversionStrategy 的數學邏輯，不需要真實 Binance client。
    """

    def check_on_bar(self, df: pd.DataFrame) -> Optional[object]:
        """
        df: 截至當根（已收盤）的本地 K 線資料
        Returns: namedtuple-like 物件，欄位對齊 BtTrade 需要 (entry, sl, tp1, tp2, score)
        或 None
        """
        if len(df) < 50:
            return None

        try:
            rsi = ta.rsi(df["close"], length=Config.MR_RSI_PERIOD)
            bb  = ta.bbands(df["close"], length=Config.MR_BB_PERIOD,
                            std=Config.MR_BB_STD)
            adx_df = ta.adx(df["high"], df["low"], df["close"], length=14)
            atr_s  = ta.atr(df["high"], df["low"], df["close"], length=14)
        except Exception:
            return None

        if rsi is None or bb is None or adx_df is None or atr_s is None:
            return None

        # 自動偵測 pandas_ta bbands 欄位名稱（不同版本格式不同）
        col_u = next((c for c in bb.columns if c.startswith("BBU_")), None)
        col_l = next((c for c in bb.columns if c.startswith("BBL_")), None)
        col_m = next((c for c in bb.columns if c.startswith("BBM_")), None)
        if not col_u or not col_l or not col_m:
            return None

        if rsi.isna().iloc[-1] or bb[col_u].isna().iloc[-1]:
            return None

        rsi_val  = float(rsi.iloc[-1])
        adx_val  = float(adx_df["ADX_14"].iloc[-1])
        bb_upper = float(bb[col_u].iloc[-1])
        bb_lower = float(bb[col_l].iloc[-1])
        bb_mid   = float(bb[col_m].iloc[-1])
        price    = float(df["close"].iloc[-1])
        atr_val  = float(atr_s.iloc[-1])

        # ADX 過濾（MR 只在非趨勢盤運作）
        if adx_val >= 25:
            return None

        # 成交量相對均量
        avg_vol  = float(df["volume"].tail(21).iloc[:-1].mean())
        last_vol = float(df["volume"].iloc[-1])
        vol_ok   = last_vol <= avg_vol * Config.MR_VOL_MULT

        # ── 入場判斷（同正式：無 BB 容差，反轉 K 棒為硬性條件）──
        side = None
        if rsi_val <= Config.MR_RSI_OVERSOLD and price <= bb_lower:
            if self._has_reversal(df, "LONG"):
                side = "LONG"
        elif rsi_val >= Config.MR_RSI_OVERBOUGHT and price >= bb_upper:
            if self._has_reversal(df, "SHORT"):
                side = "SHORT"

        if side is None:
            return None

        # ── 評分（同正式 _score_signal）──────────────────────────
        score = 1
        if (side == "LONG" and rsi_val <= 15) or \
           (side == "SHORT" and rsi_val >= 85):
            score += 1
        bb_width = bb_upper - bb_lower
        if side == "LONG" and price < bb_lower - bb_width * 0.1:
            score += 1
        elif side == "SHORT" and price > bb_upper + bb_width * 0.1:
            score += 1
        try:
            stoch = ta.stochrsi(df["close"])
            if stoch is not None and len(stoch.columns) >= 2:
                k_s = float(stoch.iloc[-1, 0])
                d_s = float(stoch.iloc[-1, 1])
                if side == "LONG" and k_s > d_s and k_s < 20:
                    score += 1
                elif side == "SHORT" and k_s < d_s and k_s > 80:
                    score += 1
        except Exception:
            pass
        try:
            macd = ta.macd(df["close"])
            if macd is not None and macd.shape[1] >= 3:
                hist = macd.iloc[:, 2]
                if side == "LONG" and \
                        float(hist.iloc[-1]) > float(hist.iloc[-2]) and \
                        float(df["close"].iloc[-1]) < float(df["close"].iloc[-2]):
                    score += 1
                elif side == "SHORT" and \
                        float(hist.iloc[-1]) < float(hist.iloc[-2]) and \
                        float(df["close"].iloc[-1]) > float(df["close"].iloc[-2]):
                    score += 1
        except Exception:
            pass
        score = min(score, 5)

        # ── TP/SL（同正式 _calc_tp_sl）─────────────────────────────
        sl_dist = min(Config.MR_SL_PCT * price, atr_val * 1.0)
        sl_dist = max(sl_dist, price * 0.005)
        sl_dist = min(sl_dist, price * 0.03)

        # TP1：ATR-based 短目標（同正式）
        mid_dist = abs(bb_mid - price)
        tp1_dist = min(atr_val * 1.2, mid_dist) if mid_dist > atr_val * 0.5 else atr_val * 1.0
        tp1_dist = max(tp1_dist, price * 0.005)  # 最少 0.5%

        if side == "LONG":
            tp1 = price + tp1_dist
            tp2 = bb_mid if bb_mid > tp1 else tp1 * 1.015
            sl  = price - sl_dist
        else:
            tp1 = price - tp1_dist
            tp2 = bb_mid if bb_mid < tp1 else tp1 * 0.985
            sl  = price + sl_dist

        from types import SimpleNamespace
        return SimpleNamespace(
            direction  = side,
            entry      = price,
            sl         = sl,
            tp1        = tp1,
            tp2        = tp2,
            score      = score,
            fib_level  = "—",
            pattern    = "MR_REVERSAL",
            timeframe  = Config.MR_TIMEFRAME,
        )

    def _has_reversal(self, df: pd.DataFrame, side: str) -> bool:
        """簡化版反轉 K 棒偵測（與 MeanReversionStrategy._has_reversal_candle 相同邏輯）"""
        c    = df.iloc[-1]
        body = abs(float(c["close"]) - float(c["open"]))
        upper_shadow = float(c["high"]) - max(float(c["close"]), float(c["open"]))
        lower_shadow = min(float(c["close"]), float(c["open"])) - float(c["low"])
        if side == "LONG":
            if body > 0 and lower_shadow >= body * 2:
                return True
            if float(c["close"]) > float(c["open"]):
                prev3 = df.iloc[-4:-1]
                if len(prev3) == 3 and all(
                    prev3["close"].iloc[i] < prev3["open"].iloc[i] for i in range(3)
                ):
                    return True
        else:
            if body > 0 and upper_shadow >= body * 2:
                return True
            if float(c["close"]) < float(c["open"]):
                prev3 = df.iloc[-4:-1]
                if len(prev3) == 3 and all(
                    prev3["close"].iloc[i] > prev3["open"].iloc[i] for i in range(3)
                ):
                    return True
        return False


# ── ML 回測引擎 ──────────────────────────────────────────────────
class BacktestMLEngine:
    """
    Momentum Long 策略回測引擎：在本地 DataFrame 上逐根偵測
    阻力突破做多訊號，使用預計算指標（向量化 O(n)）。
    """

    def _find_swing_highs(self, df: pd.DataFrame,
                          left: int = 5, right: int = 5) -> list:
        swings = []
        for i in range(left, len(df) - right):
            window = df["high"].iloc[i - left:i + right + 1]
            if df["high"].iloc[i] == window.max():
                swings.append({"idx": i, "price": float(df["high"].iloc[i])})
        return swings

    def _find_swing_lows(self, df: pd.DataFrame,
                         left: int = 5, right: int = 5) -> list:
        swings = []
        for i in range(left, len(df) - right):
            window = df["low"].iloc[i - left:i + right + 1]
            if df["low"].iloc[i] == window.min():
                swings.append({"idx": i, "price": float(df["low"].iloc[i])})
        return swings

    def _has_bullish_candle(self, df_slice: pd.DataFrame) -> bool:
        if len(df_slice) < 2:
            return False
        c = df_slice.iloc[-1]
        body = abs(float(c["close"]) - float(c["open"]))
        total = float(c["high"]) - float(c["low"])
        if total <= 0:
            return False
        # 大陽線
        if float(c["close"]) > float(c["open"]) and body / total > 0.70:
            return True
        # 錘子
        lower = min(float(c["close"]), float(c["open"])) - float(c["low"])
        if body > 0 and lower >= body * 2:
            return True
        # 多頭吞噬
        prev = df_slice.iloc[-2]
        if (float(prev["close"]) < float(prev["open"]) and
                float(c["close"]) > float(c["open"]) and
                float(c["open"]) <= float(prev["close"]) and
                float(c["close"]) >= float(prev["open"])):
            return True
        return False


# ── ML 主回測邏輯 ───────────────────────────────────────────────
def run_backtest_ml(client: Client, symbol: str, months: int,
                    debug: bool = False,
                    regime_series=None) -> list:
    """
    Momentum Long 策略回測：使用 ML_TIMEFRAME K 線逐根回放。
    指標在完整 DataFrame 上一次性計算（向量化 O(n)）。
    只做多：突破近 N 根最高點 + 放量 + 多頭結構。
    """
    tf = Config.ML_TIMEFRAME
    print(f"\n[{symbol} ML {tf}] 回測開始")

    df_tf = fetch_klines(client, symbol, tf, months)
    if len(df_tf) < 100:
        print(f"  資料不足（{len(df_tf)} 根），跳過")
        return []

    # ── 一次性預計算所有指標 ─────────────────────────────────────
    print(f"  預計算指標...", end="", flush=True)
    ema20_s   = ta.ema(df_tf["close"], length=20)
    ema50_s   = ta.ema(df_tf["close"], length=50)
    adx_full  = ta.adx(df_tf["high"], df_tf["low"], df_tf["close"], length=14)
    atr_full  = ta.atr(df_tf["high"], df_tf["low"], df_tf["close"], length=14)
    macd_full = ta.macd(df_tf["close"])
    avg_vol_s = df_tf["volume"].rolling(21).mean().shift(1)

    # 滾動最高點（近 ML_LOOKBACK_BARS 根的 high 最大值，不含當根）
    lookback = Config.ML_LOOKBACK_BARS
    rolling_high_s = df_tf["high"].rolling(lookback).max().shift(1)
    print(" 完成")

    warmup       = max(60, lookback + 1)
    engine       = BacktestMLEngine()
    trades       = []
    balance      = INITIAL_BALANCE
    cooldown_until = -1
    timeout_bars = Config.ML_TIMEOUT_BARS
    min_score    = Config.ML_MIN_SCORE

    dbg = {"cooldown": 0, "no_sig": 0, "low_score": 0, "bad_pos": 0,
           "regime_block": 0, "v2_htf_block": 0, "v2_no_entry": 0,
           "signals": 0}

    # ── ML v2 旗標 + HTF 預計算 ─────────────────────────────────
    v2_enabled = bool(getattr(Config, "ML_V2_ENABLED", False))
    v2_burst   = v2_enabled and bool(getattr(Config, "ML_V2_VOL_BURST_ENABLED", True))
    v2_htf     = v2_enabled and bool(getattr(Config, "ML_V2_HTF_ENABLED", True))
    burst_mult = float(getattr(Config, "ML_V2_VOL_BURST_MULT", 3.0))
    burst_close_pct = float(getattr(Config, "ML_V2_VOL_BURST_CLOSE_PCT", 0.7))

    htf_close_arr = None
    htf_ema_arr   = None
    htf_slope_arr = None
    if v2_htf:
        try:
            htf_tf      = getattr(Config, "ML_V2_HTF_TIMEFRAME", "4h")
            htf_period  = int(getattr(Config, "ML_V2_HTF_EMA_PERIOD", 50))
            htf_slope_n = int(getattr(Config, "ML_V2_HTF_SLOPE_BARS", 5))
            print(f"  下載 ML HTF {htf_tf} 計算 EMA{htf_period}...", end="", flush=True)
            df_htf = fetch_klines(client, symbol, htf_tf, months + 2)
            if len(df_htf) >= htf_period + htf_slope_n + 5:
                htf_ema = ta.ema(df_htf["close"], length=htf_period)
                htf_slope = (htf_ema - htf_ema.shift(htf_slope_n)) / htf_ema.shift(htf_slope_n)
                df_htf_aligned = pd.DataFrame({
                    "close_time": pd.to_datetime(df_htf["close_time"], unit="ms"),
                    "htf_close":  df_htf["close"].values,
                    "htf_ema":    htf_ema.values,
                    "htf_slope":  htf_slope.values,
                }).dropna()
                df_1h_idx = df_tf[["time"]].copy()
                df_1h_idx["_orig_idx"] = range(len(df_1h_idx))
                merged = pd.merge_asof(
                    df_1h_idx.sort_values("time"),
                    df_htf_aligned.sort_values("close_time"),
                    left_on="time", right_on="close_time",
                    direction="backward",
                )
                merged = merged.sort_values("_orig_idx").reset_index(drop=True)
                htf_close_arr = merged["htf_close"].values
                htf_ema_arr   = merged["htf_ema"].values
                htf_slope_arr = merged["htf_slope"].values
                print(" 完成")
            else:
                print(" 資料不足，HTF 過濾停用")
        except Exception as e:
            print(f" 失敗（{e}），HTF 過濾停用")

    print(f"  掃描 {len(df_tf) - warmup} 根 K 棒...", end="", flush=True)

    for i in range(warmup, len(df_tf) - 1):
        if i <= cooldown_until:
            dbg["cooldown"] += 1
            continue

        # ── 讀取預計算指標值 ──────────────────────────────────────
        ema20_val = ema20_s.iloc[i] if ema20_s is not None else float("nan")
        ema50_val = ema50_s.iloc[i] if ema50_s is not None else float("nan")
        adx_val   = adx_full["ADX_14"].iloc[i] if adx_full is not None else float("nan")
        atr_val   = atr_full.iloc[i] if atr_full is not None else float("nan")
        price     = float(df_tf["close"].iloc[i])
        avg_vol_i = avg_vol_s.iloc[i]
        resistance = rolling_high_s.iloc[i]

        if pd.isna(ema20_val) or pd.isna(ema50_val) or pd.isna(adx_val) or \
                pd.isna(atr_val) or pd.isna(avg_vol_i) or pd.isna(resistance):
            dbg["no_sig"] += 1
            continue

        ema20_val = float(ema20_val)
        ema50_val = float(ema50_val)
        adx_val   = float(adx_val)
        atr_val   = float(atr_val)
        resistance = float(resistance)

        # ── 基本條件：多頭結構 ───────────────────────────────────
        if ema20_val <= ema50_val:
            dbg["no_sig"] += 1
            continue
        if price <= ema50_val:
            dbg["no_sig"] += 1
            continue
        if adx_val < 20:
            dbg["no_sig"] += 1
            continue

        last_vol = float(df_tf["volume"].iloc[i])

        # ── 標準路徑：突破 + 放量 ──────────────────────────────
        breakout_ok = price > resistance and \
                      (price - resistance) / resistance >= 0.001
        vol_ok = last_vol >= float(avg_vol_i) * Config.ML_VOL_MULT
        path_breakout = breakout_ok and vol_ok

        # ── ML v2 Volume Burst 路徑 ─────────────────────────────
        path_burst = False
        if v2_burst:
            cur_high = float(df_tf["high"].iloc[i])
            cur_low  = float(df_tf["low"].iloc[i])
            cur_range = cur_high - cur_low
            close_pos = (price - cur_low) / cur_range if cur_range > 0 else 0
            if last_vol >= float(avg_vol_i) * burst_mult and close_pos >= burst_close_pct:
                path_burst = True

        if not (path_breakout or path_burst):
            dbg["v2_no_entry"] += 1 if v2_enabled else 0
            dbg["no_sig"] += 1 if not v2_enabled else 0
            continue

        # ── ML v2 HTF 過濾 ──────────────────────────────────────
        if v2_htf and htf_close_arr is not None:
            htf_c = htf_close_arr[i] if i < len(htf_close_arr) else None
            htf_e = htf_ema_arr[i]   if i < len(htf_ema_arr)   else None
            htf_s = htf_slope_arr[i] if i < len(htf_slope_arr) else None
            if htf_c is not None and htf_e is not None and htf_s is not None \
                    and not pd.isna(htf_c) and not pd.isna(htf_e) and not pd.isna(htf_s):
                htf_c = float(htf_c); htf_e = float(htf_e); htf_s = float(htf_s)
                min_slope = float(getattr(Config, "ML_V2_HTF_MIN_SLOPE_PCT", 0.003))
                # close 必須在 EMA 上方 + EMA 上行 ≥ min_slope
                if htf_c <= htf_e or htf_s < min_slope:
                    dbg["v2_htf_block"] += 1
                    continue

        # ── 評分 ─────────────────────────────────────────────────
        score = 1  # 基礎分

        if adx_val > 30:
            score += 1
        if float(avg_vol_i) > 0 and last_vol >= float(avg_vol_i) * 2.0:
            score += 1
        # 多頭 K 棒
        df_slice = df_tf.iloc[max(0, i - 1):i + 1]
        if engine._has_bullish_candle(df_slice):
            score += 1
        # MACD 多頭加速
        try:
            if macd_full is not None and macd_full.shape[1] >= 3:
                hist = macd_full.iloc[:, 2]
                if not pd.isna(hist.iloc[i]) and not pd.isna(hist.iloc[i - 1]):
                    if float(hist.iloc[i]) > 0 and float(hist.iloc[i]) > float(hist.iloc[i - 1]):
                        score += 1
        except Exception:
            pass

        score = min(score, 5)

        if score < min_score:
            dbg["low_score"] += 1
            continue

        # ── Regime gate（v5：對齊 live MarketContext）──────────
        if regime_series is not None:
            bar_time = df_tf["time"].iloc[i]
            regime = _regime_at(regime_series, bar_time)
            if not _regime_allows(regime, "momentum_long"):
                dbg["regime_block"] += 1
                continue

        dbg["signals"] += 1

        # ── TP/SL（Fib extension）────────────────────────────────
        df_recent = df_tf.iloc[max(0, i - 60):i + 1].reset_index(drop=True)
        swing_highs = engine._find_swing_highs(df_recent, left=5, right=5)
        swing_lows = engine._find_swing_lows(df_recent, left=5, right=5)

        if swing_highs and swing_lows:
            sh = swing_highs[-1]["price"]
            sl_swing = swing_lows[-1]["price"]
            if sh > sl_swing:
                diff = sh - sl_swing
                tp1 = sh + diff * 0.272
                tp2 = sh + diff * 0.618
            else:
                tp1 = price * 1.03
                tp2 = price * 1.05
        else:
            tp1 = price * 1.03
            tp2 = price * 1.05

        sl = resistance - Config.ML_SL_ATR_MULT * atr_val
        sl = max(sl, price * 0.95)
        sl = min(sl, price * 0.997)

        if tp1 <= price:
            tp1 = price * 1.015
        if tp2 <= tp1:
            tp2 = tp1 * 1.015

        pos = calc_position(balance, price, sl, tp1, tp2)
        if not pos:
            dbg["bad_pos"] += 1
            continue

        trade = BtTrade(
            symbol    = symbol,
            direction = "LONG",
            entry     = price,
            sl        = sl,
            tp1       = tp1,
            tp2       = tp2,
            qty       = pos["qty"],
            qty_tp1   = pos["qty_tp1"],
            qty_tp2   = pos["qty_tp2"],
            fib_level = "—",
            pattern   = "ML_BREAKOUT",
            score     = score,
            timeframe = tf,
            open_bar  = i,
            open_time = df_tf["time"].iloc[i],
            strategy  = "momentum_long",
        )

        df_future = df_tf.iloc[i + 1:].reset_index(drop=True)
        trade = simulate_trade(trade, df_future, max_bars=timeout_bars)

        if trade.result in ("", "OPEN"):
            continue

        balance += trade.net_pnl
        trades.append(trade)

        if "SL" in trade.result:
            cooldown_until = trade.close_bar + COOLDOWN_BARS

    print(f" 找到 {len(trades)} 筆訊號")
    if debug or len(trades) == 0:
        scanned = len(df_tf) - warmup - dbg["cooldown"]
        print(f"\n  ── ML 診斷（為何沒有入場？）──")
        print(f"  總掃描根數：{scanned}")
        print(f"  ├─ 沒通過訊號過濾：{dbg['no_sig']} 根  （EMA/ADX/突破/放量）")
        print(f"  ├─ 評分不足 (<{min_score})：{dbg['low_score']} 根")
        print(f"  ├─ 倉位計算失敗   ：{dbg['bad_pos']} 根  （R:R < 1.2 或 SL% 範圍外）")
        print(f"  └─ 通過全部過濾   ：{dbg['signals']} 根")
    return trades


# ── BD 回測引擎 ──────────────────────────────────────────────────
class BacktestBDEngine:
    """
    Breakdown Short 策略回測引擎：在本地 DataFrame 上逐根偵測
    支撐突破做空訊號，使用預計算指標（向量化 O(n)）。
    """

    def _find_swing_highs(self, df: pd.DataFrame,
                          left: int = 5, right: int = 5) -> list:
        swings = []
        for i in range(left, len(df) - right):
            window = df["high"].iloc[i - left:i + right + 1]
            if df["high"].iloc[i] == window.max():
                swings.append({"idx": i, "price": float(df["high"].iloc[i])})
        return swings

    def _find_swing_lows(self, df: pd.DataFrame,
                         left: int = 5, right: int = 5) -> list:
        swings = []
        for i in range(left, len(df) - right):
            window = df["low"].iloc[i - left:i + right + 1]
            if df["low"].iloc[i] == window.min():
                swings.append({"idx": i, "price": float(df["low"].iloc[i])})
        return swings

    def _has_bearish_candle(self, df_slice: pd.DataFrame) -> bool:
        if len(df_slice) < 2:
            return False
        c = df_slice.iloc[-1]
        body = abs(float(c["close"]) - float(c["open"]))
        total = float(c["high"]) - float(c["low"])
        if total <= 0:
            return False
        # 大陰線
        if float(c["close"]) < float(c["open"]) and body / total > 0.70:
            return True
        # 射擊之星
        upper = float(c["high"]) - max(float(c["close"]), float(c["open"]))
        if body > 0 and upper >= body * 2:
            return True
        # 空頭吞噬
        prev = df_slice.iloc[-2]
        if (float(prev["close"]) > float(prev["open"]) and
                float(c["close"]) < float(c["open"]) and
                float(c["open"]) >= float(prev["close"]) and
                float(c["close"]) <= float(prev["open"])):
            return True
        return False


# ── BD 主回測邏輯 ───────────────────────────────────────────────
def run_backtest_bd(client: Client, symbol: str, months: int, *,
                    regime_series=None,
                    debug: bool = False) -> list:
    """
    Breakdown Short 策略回測：使用 BD_TIMEFRAME K 線逐根回放。
    指標在完整 DataFrame 上一次性計算（向量化 O(n)）。
    只做空：跌破近 N 根最低點 + 放量 + 空頭結構。
    """
    tf = Config.BD_TIMEFRAME
    print(f"\n[{symbol} BD {tf}] 回測開始")

    df_tf = fetch_klines(client, symbol, tf, months)
    if len(df_tf) < 100:
        print(f"  資料不足（{len(df_tf)} 根），跳過")
        return []

    # ── 一次性預計算所有指標 ─────────────────────────────────────
    print(f"  預計算指標...", end="", flush=True)
    ema20_s   = ta.ema(df_tf["close"], length=20)
    ema50_s   = ta.ema(df_tf["close"], length=50)
    adx_full  = ta.adx(df_tf["high"], df_tf["low"], df_tf["close"], length=14)
    atr_full  = ta.atr(df_tf["high"], df_tf["low"], df_tf["close"], length=14)
    macd_full = ta.macd(df_tf["close"])
    avg_vol_s = df_tf["volume"].rolling(21).mean().shift(1)

    # 滾動最低點（近 BD_LOOKBACK_BARS 根的 low 最小值，不含當根）
    lookback = Config.BD_LOOKBACK_BARS
    rolling_low_s = df_tf["low"].rolling(lookback).min().shift(1)
    print(" 完成")

    warmup       = max(60, lookback + 1)
    engine       = BacktestBDEngine()
    trades       = []
    balance      = INITIAL_BALANCE
    cooldown_until = -1
    timeout_bars = Config.BD_TIMEOUT_BARS
    min_score    = Config.BD_MIN_SCORE

    dbg = {"cooldown": 0, "no_sig": 0, "low_score": 0, "bad_pos": 0,
           "v2_near_support": 0, "v2_no_lh": 0, "v2_no_confirm": 0,
           "signals": 0}

    # BD v2 旗標
    v2_enabled = bool(getattr(Config, "BD_V2_ENABLED", False))

    print(f"  掃描 {len(df_tf) - warmup} 根 K 棒...", end="", flush=True)

    for i in range(warmup, len(df_tf) - 1):
        if i <= cooldown_until:
            dbg["cooldown"] += 1
            continue

        # ── 讀取預計算指標值 ──────────────────────────────────────
        ema20_val = ema20_s.iloc[i] if ema20_s is not None else float("nan")
        ema50_val = ema50_s.iloc[i] if ema50_s is not None else float("nan")
        adx_val   = adx_full["ADX_14"].iloc[i] if adx_full is not None else float("nan")
        atr_val   = atr_full.iloc[i] if atr_full is not None else float("nan")
        price     = float(df_tf["close"].iloc[i])
        avg_vol_i = avg_vol_s.iloc[i]
        support   = rolling_low_s.iloc[i]

        if pd.isna(ema20_val) or pd.isna(ema50_val) or pd.isna(adx_val) or \
                pd.isna(atr_val) or pd.isna(avg_vol_i) or pd.isna(support):
            dbg["no_sig"] += 1
            continue

        ema20_val = float(ema20_val)
        ema50_val = float(ema50_val)
        adx_val   = float(adx_val)
        atr_val   = float(atr_val)
        support   = float(support)

        # ── 基本條件：空頭結構 ───────────────────────────────────
        if ema20_val >= ema50_val:
            dbg["no_sig"] += 1
            continue
        if price >= ema50_val:
            dbg["no_sig"] += 1
            continue
        if adx_val < 20:
            dbg["no_sig"] += 1
            continue

        # ── BD v2 結構過濾 ───────────────────────────────────────
        if v2_enabled and getattr(Config, "BD_V2_REJECT_NEAR_SUPPORT", True):
            sup_lookback = int(getattr(Config, "BD_V2_SUPPORT_LOOKBACK", 30))
            sup_mult     = float(getattr(Config, "BD_V2_SUPPORT_ATR_MULT", 1.0))
            if i >= sup_lookback + 1:
                deepest_low = float(df_tf["low"].iloc[i - sup_lookback:i].min())
                if price - deepest_low < sup_mult * atr_val:
                    dbg["v2_near_support"] += 1
                    continue

        if v2_enabled and getattr(Config, "BD_V2_REQUIRE_LOWER_HIGHS", True):
            lh_lookback = int(getattr(Config, "BD_V2_LH_LOOKBACK", 30))
            if i >= lh_lookback + 2:
                hs = df_tf["high"].iloc[i - lh_lookback:i].values
                swing_highs = []
                for k in range(2, len(hs) - 2):
                    if hs[k] >= hs[k-1] and hs[k] >= hs[k-2] \
                            and hs[k] >= hs[k+1] and hs[k] >= hs[k+2]:
                        swing_highs.append(float(hs[k]))
                if len(swing_highs) < 2 or swing_highs[-1] >= max(swing_highs[:-1]):
                    dbg["v2_no_lh"] += 1
                    continue

        # ── 突破偵測：收盤跌破近 N 根最低點 ─────────────────────
        if price >= support:
            dbg["no_sig"] += 1
            continue
        break_pct = (support - price) / support
        if break_pct < 0.001:
            dbg["no_sig"] += 1
            continue

        # ── 放量確認 ─────────────────────────────────────────────
        last_vol = float(df_tf["volume"].iloc[i])

        # ── BD v2 multi-bar confirmation ────────────────────────
        if v2_enabled and getattr(Config, "BD_V2_REQUIRE_CONFIRM", True):
            if i < 2:
                continue
            # 修：i-1 的 rolling_low 是 min([i-1-N..i-2])，不是 support[i]
            #    support[i] 包含 low[i-1]，prev_close >= support[i] 恆真 → bug
            prev_support = (
                float(rolling_low_s.iloc[i - 1])
                if i - 1 < len(rolling_low_s) else float("nan")
            )
            if pd.isna(prev_support):
                dbg["v2_no_confirm"] += 1
                continue
            prev_close = float(df_tf["close"].iloc[i - 1])
            # i-1 必須跌破自己的 rolling_low（首次破位）
            if prev_close >= prev_support:
                dbg["v2_no_confirm"] += 1
                continue
            # i 必須 close 更低（持續下行）
            if price >= prev_close:
                dbg["v2_no_confirm"] += 1
                continue
            # 兩根合計量能
            prev_vol = float(df_tf["volume"].iloc[i - 1])
            if last_vol + prev_vol < float(avg_vol_i) * Config.BD_VOL_MULT * 2:
                dbg["v2_no_confirm"] += 1
                continue
        else:
            if last_vol < float(avg_vol_i) * Config.BD_VOL_MULT:
                dbg["no_sig"] += 1
                continue

        # ── 評分 ─────────────────────────────────────────────────
        score = 1  # 基礎分

        if adx_val > 30:
            score += 1
        if float(avg_vol_i) > 0 and last_vol >= float(avg_vol_i) * 2.0:
            score += 1
        # 空頭 K 棒
        df_slice = df_tf.iloc[max(0, i - 1):i + 1]
        if engine._has_bearish_candle(df_slice):
            score += 1
        # MACD 空頭加速
        try:
            if macd_full is not None and macd_full.shape[1] >= 3:
                hist = macd_full.iloc[:, 2]
                if not pd.isna(hist.iloc[i]) and not pd.isna(hist.iloc[i - 1]):
                    if float(hist.iloc[i]) < 0 and float(hist.iloc[i]) < float(hist.iloc[i - 1]):
                        score += 1
        except Exception:
            pass

        score = min(score, 5)

        if score < min_score:
            dbg["low_score"] += 1
            continue

        # ── Regime gate（v5：對齊 live MarketContext）──────────
        if regime_series is not None:
            bar_time = df_tf["time"].iloc[i]
            regime = _regime_at(regime_series, bar_time)
            if not _regime_allows(regime, "breakdown_short"):
                dbg.setdefault("regime_block", 0)
                dbg["regime_block"] += 1
                continue

        dbg["signals"] += 1

        # ── TP/SL（Fib extension）────────────────────────────────
        # 找最近的 swing pair
        df_recent = df_tf.iloc[max(0, i - 60):i + 1].reset_index(drop=True)
        swing_highs = engine._find_swing_highs(df_recent, left=5, right=5)
        swing_lows = engine._find_swing_lows(df_recent, left=5, right=5)

        if swing_highs and swing_lows:
            sh = swing_highs[-1]["price"]
            sl_swing = swing_lows[-1]["price"]
            if sh > sl_swing:
                diff = sh - sl_swing
                tp1 = sl_swing - diff * 0.272
                tp2 = sl_swing - diff * 0.618
            else:
                tp1 = price * 0.97
                tp2 = price * 0.95
        else:
            tp1 = price * 0.97
            tp2 = price * 0.95

        sl = support + Config.BD_SL_ATR_MULT * atr_val
        sl = min(sl, price * 1.05)
        sl = max(sl, price * 1.003)

        if tp1 >= price:
            tp1 = price * 0.985
        if tp2 >= tp1:
            tp2 = tp1 * 0.985

        pos = calc_position(balance, price, sl, tp1, tp2)
        if not pos:
            dbg["bad_pos"] += 1
            continue

        trade = BtTrade(
            symbol    = symbol,
            direction = "SHORT",
            entry     = price,
            sl        = sl,
            tp1       = tp1,
            tp2       = tp2,
            qty       = pos["qty"],
            qty_tp1   = pos["qty_tp1"],
            qty_tp2   = pos["qty_tp2"],
            fib_level = "—",
            pattern   = "BD_BREAKDOWN",
            score     = score,
            timeframe = tf,
            open_bar  = i,
            open_time = df_tf["time"].iloc[i],
            strategy  = "breakdown_short",
        )

        df_future = df_tf.iloc[i + 1:].reset_index(drop=True)
        trade = simulate_trade(trade, df_future, max_bars=timeout_bars)

        if trade.result in ("", "OPEN"):
            continue

        balance += trade.net_pnl
        trades.append(trade)

        if "SL" in trade.result:
            cooldown_until = trade.close_bar + COOLDOWN_BARS

    print(f" 找到 {len(trades)} 筆訊號")
    if debug or len(trades) == 0:
        scanned = len(df_tf) - warmup - dbg["cooldown"]
        print(f"\n  ── BD 診斷（為何沒有入場？）──")
        print(f"  總掃描根數：{scanned}")
        print(f"  ├─ 沒通過訊號過濾：{dbg['no_sig']} 根  （EMA/ADX/突破/放量）")
        print(f"  ├─ 評分不足 (<{min_score})：{dbg['low_score']} 根")
        print(f"  ├─ 倉位計算失敗   ：{dbg['bad_pos']} 根  （R:R < 1.2 或 SL% 範圍外）")
        print(f"  └─ 通過全部過濾   ：{dbg['signals']} 根")
    return trades


# ── 倉位計算（脫離 client，使用模擬餘額；同 risk_manager.calc_position）──
def calc_position(balance: float, entry: float, sl: float,
                  tp1: float, tp2: float, min_rr: float = 1.2,
                  tp1_split_pct: float = 0.5) -> Optional[dict]:
    sl_pct = abs(entry - sl) / entry
    if sl_pct < 0.003 or sl_pct > 0.12:
        return None

    if balance < MARGIN_USDT:
        return None

    margin   = MARGIN_USDT
    notional = margin * LEVERAGE
    qty      = notional / entry

    qty = max(round(qty, 3), 0.001)             # 同正式：3 位精度
    qty_tp1 = round(qty * tp1_split_pct, 3)      # 同正式：依 tp1_split_pct 分倉
    qty_tp2 = qty - qty_tp1
    if qty_tp1 < 0.001:
        qty_tp1 = qty
        qty_tp2 = 0.0

    # 手續費估算
    fee_open  = qty * entry * TAKER_FEE_RATE
    fee_if_sl = qty * sl * TAKER_FEE_RATE + fee_open
    fee_if_tp = fee_open + qty_tp1 * tp1 * TAKER_FEE_RATE + qty_tp2 * tp2 * TAKER_FEE_RATE

    raw_risk = abs(entry - sl) * qty
    raw_reward = (abs(tp1 - entry) * qty_tp1 + abs(tp2 - entry) * qty_tp2)
    net_rr = (raw_reward - fee_if_tp) / (raw_risk + fee_if_sl) if (raw_risk + fee_if_sl) > 0 else 0

    if net_rr < min_rr:
        return None

    return {"qty": qty, "qty_tp1": qty_tp1, "qty_tp2": qty_tp2, "margin": margin}


# ── 模擬交易結果（逐根比對後續 K 棒）───────────────────────────────
def simulate_trade(trade: BtTrade, df_future: pd.DataFrame,
                   max_bars: int = 48) -> BtTrade:
    """
    從開倉後，逐根 K 棒檢查是否觸發 SL / TP1 / TP2
    max_bars：最多持倉幾根（超過就以市價平倉）
    """
    tp1_hit = False
    remaining_qty = trade.qty

    for i, (_, bar) in enumerate(df_future.iterrows()):
        if i >= max_bars:
            # 超時平倉
            exit_p = bar["close"]
            pnl = ((exit_p - trade.entry) * remaining_qty
                   if trade.direction == "LONG"
                   else (trade.entry - exit_p) * remaining_qty)
            fee = exit_p * remaining_qty * TAKER_FEE_RATE + trade.qty * trade.entry * TAKER_FEE_RATE
            trade.result     = "TIMEOUT"
            trade.exit_price = exit_p
            trade.close_bar  = trade.open_bar + i
            trade.pnl        = round(pnl, 4)
            trade.fee        = round(fee, 4)
            trade.net_pnl    = round(pnl - fee, 4)
            trade.close_time = bar["time"]
            trade.tp1_hit    = tp1_hit
            return trade

        high, low = bar["high"], bar["low"]

        if trade.direction == "LONG":
            # ── SL 觸發 ──
            if low <= trade.sl:
                exit_p = trade.sl
                sl_qty = remaining_qty
                pnl = (exit_p - trade.entry) * sl_qty
                fee = (exit_p * sl_qty + trade.qty * trade.entry) * TAKER_FEE_RATE
                tp1_pnl = ((trade.tp1 - trade.entry) * (trade.qty - remaining_qty)
                           if tp1_hit else 0)
                tp1_fee = (trade.tp1 * (trade.qty - remaining_qty) * TAKER_FEE_RATE
                           if tp1_hit else 0)
                total_pnl = pnl + tp1_pnl
                total_fee = fee + tp1_fee
                trade.result     = "TP1+SL" if tp1_hit else "SL"
                trade.exit_price = exit_p
                trade.close_bar  = trade.open_bar + i
                trade.pnl        = round(total_pnl, 4)
                trade.fee        = round(total_fee, 4)
                trade.net_pnl    = round(total_pnl - total_fee, 4)
                trade.close_time = bar["time"]
                trade.tp1_hit    = tp1_hit
                return trade

            # ── TP1 觸發（部分平倉）──
            if not tp1_hit and high >= trade.tp1:
                tp1_hit = True
                remaining_qty = trade.qty_tp2

            # ── TP2 觸發（剩餘平倉）──
            if tp1_hit and high >= trade.tp2:
                tp2_pnl = (trade.tp2 - trade.entry) * trade.qty_tp2
                tp1_pnl = (trade.tp1 - trade.entry) * trade.qty_tp1
                fee = (trade.qty * trade.entry +
                       trade.qty_tp1 * trade.tp1 +
                       trade.qty_tp2 * trade.tp2) * TAKER_FEE_RATE
                trade.result     = "TP1+TP2"
                trade.exit_price = trade.tp2
                trade.close_bar  = trade.open_bar + i
                trade.pnl        = round(tp1_pnl + tp2_pnl, 4)
                trade.fee        = round(fee, 4)
                trade.net_pnl    = round(tp1_pnl + tp2_pnl - fee, 4)
                trade.close_time = bar["time"]
                trade.tp1_hit    = True
                return trade

        else:  # SHORT
            # ── SL 觸發 ──
            if high >= trade.sl:
                exit_p = trade.sl
                sl_qty = remaining_qty
                pnl = (trade.entry - exit_p) * sl_qty
                fee = (exit_p * sl_qty + trade.qty * trade.entry) * TAKER_FEE_RATE
                tp1_pnl = ((trade.entry - trade.tp1) * (trade.qty - remaining_qty)
                           if tp1_hit else 0)
                tp1_fee = (trade.tp1 * (trade.qty - remaining_qty) * TAKER_FEE_RATE
                           if tp1_hit else 0)
                total_pnl = pnl + tp1_pnl
                total_fee = fee + tp1_fee
                trade.result     = "TP1+SL" if tp1_hit else "SL"
                trade.exit_price = exit_p
                trade.close_bar  = trade.open_bar + i
                trade.pnl        = round(total_pnl, 4)
                trade.fee        = round(total_fee, 4)
                trade.net_pnl    = round(total_pnl - total_fee, 4)
                trade.close_time = bar["time"]
                trade.tp1_hit    = tp1_hit
                return trade

            # ── TP1 觸發 ──
            if not tp1_hit and low <= trade.tp1:
                tp1_hit = True
                remaining_qty = trade.qty_tp2

            # ── TP2 觸發 ──
            if tp1_hit and low <= trade.tp2:
                tp2_pnl = (trade.entry - trade.tp2) * trade.qty_tp2
                tp1_pnl = (trade.entry - trade.tp1) * trade.qty_tp1
                fee = (trade.qty * trade.entry +
                       trade.qty_tp1 * trade.tp1 +
                       trade.qty_tp2 * trade.tp2) * TAKER_FEE_RATE
                trade.result     = "TP1+TP2"
                trade.exit_price = trade.tp2
                trade.close_bar  = trade.open_bar + i
                trade.pnl        = round(tp1_pnl + tp2_pnl, 4)
                trade.fee        = round(fee, 4)
                trade.net_pnl    = round(tp1_pnl + tp2_pnl - fee, 4)
                trade.close_time = bar["time"]
                trade.tp1_hit    = True
                return trade

    # 沒撞到任何條件（資料不夠）
    trade.result = "OPEN"
    return trade


# ── MR 主回測邏輯 ───────────────────────────────────────────────
def run_backtest_mr(client: Client, symbol: str, months: int,
                    debug: bool = False, adx_max: float = 25.0, *,
                    regime_series=None) -> list:
    """
    均值回歸策略回測：使用 MR_TIMEFRAME K 線逐根回放。
    指標在完整 DataFrame 上一次性計算（矢量化，O(n) 代替原 O(n²)）。
    超時平倉：MR_TIMEOUT_BARS 根後強制平倉。
    """
    tf = Config.MR_TIMEFRAME
    print(f"\n[{symbol} MR {tf}] 回測開始")

    df_tf = fetch_klines(client, symbol, tf, months)
    if len(df_tf) < 100:
        print(f"  資料不足（{len(df_tf)} 根），跳過")
        return []

    # ── 一次性預計算所有指標（大幅提速）─────────────────────────────
    print(f"  預計算指標...", end="", flush=True)
    rsi_s    = ta.rsi(df_tf["close"], length=Config.MR_RSI_PERIOD)
    bb_full  = ta.bbands(df_tf["close"], length=Config.MR_BB_PERIOD,
                         std=Config.MR_BB_STD)
    adx_full = ta.adx(df_tf["high"], df_tf["low"], df_tf["close"], length=14)
    atr_full = ta.atr(df_tf["high"], df_tf["low"], df_tf["close"], length=14)
    stoch_full = ta.stochrsi(df_tf["close"])
    macd_full  = ta.macd(df_tf["close"])
    avg_vol_s  = df_tf["volume"].rolling(21).mean().shift(1)  # 前20根均量（不含當棒）

    col_u = next((c for c in bb_full.columns if c.startswith("BBU_")), None) if bb_full is not None else None
    col_l = next((c for c in bb_full.columns if c.startswith("BBL_")), None) if bb_full is not None else None
    col_m = next((c for c in bb_full.columns if c.startswith("BBM_")), None) if bb_full is not None else None
    if not col_u or not col_l or not col_m:
        print(f"\n  [ERROR] BB columns not detected")
        return []
    print(" 完成")

    warmup         = 60
    engine         = BacktestMREngine()
    trades         = []
    balance        = INITIAL_BALANCE
    cooldown_until = -1
    timeout_bars   = Config.MR_TIMEOUT_BARS
    min_score      = Config.MR_MIN_SCORE

    dbg = {"cooldown": 0, "no_sig": 0, "low_score": 0, "bad_pos": 0,
           "no_div": 0, "no_sr": 0, "signals": 0}

    print(f"  掃描 {len(df_tf) - warmup} 根 K 棒...", end="", flush=True)

    for i in range(warmup, len(df_tf) - 1):
        if i <= cooldown_until:
            dbg["cooldown"] += 1
            continue

        # ── 讀取預計算指標值 ──────────────────────────────────────
        rsi_val  = rsi_s.iloc[i]
        adx_val  = adx_full["ADX_14"].iloc[i] if adx_full is not None else float("nan")
        bb_upper = bb_full[col_u].iloc[i]
        bb_lower = bb_full[col_l].iloc[i]
        bb_mid   = bb_full[col_m].iloc[i]
        atr_val  = atr_full.iloc[i] if atr_full is not None else float("nan")
        price    = float(df_tf["close"].iloc[i])
        avg_vol_i = avg_vol_s.iloc[i]

        if pd.isna(rsi_val) or pd.isna(adx_val) or pd.isna(bb_upper) or \
                pd.isna(atr_val) or pd.isna(avg_vol_i):
            dbg["no_sig"] += 1
            continue

        rsi_val  = float(rsi_val)
        adx_val  = float(adx_val)
        bb_upper = float(bb_upper)
        bb_lower = float(bb_lower)
        bb_mid   = float(bb_mid)
        atr_val  = float(atr_val)

        # ADX 過濾
        if adx_val >= adx_max:
            dbg["no_sig"] += 1
            continue

        last_vol  = float(df_tf["volume"].iloc[i])
        vol_ok    = last_vol <= float(avg_vol_i) * Config.MR_VOL_MULT

        # ── 入場判斷（同正式：無 BB 容差，反轉 K 棒為硬性條件）──
        side = None
        if rsi_val <= Config.MR_RSI_OVERSOLD and price <= bb_lower:
            if engine._has_reversal(df_tf.iloc[max(0, i - 3):i + 1], "LONG"):
                side = "LONG"
        elif rsi_val >= Config.MR_RSI_OVERBOUGHT and price >= bb_upper:
            if engine._has_reversal(df_tf.iloc[max(0, i - 3):i + 1], "SHORT"):
                side = "SHORT"

        if side is None:
            dbg["no_sig"] += 1
            continue

        # ── 結構性過濾（v5：對齊 mean_reversion.py）─────────────
        # MR_STRUCTURAL_REQUIRED：0/1/2，至少幾道過濾通過才放行
        required = int(getattr(Config, "MR_STRUCTURAL_REQUIRED", 1))
        if required > 0:
            has_div = False
            has_sr  = False
            if getattr(Config, "MR_REQUIRE_DIVERGENCE", True):
                div_lookback = int(getattr(Config, "MR_DIV_LOOKBACK", 20))
                has_div = _bt_has_rsi_divergence(
                    df_tf, rsi_s, i, side, div_lookback
                )
            if getattr(Config, "MR_REQUIRE_SR_TEST", True):
                sr_lookback = int(getattr(Config, "MR_SR_LOOKBACK", 30))
                sr_tol = float(getattr(Config, "MR_SR_TOLERANCE", 0.015))
                has_sr = _bt_has_sr_test(df_tf, i, side, sr_lookback, sr_tol)
            confirmations = int(has_div) + int(has_sr)
            if confirmations < required:
                if not has_div:
                    dbg["no_div"] += 1
                if not has_sr:
                    dbg["no_sr"] += 1
                continue

        # ── 評分（同正式 _score_signal：base=1, RSI極端+1, BB超出+1, StochRSI+1, MACD背離+1）
        score = 1
        if (side == "LONG" and rsi_val <= 15) or (side == "SHORT" and rsi_val >= 85):
            score += 1
        bb_width = bb_upper - bb_lower
        if side == "LONG" and price < bb_lower - bb_width * 0.1:
            score += 1
        elif side == "SHORT" and price > bb_upper + bb_width * 0.1:
            score += 1
        try:
            if stoch_full is not None and not stoch_full.iloc[i].isna().any():
                k_s = float(stoch_full.iloc[i, 0])
                d_s = float(stoch_full.iloc[i, 1])
                if side == "LONG" and k_s > d_s and k_s < 20:
                    score += 1
                elif side == "SHORT" and k_s < d_s and k_s > 80:
                    score += 1
        except Exception:
            pass
        try:
            if macd_full is not None and macd_full.shape[1] >= 3:
                hist = macd_full.iloc[:, 2]
                if not pd.isna(hist.iloc[i]) and not pd.isna(hist.iloc[i - 1]):
                    if side == "LONG" and float(hist.iloc[i]) > float(hist.iloc[i - 1]) \
                            and price < float(df_tf["close"].iloc[i - 1]):
                        score += 1
                    elif side == "SHORT" and float(hist.iloc[i]) < float(hist.iloc[i - 1]) \
                            and price > float(df_tf["close"].iloc[i - 1]):
                        score += 1
        except Exception:
            pass
        score = min(score, 5)

        if score < min_score:
            dbg["low_score"] += 1
            continue

        # ── Regime gate（v5：對齊 live MarketContext）──────────
        if regime_series is not None:
            bar_time = df_tf["time"].iloc[i]
            regime = _regime_at(regime_series, bar_time)
            if not _regime_allows(regime, "mean_reversion"):
                dbg.setdefault("regime_block", 0)
                dbg["regime_block"] += 1
                continue

        # ── TP/SL ────────────────────────────────────────────────
        sl_dist = min(Config.MR_SL_PCT * price, atr_val * 1.0)
        sl_dist = max(sl_dist, price * 0.005)
        sl_dist = min(sl_dist, price * 0.03)

        # MR 核心：快進快出。TP1 用 ATR 距離（小目標高勝率）
        tp1_dist = min(atr_val * 1.2, abs(bb_mid - price)) if abs(bb_mid - price) > atr_val * 0.5 else atr_val * 1.0
        tp1_dist = max(tp1_dist, price * 0.005)  # 最少 0.5%

        if side == "LONG":
            tp1 = price + tp1_dist
            tp2 = bb_mid if bb_mid > tp1 else tp1 * 1.015
            sl  = price - sl_dist
        else:
            tp1 = price - tp1_dist
            tp2 = bb_mid if bb_mid < tp1 else tp1 * 0.985
            sl  = price + sl_dist

        dbg["signals"] += 1
        pos = calc_position(balance, price, sl, tp1, tp2,
                            min_rr=Config.MR_MIN_RR, tp1_split_pct=0.7)
        if not pos:
            dbg["bad_pos"] += 1
            continue

        trade = BtTrade(
            symbol    = symbol,
            direction = side,
            entry     = price,
            sl        = sl,
            tp1       = tp1,
            tp2       = tp2,
            qty       = pos["qty"],
            qty_tp1   = pos["qty_tp1"],
            qty_tp2   = pos["qty_tp2"],
            fib_level = "—",
            pattern   = "MR_REVERSAL",
            score     = score,
            timeframe = tf,
            open_bar  = i,
            open_time = df_tf["time"].iloc[i],
            strategy  = "mean_reversion",
        )

        df_future = df_tf.iloc[i + 1:].reset_index(drop=True)
        trade = simulate_trade(trade, df_future, max_bars=timeout_bars)

        if trade.result in ("", "OPEN"):
            continue

        balance += trade.net_pnl
        trades.append(trade)

        if "SL" in trade.result:
            cooldown_until = trade.close_bar + COOLDOWN_BARS

    print(f" 找到 {len(trades)} 筆訊號")
    if debug or len(trades) == 0:
        scanned = len(df_tf) - warmup - dbg["cooldown"]
        print(f"\n  ── MR 診斷（為何沒有入場？）──")
        print(f"  總掃描根數：{scanned}")
        print(f"  ├─ 沒通過訊號過濾：{dbg['no_sig']} 根  （ADX/RSI/BB）")
        print(f"  ├─ 評分不足 (<{min_score})：{dbg['low_score']} 根")
        print(f"  ├─ 倉位計算失敗   ：{dbg['bad_pos']} 根  （R:R < 1.2 或 SL% 範圍外）")
        print(f"  └─ 通過全部過濾   ：{dbg['signals']} 根")
    return trades


# ── SMC 反轉 K 棒嚴格判定（v2：對齊 smc_sweep.py）──────────────
def _bt_smc_bullish_reversal(df, i: int) -> bool:
    """
    Pin bar (lower wick ≥ 50% range) 或強實體陽線 (body ≥ 40% +
    close 在後 60%) 或 bullish engulfing (with body ≥ 40%)
    """
    o = float(df["open"].iloc[i])
    h = float(df["high"].iloc[i])
    l = float(df["low"].iloc[i])
    c = float(df["close"].iloc[i])
    rng = h - l
    if rng <= 0:
        return False
    body = abs(c - o)
    lower_wick = min(o, c) - l
    body_ratio  = body / rng
    lower_ratio = lower_wick / rng
    close_in_upper = (c - l) / rng

    if lower_ratio >= 0.5 and close_in_upper >= 0.6:
        return True
    if c > o and body_ratio >= 0.4 and close_in_upper >= 0.6:
        return True
    if i >= 1:
        po = float(df["open"].iloc[i - 1])
        pc = float(df["close"].iloc[i - 1])
        if pc < po and c > o and c >= po and o <= pc and body_ratio >= 0.4:
            return True
    return False


def _bt_smc_bearish_reversal(df, i: int) -> bool:
    o = float(df["open"].iloc[i])
    h = float(df["high"].iloc[i])
    l = float(df["low"].iloc[i])
    c = float(df["close"].iloc[i])
    rng = h - l
    if rng <= 0:
        return False
    body = abs(c - o)
    upper_wick = h - max(o, c)
    body_ratio  = body / rng
    upper_ratio = upper_wick / rng
    close_in_lower = (h - c) / rng

    if upper_ratio >= 0.5 and close_in_lower >= 0.6:
        return True
    if c < o and body_ratio >= 0.4 and close_in_lower >= 0.6:
        return True
    if i >= 1:
        po = float(df["open"].iloc[i - 1])
        pc = float(df["close"].iloc[i - 1])
        if pc > po and c < o and c <= po and o >= pc and body_ratio >= 0.4:
            return True
    return False


# ── SMC 主回測邏輯 ──────────────────────────────────────────────
def run_backtest_smc(client: Client, symbol: str, months: int,
                    debug: bool = False, *,
                    regime_series=None) -> list:
    """
    SMC Liquidity Sweep + Reversal 策略回測。
    SMC 不依賴 regime（與 NKF 一樣特權）；regime_series 參數保留為
    對齊其他策略的呼叫簽名，實際不擋。
    """
    # v4：個別幣排除（gap risk / 結構性失敗無法靠技術過濾解）
    excluded = getattr(Config, "SMC_EXCLUDED_SYMBOLS", "")
    if excluded:
        ex_set = {s.strip().upper() for s in excluded.split(",") if s.strip()}
        if symbol.upper() in ex_set:
            print(f"\n[{symbol} SMC] 在排除清單，跳過")
            return []

    tf = Config.SMC_TIMEFRAME
    print(f"\n[{symbol} SMC {tf}] 回測開始")

    df_tf = fetch_klines(client, symbol, tf, months)
    if len(df_tf) < 80:
        print(f"  資料不足（{len(df_tf)} 根），跳過")
        return []

    print("  預計算指標...", end="", flush=True)
    atr_full  = ta.atr(df_tf["high"], df_tf["low"], df_tf["close"], length=14)
    avg_vol_s = df_tf["volume"].rolling(21).mean().shift(1)

    # 預先標記所有 fractal swing low/high（向量化前處理）
    n = len(df_tf)
    left  = int(Config.SMC_SWING_LEFT)
    right = int(Config.SMC_SWING_RIGHT)
    is_swing_low  = np.zeros(n, dtype=bool)
    is_swing_high = np.zeros(n, dtype=bool)
    lows_arr  = df_tf["low"].values
    highs_arr = df_tf["high"].values
    for k in range(left, n - right):
        if lows_arr[k] == lows_arr[k - left:k + right + 1].min():
            is_swing_low[k] = True
        if highs_arr[k] == highs_arr[k - left:k + right + 1].max():
            is_swing_high[k] = True

    # MACD（給 score 用）
    macd_full = ta.macd(df_tf["close"])
    print(" 完成")

    warmup = max(60, int(Config.SMC_SWING_LOOKBACK) + right + 5)
    trades = []
    cooldown_until = -1
    timeout_bars = int(Config.SMC_TIMEOUT_BARS)
    min_score    = int(Config.SMC_MIN_SCORE)
    sweep_min    = float(Config.SMC_SWEEP_MIN_PCT)
    sweep_max    = float(Config.SMC_SWEEP_MAX_PCT)
    vol_mult     = float(Config.SMC_VOL_MULT)
    sl_buffer_pct = float(Config.SMC_SL_BUFFER)
    min_rr       = float(Config.SMC_MIN_RR)

    dbg = {"cooldown": 0, "no_swing": 0, "no_sweep": 0,
           "no_reversal": 0, "no_volume": 0, "low_score": 0,
           "bad_pos": 0, "htf_block": 0, "auto_excluded": 0,
           "signals": 0}

    # ── HTF (4h EMA50) 趨勢過濾預計算 ───────────────────────────
    htf_enabled = bool(getattr(Config, "SMC_HTF_FILTER_ENABLED", True))
    htf_close_arr = None
    htf_ema_arr   = None
    htf_slope_arr = None  # v5：EMA50 斜率系列
    if htf_enabled:
        try:
            htf_tf      = getattr(Config, "SMC_HTF_TIMEFRAME", "4h")
            htf_period  = int(getattr(Config, "SMC_HTF_EMA_PERIOD", 50))
            print(f"  下載 HTF {htf_tf} 計算 EMA{htf_period}...", end="", flush=True)
            slope_bars = int(getattr(Config, "SMC_HTF_SLOPE_BARS", 5))
            df_htf = fetch_klines(client, symbol, htf_tf, months + 2)
            if len(df_htf) >= htf_period + slope_bars + 5:
                htf_ema = ta.ema(df_htf["close"], length=htf_period)
                # v5：算 EMA 斜率（pct）— 過去 slope_bars 根 EMA 的相對變動
                htf_ema_past = htf_ema.shift(slope_bars)
                htf_slope = (htf_ema - htf_ema_past) / htf_ema_past
                # 重要：fetch_klines 的 close_time 仍是 int64 ms，必須
                # 轉為 datetime 才能跟 df_tf["time"]（datetime）merge_asof，
                # 否則 silent fail 導致 HTF 過濾不生效（v3 bug）
                df_htf_aligned = pd.DataFrame({
                    "close_time": pd.to_datetime(
                        df_htf["close_time"], unit="ms"
                    ),
                    "htf_close":  df_htf["close"].values,
                    "htf_ema":    htf_ema.values,
                    "htf_slope":  htf_slope.values,
                }).dropna()
                df_1h_idx = df_tf[["time"]].copy()
                df_1h_idx["_orig_idx"] = range(len(df_1h_idx))
                merged = pd.merge_asof(
                    df_1h_idx.sort_values("time"),
                    df_htf_aligned.sort_values("close_time"),
                    left_on="time",
                    right_on="close_time",
                    direction="backward",
                )
                merged = merged.sort_values("_orig_idx").reset_index(drop=True)
                htf_close_arr = merged["htf_close"].values
                htf_ema_arr   = merged["htf_ema"].values
                htf_slope_arr = merged["htf_slope"].values
                # 驗證至少有效資料 ≥ warmup 後總根數的 50%
                valid_pct = (
                    int((~merged["htf_close"].isna()).sum())
                    / max(len(merged), 1) * 100
                )
                print(f" 完成（{valid_pct:.0f}% 有效）")
            else:
                print(" 資料不足，HTF 過濾停用")
        except Exception as e:
            print(f" 失敗（{e}），HTF 過濾停用")
            htf_close_arr = None
            htf_ema_arr   = None

    print(f"  掃描 {len(df_tf) - warmup} 根 K 棒...", end="", flush=True)

    for i in range(warmup, len(df_tf) - 1):
        if i <= cooldown_until:
            dbg["cooldown"] += 1
            continue

        # v2 修正：sweep 在 i-1（前一根）、confirmation 在 i（當根）
        # → 等下一根確認方向才入場，避免接刀
        sweep_idx = i - 1
        if sweep_idx < right + 5:
            continue

        sw_high_p = float(df_tf["high"].iloc[sweep_idx])
        sw_low_p  = float(df_tf["low"].iloc[sweep_idx])
        sw_close  = float(df_tf["close"].iloc[sweep_idx])
        sw_vol    = float(df_tf["volume"].iloc[sweep_idx])
        cur_close = float(df_tf["close"].iloc[i])
        cur_high  = float(df_tf["high"].iloc[i])
        cur_low   = float(df_tf["low"].iloc[i])
        cur_vol   = float(df_tf["volume"].iloc[i])

        atr_val   = atr_full.iloc[i] if atr_full is not None else float("nan")
        avg_vol   = avg_vol_s.iloc[sweep_idx] if avg_vol_s is not None else float("nan")

        if pd.isna(atr_val) or pd.isna(avg_vol) or float(avg_vol) <= 0:
            continue
        atr_val = float(atr_val)
        avg_vol = float(avg_vol)

        # 找 swing：必須在 sweep_idx - right 之前
        end_search   = sweep_idx - right
        start_search = max(0, end_search - int(Config.SMC_SWING_LOOKBACK))
        last_sw_low  = None
        last_sw_high = None
        for k in range(end_search, start_search - 1, -1):
            if last_sw_low is None and is_swing_low[k]:
                last_sw_low = float(lows_arr[k])
            if last_sw_high is None and is_swing_high[k]:
                last_sw_high = float(highs_arr[k])
            if last_sw_low is not None and last_sw_high is not None:
                break

        if last_sw_low is None and last_sw_high is None:
            dbg["no_swing"] += 1
            continue

        side = None
        sweep_level = 0.0

        # LONG：sweep@i-1 + reversal@i-1 + confirmation@i
        if last_sw_low is not None:
            sweep_pct = (last_sw_low - sw_low_p) / last_sw_low if last_sw_low > 0 else 0
            if sweep_min <= sweep_pct <= sweep_max and sw_close > last_sw_low:
                if _bt_smc_bullish_reversal(df_tf, sweep_idx):
                    if sw_vol >= avg_vol * vol_mult:
                        if cur_close > sw_close:        # confirmation：續漲
                            side = "LONG"
                            sweep_level = last_sw_low
                    else:
                        dbg["no_volume"] += 1
                else:
                    dbg["no_reversal"] += 1
            elif sw_low_p < last_sw_low * (1 - sweep_max):
                dbg["no_sweep"] += 1

        # SHORT：sweep@i-1 + reversal@i-1 + confirmation@i
        if side is None and last_sw_high is not None:
            sweep_pct = (sw_high_p - last_sw_high) / last_sw_high if last_sw_high > 0 else 0
            if sweep_min <= sweep_pct <= sweep_max and sw_close < last_sw_high:
                if _bt_smc_bearish_reversal(df_tf, sweep_idx):
                    if sw_vol >= avg_vol * vol_mult:
                        if cur_close < sw_close:        # confirmation：續跌
                            side = "SHORT"
                            sweep_level = last_sw_high
                    else:
                        dbg["no_volume"] += 1
                else:
                    dbg["no_reversal"] += 1

        if side is None:
            continue

        # ── Per-coin 自動學習（v7）──────────────────────────────
        # 看本次 backtest 已完成的 trades（同 symbol）的近 N 單 win rate，
        # 低於門檻就跳過。模擬 live DB 自動學習行為。
        if getattr(Config, "SMC_AUTO_EXCLUDE_ENABLED", True):
            min_n = int(getattr(Config, "SMC_AUTO_EXCLUDE_MIN_TRADES", 10))
            thr   = float(getattr(Config, "SMC_AUTO_EXCLUDE_WIN_THRESHOLD", 0.35))
            lb    = int(getattr(Config, "SMC_AUTO_EXCLUDE_LOOKBACK", 30))
            completed = [t for t in trades if t.result not in ("", "OPEN")]
            if len(completed) >= min_n:
                recent = completed[-lb:]
                wins = sum(1 for t in recent if t.net_pnl > 0)
                wr = wins / len(recent) if recent else 1.0
                if wr < thr:
                    dbg["auto_excluded"] += 1
                    continue

        # ── HTF（4h EMA50）趨勢過濾（v3+v4+v5）─────────────────
        if htf_close_arr is not None and htf_ema_arr is not None:
            htf_c = htf_close_arr[i] if i < len(htf_close_arr) else None
            htf_e = htf_ema_arr[i]   if i < len(htf_ema_arr)   else None
            if htf_c is None or htf_e is None or pd.isna(htf_c) or pd.isna(htf_e):
                # 無 HTF 資料 → fail-open（不擋）
                pass
            else:
                htf_c = float(htf_c); htf_e = float(htf_e)
                min_dist = float(
                    getattr(Config, "SMC_HTF_MIN_DISTANCE_PCT", 0.005)
                )
                upper_thr = htf_e * (1 + min_dist)
                lower_thr = htf_e * (1 - min_dist)
                if side == "LONG" and htf_c < upper_thr:
                    dbg["htf_block"] += 1
                    continue
                if side == "SHORT" and htf_c > lower_thr:
                    dbg["htf_block"] += 1
                    continue

                # v5 方向 + v6 強度：EMA50 斜率必須有足夠幅度
                if (htf_slope_arr is not None
                        and getattr(Config, "SMC_HTF_REQUIRE_SLOPE", True)):
                    htf_s = htf_slope_arr[i] if i < len(htf_slope_arr) else None
                    if htf_s is not None and not pd.isna(htf_s):
                        slope = float(htf_s)
                        min_slope = float(
                            getattr(Config, "SMC_HTF_MIN_SLOPE_PCT", 0.005)
                        )
                        if side == "LONG" and slope < min_slope:
                            dbg["htf_block"] += 1
                            continue
                        if side == "SHORT" and slope > -min_slope:
                            dbg["htf_block"] += 1
                            continue

        # ── Score（對齊 live SMC._score_signal）─────────────────
        # v2：用 sweep candle 的量（是訊號當下意義的量）
        score = 1
        ratio = sw_vol / avg_vol if avg_vol > 0 else 1.0
        if ratio >= 2.5:
            score += 2
        elif ratio >= 1.5:
            score += 1
        if sweep_level > 0:
            recovery_pct = abs(cur_close - sweep_level) / sweep_level * 100
            if recovery_pct >= 0.3:
                score += 1
        # MACD 動能對齊
        try:
            if macd_full is not None and macd_full.shape[1] >= 3:
                hist = macd_full.iloc[:, 2]
                if not pd.isna(hist.iloc[i]) and not pd.isna(hist.iloc[i - 1]):
                    if side == "LONG" and float(hist.iloc[i]) > float(hist.iloc[i - 1]):
                        score += 1
                    elif side == "SHORT" and float(hist.iloc[i]) < float(hist.iloc[i - 1]):
                        score += 1
        except Exception:
            pass
        score = min(score, 5)

        if score < min_score:
            dbg["low_score"] += 1
            continue

        # ── SL / TP ────────────────────────────────────────────
        atr_buf = atr_val * sl_buffer_pct
        if side == "LONG":
            sl = sweep_level - atr_buf - cur_close * 0.001
            risk = cur_close - sl
            if risk <= 0:
                sl = cur_close - atr_val * 1.5
                risk = cur_close - sl
            tp1 = cur_close + risk * 1.0
            tp2 = cur_close + risk * 2.0
        else:
            sl = sweep_level + atr_buf + cur_close * 0.001
            risk = sl - cur_close
            if risk <= 0:
                sl = cur_close + atr_val * 1.5
                risk = sl - cur_close
            tp1 = cur_close - risk * 1.0
            tp2 = cur_close - risk * 2.0

        # R:R 檢查
        rr = abs(tp2 - cur_close) / risk if risk > 0 else 0
        if rr < min_rr:
            dbg["bad_pos"] += 1
            continue

        # 分倉 50/50
        qty_total = MARGIN_USDT * LEVERAGE / cur_close
        qty_tp1 = round(qty_total * 0.5, 4)
        qty_tp2 = round(qty_total - qty_tp1, 4)

        trade = BtTrade(
            symbol    = symbol,
            direction = side,
            entry     = cur_close,
            sl        = sl,
            tp1       = tp1,
            tp2       = tp2,
            qty       = qty_total,
            qty_tp1   = qty_tp1,
            qty_tp2   = qty_tp2,
            fib_level = "",
            pattern   = "SMC_SWEEP",
            score     = score,
            timeframe = tf,
            open_bar  = i,
            open_time = df_tf["time"].iloc[i],
            strategy  = "smc_sweep",
        )

        df_future = df_tf.iloc[i + 1:].reset_index(drop=True)
        trade = simulate_trade(trade, df_future, max_bars=timeout_bars)

        if trade.result in ("", "OPEN"):
            continue

        trades.append(trade)
        dbg["signals"] += 1

        if "SL" in trade.result:
            cooldown_until = trade.close_bar + COOLDOWN_BARS

    print(f" 找到 {len(trades)} 筆訊號")
    if debug or len(trades) == 0:
        scanned = len(df_tf) - warmup - dbg["cooldown"]
        print(f"\n  ── SMC 診斷（為何沒有入場？）──")
        print(f"  總掃描根數：{scanned}")
        print(f"  ├─ 找不到 swing       ：{dbg['no_swing']} 根")
        print(f"  ├─ 刺破過深非 sweep    ：{dbg['no_sweep']} 根")
        print(f"  ├─ 反轉 K 棒不成立    ：{dbg['no_reversal']} 根")
        print(f"  ├─ 量能不足           ：{dbg['no_volume']} 根")
        print(f"  ├─ 評分不足 (<{min_score})：{dbg['low_score']} 根")
        print(f"  ├─ R:R 過低 / SL 異常  ：{dbg['bad_pos']} 根")
        print(f"  ├─ HTF 趨勢過濾擋下    ：{dbg['htf_block']} 根")
        print(f"  ├─ Auto-exclude（學習）：{dbg['auto_excluded']} 根")
        print(f"  └─ 通過全部過濾       ：{dbg['signals']} 根")
    return trades


# ── 輸出統計 ──────────────────────────────────────────────────────
def print_stats(trades: list, timeframe: str, symbol: str,
                initial_balance: float, label: str = ""):
    closed = [t for t in trades if t.result not in ("", "OPEN")]
    tag = f" [{label}]" if label else ""
    if not closed:
        print(f"\n[{symbol} {timeframe}{tag}] 無已結算交易")
        return

    wins   = [t for t in closed if t.net_pnl > 0]
    losses = [t for t in closed if t.net_pnl <= 0]
    tp2_hits = [t for t in closed if t.result == "TP1+TP2"]
    sl_hits  = [t for t in closed if t.result in ("SL", "TP1+SL")]
    timeout  = [t for t in closed if t.result == "TIMEOUT"]

    total_net = sum(t.net_pnl for t in closed)
    total_fee = sum(t.fee for t in closed)
    win_rate  = len(wins) / len(closed) * 100
    avg_win   = (sum(t.net_pnl for t in wins) / len(wins)) if wins else 0
    avg_loss  = (sum(t.net_pnl for t in losses) / len(losses)) if losses else 0
    expectancy = win_rate / 100 * avg_win + (1 - win_rate / 100) * avg_loss

    # 最大連虧
    max_dd_streak = 0
    streak = 0
    for t in closed:
        if t.net_pnl <= 0:
            streak += 1
            max_dd_streak = max(max_dd_streak, streak)
        else:
            streak = 0

    # 最大回撤（以模擬餘額計算）
    balance = initial_balance
    peak = balance
    max_drawdown = 0.0
    for t in closed:
        balance += t.net_pnl
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100
        max_drawdown = max(max_drawdown, dd)

    final_balance = initial_balance + total_net

    print(f"\n{'=' * 60}")
    print(f"  回測結果：{symbol} {timeframe}{tag}")
    print(f"{'=' * 60}")

    # ── 退出方式細分 ─────────────────────────────────────────────
    timeout_wins   = [t for t in timeout if t.net_pnl > 0]
    timeout_losses = [t for t in timeout if t.net_pnl <= 0]
    tp1_only       = [t for t in closed if t.result == "TP1+SL"]
    tp1_pct = len(tp1_only) / len(closed) * 100 if closed else 0
    timeout_pct = len(timeout) / len(closed) * 100 if closed else 0
    timeout_net = sum(t.net_pnl for t in timeout)

    print(f"  交易總數：{len(closed)}  "
          f"（TP2全達：{len(tp2_hits)}  TP1出場：{len(tp1_only)}  止損：{len(sl_hits)}  超時：{len(timeout)}）")
    print(f"  勝率：    {win_rate:.1f}%  ({len(wins)} 勝 / {len(losses)} 敗)")
    print(f"  退出方式：TP2={len(tp2_hits)/len(closed)*100:.0f}%  "
          f"TP1SL={tp1_pct:.0f}%  "
          f"止損={len(sl_hits)/len(closed)*100:.0f}%  "
          f"超時={timeout_pct:.0f}%  "
          f"({'⚠ 超時佔比過高，建議調大 --max-bars 或確認用正式API' if timeout_pct >= 40 else 'OK'})")
    if timeout:
        to_avg = timeout_net / len(timeout)
        print(f"  超時明細：{len(timeout_wins)} 超時盈利 / {len(timeout_losses)} 超時虧損 "
              f"  超時平均：{to_avg:+.4f} USDT/單  超時合計：{timeout_net:+.4f} USDT")
    print(f"  期望值：  {expectancy:+.4f} USDT/單")
    print(f"  平均獲利：{avg_win:+.4f} USDT")
    print(f"  平均虧損：{avg_loss:+.4f} USDT")
    print(f"  總淨損益：{total_net:+.4f} USDT  （手續費：{total_fee:.4f} USDT）")
    print(f"  起始資金：{initial_balance:.2f} → 最終：{final_balance:.2f} USDT "
          f"（{(final_balance/initial_balance - 1)*100:+.1f}%）")
    print(f"  最大連虧：{max_dd_streak} 單")
    print(f"  最大回撤：{max_drawdown:.1f}%")

    # 月度摘要
    print(f"\n  月度損益：")
    monthly: dict[str, float] = {}
    for t in closed:
        if not t.close_time:
            continue
        key = t.close_time.strftime("%Y-%m")
        monthly[key] = monthly.get(key, 0) + t.net_pnl
    for month, pnl in sorted(monthly.items()):
        bar = "█" * int(abs(pnl) / max(abs(v) for v in monthly.values()) * 20)
        sign = "+" if pnl >= 0 else ""
        print(f"    {month}：{sign}{pnl:>8.4f} USDT  {bar}")

    # 最近 10 筆交易
    print(f"\n  最近 10 筆交易：")
    print(f"  {'時間':<18} {'方向':<6} {'結果':<10} {'Fib':<7} {'形態':<22} {'淨損益':>10}")
    print(f"  {'-'*78}")
    for t in closed[-10:]:
        ts = t.close_time.strftime("%m/%d %H:%M") if t.close_time else "—"
        mark = "✓" if t.net_pnl > 0 else "✗"
        print(f"  {ts:<18} {t.direction:<6} {mark}{t.result:<9} "
              f"{t.fib_level:<7} {t.pattern:<22} {t.net_pnl:>+10.4f}")

    print(f"{'=' * 60}")


# ── 主回測邏輯 ───────────────────────────────────────────────────
def run_backtest(client: Client, symbol: str, timeframe: str,
                 months: int, max_bars: int = 48) -> list:

    print(f"\n[{symbol} {timeframe}] 回測開始")

    # 下載相同時間區間的 daily（給 swing 判斷用）
    df_tf    = fetch_klines(client, symbol, timeframe, months)
    df_daily = fetch_klines(client, symbol, "1d", months + 1)

    if len(df_tf) < 100:
        print(f"  資料不足（{len(df_tf)} 根），跳過")
        return []

    # 預留前 60 根作為 warm-up（swing 判斷需要歷史資料）
    warmup = 60
    trades: list[BtTrade] = []
    balance = INITIAL_BALANCE
    in_trade = False
    cooldown_until = -1  # 冷卻期結束的 bar index

    # 建立回測引擎（使用可調參數）
    engine = BacktestSignalEngine(
        fib_tol       = Config.NKF_FIB_TOL,
        vol_mult      = Config.NKF_VOL_RATIO,
        skip_vol_rise = not Config.NKF_VOL_RISING,
        skip_bad_fib  = False,   # 同正式：不跳過任何 Fib 位
    )

    print(f"  掃描 {len(df_tf) - warmup} 根 K 棒...", end="", flush=True)

    for i in range(warmup, len(df_tf) - 1):
        # 冷卻期
        if i <= cooldown_until:
            continue

        # 當根已收盤 → 用截至當根的資料檢查訊號
        df_slice  = df_tf.iloc[:i + 1].copy().reset_index(drop=True)

        # daily 同步截到當根時間
        bar_time = df_tf["time"].iloc[i]
        df_d_slice = df_daily[df_daily["time"] <= bar_time].copy().reset_index(drop=True)
        if len(df_d_slice) < 20:
            continue

        try:
            sig = engine.check_on_bar(df_slice, df_d_slice, len(df_slice) - 1, timeframe)
        except Exception:
            continue

        if not sig or sig.score < MIN_SCORE:
            continue

        # 計算倉位
        pos = calc_position(balance, sig.entry, sig.sl, sig.tp1, sig.tp2)
        if not pos:
            continue

        # 建立模擬交易
        trade = BtTrade(
            symbol    = symbol,
            direction = sig.direction,
            entry     = sig.entry,
            sl        = sig.sl,
            tp1       = sig.tp1,
            tp2       = sig.tp2,
            qty       = pos["qty"],
            qty_tp1   = pos["qty_tp1"],
            qty_tp2   = pos["qty_tp2"],
            fib_level = sig.fib_level,
            pattern   = sig.pattern,
            score     = sig.score,
            timeframe = timeframe,
            open_bar  = i,
            open_time = bar_time,
        )

        # 用後續 K 棒模擬結果
        df_future = df_tf.iloc[i + 1:].reset_index(drop=True)
        trade = simulate_trade(trade, df_future, max_bars=max_bars)

        if trade.result in ("", "OPEN"):
            continue

        balance += trade.net_pnl
        trades.append(trade)

        # 止損後設冷卻期
        if "SL" in trade.result:
            cooldown_until = trade.close_bar + COOLDOWN_BARS

    print(f" 找到 {len(trades)} 筆訊號")
    return trades


# ── MR 指標快照（診斷用）─────────────────────────────────────────
def _print_mr_indicator_snapshot(client: Client, symbol: str, months: int, adx_max: float = 25.0):
    """
    下載最近一段資料，印出最後 20 根 K 棒的實際 RSI/ADX 值，
    幫助確認入場條件設定是否合理。
    """
    tf = Config.MR_TIMEFRAME
    df = fetch_klines(client, symbol, tf, 1)  # 只需要最近 1 個月夠計算指標
    if len(df) < 60:
        print("  [診斷] 資料不足，無法計算指標")
        return

    rsi   = ta.rsi(df["close"], length=Config.MR_RSI_PERIOD)
    bb    = ta.bbands(df["close"], length=Config.MR_BB_PERIOD, std=Config.MR_BB_STD)
    adx_df2 = ta.adx(df["high"], df["low"], df["close"], length=14)
    # 自動偵測欄位名稱
    col_u = next((c for c in bb.columns if c.startswith("BBU_")), None) if bb is not None else None
    col_l = next((c for c in bb.columns if c.startswith("BBL_")), None) if bb is not None else None
    avg_vol = df["volume"].rolling(20).mean()

    print(f"\n  ── {symbol} {tf} 最後 20 根指標快照 ──")
    print(f"  {'時間':<18} {'RSI':>6} {'ADX':>6} {'BB%':>7} {'Vol%':>7} {'Price':>12}  入場方向")
    print(f"  {'-'*72}")
    for idx in df.index[-20:]:
        t   = df.loc[idx, "time"].strftime("%m/%d %H:%M") if hasattr(df.loc[idx, "time"], "strftime") else str(df.loc[idx, "time"])[:16]
        r   = float(rsi.iloc[idx]) if rsi is not None and not pd.isna(rsi.iloc[idx]) else 0
        a   = float(adx_df2["ADX_14"].iloc[idx]) if adx_df2 is not None and not pd.isna(adx_df2["ADX_14"].iloc[idx]) else 0
        pr  = float(df.loc[idx, "close"])
        bbu = float(bb[col_u].iloc[idx]) if bb is not None and col_u and col_u in bb.columns else 0
        bbl = float(bb[col_l].iloc[idx]) if bb is not None and col_l and col_l in bb.columns else 0
        bbm = (bbu + bbl) / 2 if bbu else 0
        bw  = (bbu - bbl) / bbm * 100 if bbm else 0
        av  = float(avg_vol.iloc[idx]) if not pd.isna(avg_vol.iloc[idx]) else 0
        vr  = float(df.loc[idx, "volume"]) / av * 100 if av > 0 else 0

        direction = ""
        if r <= Config.MR_RSI_OVERSOLD and pr <= bbl and a < adx_max:
            direction = "▲LONG 候選"
        elif r >= Config.MR_RSI_OVERBOUGHT and pr >= bbu and a < adx_max:
            direction = "▼SHORT 候選"
        elif a >= adx_max:
            direction = "ADX太高"
        elif r > Config.MR_RSI_OVERSOLD and r < Config.MR_RSI_OVERBOUGHT:
            direction = f"RSI={r:.0f} 中性"

        print(f"  {t:<18} {r:>6.1f} {a:>6.1f} {bw:>6.1f}% {vr:>6.0f}%  {pr:>12.2f}  {direction}")

    # 分布統計
    valid_rsi = rsi.dropna()
    valid_adx = adx_df2["ADX_14"].dropna()
    print(f"\n  ── 統計分布（全部歷史資料）──")
    print(f"  RSI：min={valid_rsi.min():.1f}  max={valid_rsi.max():.1f}  "
          f"中位數={valid_rsi.median():.1f}")
    print(f"  RSI ≤ {Config.MR_RSI_OVERSOLD:.0f} 出現次數：{(valid_rsi <= Config.MR_RSI_OVERSOLD).sum()} 根  "
          f"（{(valid_rsi <= Config.MR_RSI_OVERSOLD).mean()*100:.1f}%）")
    print(f"  RSI ≥ {Config.MR_RSI_OVERBOUGHT:.0f} 出現次數：{(valid_rsi >= Config.MR_RSI_OVERBOUGHT).sum()} 根  "
          f"（{(valid_rsi >= Config.MR_RSI_OVERBOUGHT).mean()*100:.1f}%）")
    print(f"  ADX：min={valid_adx.min():.1f}  max={valid_adx.max():.1f}  "
          f"中位數={valid_adx.median():.1f}")
    print(f"  ADX < {adx_max:.0f} 出現次數：{(valid_adx < adx_max).sum()} 根  "
          f"（{(valid_adx < adx_max).mean()*100:.1f}%）")
    ovs_and_low_adx = ((valid_rsi <= Config.MR_RSI_OVERSOLD) & (valid_adx < adx_max)).sum()
    ovb_and_low_adx = ((valid_rsi >= Config.MR_RSI_OVERBOUGHT) & (valid_adx < adx_max)).sum()
    print(f"\n  RSI≤{Config.MR_RSI_OVERSOLD:.0f} + ADX<{adx_max:.0f} 同時成立：{ovs_and_low_adx} 根  "
          f"（這些才是 MR 候選入場機會）")
    print(f"  RSI≥{Config.MR_RSI_OVERBOUGHT:.0f} + ADX<{adx_max:.0f} 同時成立：{ovb_and_low_adx} 根\n")


# ── MR 多幣掃描 ──────────────────────────────────────────────────
_MR_SCAN_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT",
    "XRPUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "LTCUSDT", "UNIUSDT", "ATOMUSDT", "NEARUSDT", "MATICUSDT",
    "TRXUSDT", "SHIBUSDT", "XLMUSDT",  "OPUSDT",  "ARBUSDT",
]


_STABLE_QUOTE_PREFIXES = ("USDC", "FDUSD", "TUSD", "BUSD", "DAI")


def _resolve_symbol_list(args, client: Client) -> list[str]:
    """解析多幣回測的目標 symbol 清單。

    優先順序：--symbols（手動）> --top-n（自動）> 單幣 --symbol。
    """
    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        return syms

    if args.top_n and args.top_n > 0:
        try:
            tickers = client.futures_ticker()
        except Exception as e:
            print(f"  ⚠ 取得 futures_ticker 失敗：{e}，fallback 單幣模式")
            return [args.symbol]
        # 24h quoteVolume 由高到低
        ranked = sorted(
            tickers,
            key=lambda t: float(t.get("quoteVolume", 0) or 0),
            reverse=True,
        )
        out: list[str] = []
        for t in ranked:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            if sym.endswith("_PERP"):
                continue
            # 排除穩定幣對
            if args.exclude_stable:
                base = sym[:-4]
                if any(base.startswith(p) for p in _STABLE_QUOTE_PREFIXES):
                    continue
            out.append(sym)
            if len(out) >= args.top_n:
                break
        if not out:
            return [args.symbol]
        return out

    return [args.symbol]


def _run_multi_coin_backtest(
    client: Client,
    symbols: list[str],
    args,
    run_flags: dict,
) -> dict:
    """對每幣 × 每策略跑回測，回傳 {(sym, strat): [BtTrade]}。"""
    results: dict[tuple[str, str], list] = {}
    timeframes = args.tf or DEFAULT_TF

    # 預先建構 BTC regime 時間序列（除非 --no-regime 關閉）
    # 這對齊 live MarketContext.regime_allows 的過濾，避免 backtest 結果
    # 對 ML/BD/MR 過於悲觀（live 已經在 CHOPPY 期間擋掉這些策略）
    regime_series = None
    if getattr(args, "use_regime", True) and any(
        run_flags.get(k) for k in ("ml", "bd", "mr")
    ):
        regime_series = _build_btc_regime_series(client, args.months)

    for i, sym in enumerate(symbols, 1):
        print(f"\n[{i}/{len(symbols)}] {sym}")

        if run_flags.get("nkf"):
            tf_trades = []
            for tf in timeframes:
                try:
                    tr = run_backtest(client, sym, tf, args.months,
                                      max_bars=args.max_bars)
                    tf_trades.extend(tr)
                except Exception as e:
                    print(f"  [NKF/{tf}] 失敗：{e}")
            results[(sym, "NKF")] = tf_trades

        if run_flags.get("mr"):
            try:
                tr = run_backtest_mr(client, sym, args.months,
                                     debug=False, adx_max=args.adx_max,
                                     regime_series=regime_series)
            except Exception as e:
                print(f"  [MR] 失敗：{e}")
                tr = []
            results[(sym, "MR")] = tr

        if run_flags.get("bd"):
            try:
                tr = run_backtest_bd(client, sym, args.months, debug=False,
                                     regime_series=regime_series)
            except Exception as e:
                print(f"  [BD] 失敗：{e}")
                tr = []
            results[(sym, "BD")] = tr

        if run_flags.get("ml"):
            try:
                tr = run_backtest_ml(client, sym, args.months, debug=False,
                                     regime_series=regime_series)
            except Exception as e:
                print(f"  [ML] 失敗：{e}")
                tr = []
            results[(sym, "ML")] = tr

        if run_flags.get("smc"):
            try:
                tr = run_backtest_smc(client, sym, args.months, debug=False,
                                      regime_series=regime_series)
            except Exception as e:
                print(f"  [SMC] 失敗：{e}")
                tr = []
            results[(sym, "SMC")] = tr

    return results


def _trade_stats(trades: list, balance: float) -> dict:
    """單一交易組（同 symbol+strategy）的統計，回傳 dict。"""
    closed = [t for t in trades if t.result not in ("", "OPEN")]
    if not closed:
        return {
            "trades": 0, "wins": 0, "win_rate": 0.0,
            "pnl": 0.0, "pct": 0.0, "avg_pnl": 0.0,
            "max_dd": 0.0, "best": 0.0, "worst": 0.0,
        }
    wins = [t for t in closed if t.net_pnl > 0]
    pnl_sum = sum(t.net_pnl for t in closed)
    bal = balance
    peak = bal
    mdd = 0.0
    for t in closed:
        bal += t.net_pnl
        peak = max(peak, bal)
        if peak > 0:
            mdd = max(mdd, (peak - bal) / peak * 100)
    # 平倉原因分布（診斷用：確認 TP/SL/TIMEOUT 各占比）
    reason_counts: dict[str, int] = {}
    reason_pnl: dict[str, float] = {}
    for t in closed:
        rsn = t.result or "UNK"
        reason_counts[rsn] = reason_counts.get(rsn, 0) + 1
        reason_pnl[rsn] = reason_pnl.get(rsn, 0.0) + t.net_pnl

    return {
        "trades":   len(closed),
        "wins":     len(wins),
        "win_rate": len(wins) / len(closed) * 100,
        "pnl":      pnl_sum,
        "pct":      pnl_sum / balance * 100 if balance else 0.0,
        "avg_pnl":  pnl_sum / len(closed),
        "max_dd":   mdd,
        "best":     max(t.net_pnl for t in closed),
        "worst":    min(t.net_pnl for t in closed),
        "reason_counts": reason_counts,
        "reason_pnl":    reason_pnl,
    }


def _print_multi_summary(results: dict, balance: float) -> None:
    """三層輸出：cell × strategy / per-strategy / per-coin / 建議。"""
    if not results:
        print("\n⚠ 無回測結果")
        return

    symbols = sorted({s for s, _ in results.keys()})
    strategies = sorted({st for _, st in results.keys()})

    # ── 1. Cell 細表（symbol × strategy）──────────────────────
    print(f"\n{'='*78}")
    print(f"  細項表：每幣 × 每策略")
    print(f"{'='*78}")
    print(f"  {'Coin':<14} {'Strat':<6} {'Trades':>7} {'Win%':>7} "
          f"{'PnL(U)':>10} {'AvgPnL':>9} {'MDD%':>7} {'Best':>8} {'Worst':>8}")
    print(f"  {'-'*76}")
    for sym in symbols:
        for strat in strategies:
            trades = results.get((sym, strat), [])
            if not trades:
                continue
            s = _trade_stats(trades, balance)
            if s["trades"] == 0:
                continue
            mark = ""
            if s["trades"] >= 5:
                if s["win_rate"] >= 55 and s["pnl"] > 0:
                    mark = " ★"
                elif s["win_rate"] < 35 and s["pnl"] < 0:
                    mark = " ✗"
            print(f"  {sym:<14} {strat:<6} {s['trades']:>7} "
                  f"{s['win_rate']:>6.1f}% {s['pnl']:>+10.2f} "
                  f"{s['avg_pnl']:>+9.3f} {s['max_dd']:>6.1f}% "
                  f"{s['best']:>+8.2f} {s['worst']:>+8.2f}{mark}")

    # ── 2. Per-strategy aggregate ──────────────────────────
    print(f"\n{'='*78}")
    print(f"  策略總計（跨所有幣）")
    print(f"{'='*78}")
    print(f"  {'Strategy':<10} {'Coins':>6} {'Trades':>8} {'Win%':>7} "
          f"{'TotalPnL':>11} {'AvgPnL/T':>10} {'MaxDD%':>8}")
    print(f"  {'-'*76}")
    for strat in strategies:
        all_trades = []
        coin_count = 0
        for sym in symbols:
            trs = results.get((sym, strat), [])
            cl = [t for t in trs if t.result not in ("", "OPEN")]
            if cl:
                coin_count += 1
            all_trades.extend(trs)
        s = _trade_stats(all_trades, balance)
        print(f"  {strat:<10} {coin_count:>6} {s['trades']:>8} "
              f"{s['win_rate']:>6.1f}% {s['pnl']:>+11.2f} "
              f"{s['avg_pnl']:>+10.3f} {s['max_dd']:>7.1f}%")

    # ── 3. Per-coin aggregate ──────────────────────────────
    print(f"\n{'='*78}")
    print(f"  幣種總計（跨所有策略）—— 依 PnL 由高到低")
    print(f"{'='*78}")
    print(f"  {'Coin':<14} {'Strats':>7} {'Trades':>8} {'Win%':>7} "
          f"{'TotalPnL':>11} {'AvgPnL/T':>10}")
    print(f"  {'-'*72}")
    coin_summary = []
    for sym in symbols:
        all_trades = []
        strat_count = 0
        for strat in strategies:
            trs = results.get((sym, strat), [])
            cl = [t for t in trs if t.result not in ("", "OPEN")]
            if cl:
                strat_count += 1
            all_trades.extend(trs)
        s = _trade_stats(all_trades, balance)
        if s["trades"] == 0:
            continue
        coin_summary.append((sym, strat_count, s))
    coin_summary.sort(key=lambda x: x[2]["pnl"], reverse=True)
    for sym, sc, s in coin_summary:
        mark = ""
        if s["trades"] >= 8:
            if s["win_rate"] >= 55 and s["pnl"] > 0:
                mark = " ★"
            elif s["win_rate"] < 40 or s["pnl"] < -10:
                mark = " ⚠"
        print(f"  {sym:<14} {sc:>7} {s['trades']:>8} "
              f"{s['win_rate']:>6.1f}% {s['pnl']:>+11.2f} "
              f"{s['avg_pnl']:>+10.3f}{mark}")

    # ── 3.5 平倉原因分布（每策略）── 根因診斷 ──────────────
    print(f"\n{'='*78}")
    print(f"  平倉原因分布（每策略，跨所有幣）—— 根因診斷")
    print(f"{'='*78}")
    print(f"  {'Strategy':<10} {'Reason':<12} {'Count':>7} {'Pct%':>7} "
          f"{'PnL(U)':>10} {'AvgPnL':>9}")
    print(f"  {'-'*68}")
    for strat in strategies:
        all_trades = []
        for sym in symbols:
            all_trades.extend(results.get((sym, strat), []))
        s = _trade_stats(all_trades, balance)
        if s["trades"] == 0:
            continue
        rc = s.get("reason_counts", {})
        rp = s.get("reason_pnl", {})
        # 排序：count 由高到低
        for reason, cnt in sorted(rc.items(), key=lambda x: -x[1]):
            pct = cnt / s["trades"] * 100
            pnl = rp.get(reason, 0.0)
            avg = pnl / cnt if cnt else 0.0
            print(f"  {strat:<10} {reason:<12} {cnt:>7} {pct:>6.1f}% "
                  f"{pnl:>+10.2f} {avg:>+9.3f}")
        print(f"  {'-'*68}")

    # ── 4. 建議 ────────────────────────────────────────────
    print(f"\n{'='*78}")
    print(f"  建議（基於 trades >= 8 的幣 / >= 30 的策略）")
    print(f"{'='*78}")

    boost = [c for c in coin_summary
             if c[2]["trades"] >= 8 and c[2]["win_rate"] >= 55
             and c[2]["pnl"] > 0]
    blacklist = [c for c in coin_summary
                 if c[2]["trades"] >= 8 and (
                     c[2]["win_rate"] < 40 or c[2]["pnl"] < -10
                 )]
    if boost:
        print("  ★ 推薦加權 / 維持：")
        for sym, _, s in boost:
            print(f"     {sym:<14} Win {s['win_rate']:>5.1f}%  "
                  f"PnL {s['pnl']:>+8.2f}  ({s['trades']} 單)")
    if blacklist:
        print("  ⚠ 黑名單候選：")
        for sym, _, s in blacklist:
            print(f"     {sym:<14} Win {s['win_rate']:>5.1f}%  "
                  f"PnL {s['pnl']:>+8.2f}  ({s['trades']} 單)")
    if not boost and not blacklist:
        print("  （無明顯偏離；資料量可能不足或表現均衡）")


def _run_mr_scan(client: Client, months: int, adx_max: float,
                 balance: float) -> None:
    """批量測試多幣種，找出目前最適合均值回歸的幣種。"""
    print(f"\n{'='*60}")
    print(f"  MR 幣種掃描模式（{len(_MR_SCAN_SYMBOLS)} 幣）")
    print(f"  ADX 上限：{adx_max:.0f}  回測期間：{months} 個月  資料來源：正式 API ✓")
    print(f"{'='*60}\n")

    results = []
    for sym in _MR_SCAN_SYMBOLS:
        print(f"  測試 {sym:<12}", end="", flush=True)
        try:
            trades = run_backtest_mr(client, sym, months, debug=False,
                                     adx_max=adx_max)
        except Exception as e:
            print(f"  錯誤：{e}")
            results.append({"symbol": sym, "trades": 0, "win_rate": 0.0,
                            "pnl": 0.0, "pct": 0.0, "maxdd": 0.0})
            continue

        closed = [t for t in trades if t.result not in ("", "OPEN")]
        if not closed:
            print(f"  → 0 筆交易（無入場條件）")
            results.append({"symbol": sym, "trades": 0, "win_rate": 0.0,
                            "pnl": 0.0, "pct": 0.0, "maxdd": 0.0})
            continue

        wins      = [t for t in closed if t.net_pnl > 0]
        total_pnl = sum(t.net_pnl for t in closed)
        win_rate  = len(wins) / len(closed) * 100
        pct       = total_pnl / balance * 100
        bal = balance; peak = bal; mdd = 0.0
        for t in closed:
            bal += t.net_pnl
            peak = max(peak, bal)
            mdd  = max(mdd, (peak - bal) / peak * 100)

        flag = "  ★" if win_rate >= 40 and total_pnl > 0 else ""
        print(f"  → {len(closed):>3} 單  勝率 {win_rate:>5.1f}%  "
              f"PnL {total_pnl:>+8.1f} USDT ({pct:>+6.1f}%){flag}")
        results.append({"symbol": sym, "trades": len(closed),
                        "win_rate": win_rate, "pnl": total_pnl,
                        "pct": pct, "maxdd": mdd})

    # 排名表
    print(f"\n{'='*60}")
    print(f"  幣種排名（依總損益由高到低）")
    print(f"{'='*60}")
    print(f"  {'':2} {'Coin':<12} {'Trades':>7} {'WinRate':>8} "
          f"{'PnL(U)':>10} {'Return%':>8} {'MaxDD%':>8}")
    print(f"  {'-'*58}")
    ranked = sorted(results, key=lambda x: x["pnl"], reverse=True)
    for rank, r in enumerate(ranked, 1):
        if r["trades"] == 0:
            print(f"  {rank:>2} {r['symbol']:<12} {'—':>7} {'—':>8} {'—':>10} {'—':>8} {'—':>8}")
            continue
        star = "★" if r["win_rate"] >= 40 and r["pnl"] > 0 else " "
        print(f"  {rank:>2} {r['symbol']:<12} {r['trades']:>7} "
              f"{r['win_rate']:>7.1f}% {r['pnl']:>+10.1f} "
              f"{r['pct']:>+7.1f}% {r['maxdd']:>7.1f}%  {star}")

    best = [r for r in ranked if r["win_rate"] >= 40 and r["pnl"] > 0]
    if best:
        print(f"\n  ★ 推薦幣種：{', '.join(r['symbol'] for r in best)}")
    else:
        print(f"\n  ⚠ 目前所有幣種均不適合 MR（市場趨勢性過強或參數需調整）")
        print(f"    建議：換更嚴格 ADX：--adx-max 18  或等待市場橫盤")


# ── 主程式 ───────────────────────────────────────────────────────
def main():
    global INITIAL_BALANCE, MIN_SCORE, BT_FIB_TOL, BT_VOL_MULT, BT_SKIP_VOL_RISE, BT_SKIP_BAD_FIB
    parser = argparse.ArgumentParser(description="裸K+Fib 策略回測")
    parser.add_argument("--symbol",         default=DEFAULT_SYMBOL, help="幣種，預設 BTCUSDT")
    parser.add_argument("--tf",             action="append",        help="時間框架，可多次指定")
    parser.add_argument("--months",         type=int,   default=DEFAULT_MONTHS,    help="回測月數")
    parser.add_argument("--balance",        type=float, default=INITIAL_BALANCE,   help="起始資金")
    parser.add_argument("--score",          type=int,   default=MIN_SCORE,         help="最低訊號強度")
    parser.add_argument("--fib-tol",        type=float, default=BT_FIB_TOL,        help=f"Fib 容忍度 (預設{BT_FIB_TOL}，真實bot=0.005)")
    parser.add_argument("--vol-mult",       type=float, default=BT_VOL_MULT,       help=f"成交量倍率門檻 (預設{BT_VOL_MULT}，真實bot=1.3)")
    parser.add_argument("--skip-vol-rise",  action="store_true", default=BT_SKIP_VOL_RISE,  help="不要求當根成交量>前根")
    parser.add_argument("--no-skip-bad-fib",action="store_true",                   help="包含低R:R的0.236/0.786位")
    parser.add_argument("--strategy",       default="naked_k_fib",
                        type=str,
                        help=("回測策略：naked_k_fib / mean_reversion / "
                              "breakdown_short / momentum_long / smc_sweep / all"
                              "；支援逗號分隔列表（例：bd,ml,smc 或 "
                              "breakdown_short,momentum_long,smc_sweep）"))
    parser.add_argument("--max-bars",       type=int,   default=48,
                        help="NKF 最大持倉根數（超時平倉，預設 48）")
    parser.add_argument("--testnet",        action="store_true",
                        help="強制使用 Testnet API 抓 K 線（預設使用正式 API 抓歷史資料）")
    parser.add_argument("--debug-indicators", action="store_true",
                        help="印出近 20 根實際 RSI/ADX 數據，被哪個條件擋掉")
    parser.add_argument("--adx-max",         type=float, default=25.0,
                        help="MR ADX 上限（預設 25，試試 20 更嚴格選幣）")
    parser.add_argument("--scan",            action="store_true",
                        help="批量掃描多幣種，找出最適合 MR 的幣種")
    parser.add_argument("--symbols",         default=None,
                        help="多幣回測：逗號分隔列表（覆蓋 --symbol）")
    parser.add_argument("--top-n",           type=int, default=0,
                        help="多幣回測：自動抓全市場 USDT 合約成交量前 N 大")
    parser.add_argument("--exclude-stable",  action="store_true", default=True,
                        help="--top-n 時排除穩定幣對（USDC/FDUSD 等）")
    parser.add_argument("--no-regime",       dest="use_regime",
                        action="store_false", default=True,
                        help="多幣模式關閉 regime 模擬（預設 ON，對齊 live）")
    args = parser.parse_args()

    timeframes = args.tf or DEFAULT_TF

    INITIAL_BALANCE   = args.balance
    MIN_SCORE         = args.score
    BT_FIB_TOL        = args.fib_tol
    BT_VOL_MULT       = args.vol_mult
    BT_SKIP_VOL_RISE  = args.skip_vol_rise
    BT_SKIP_BAD_FIB   = not args.no_skip_bad_fib

    # ── 重要：回測抓歷史 K 線預設用正式 API ──────────────────────
    # Testnet 歷史資料只有幾週且不準確，會導致大量 TIMEOUT 而非真實 TP/SL
    # 正式 API 的 klines 是公開資料，不需要真實 API Key 也能查詢
    # 只有下單才需要正式 Key，回測全程不下單，所以這樣使用是安全的
    use_testnet = args.testnet  # 預設 False（用正式 API 抓 K 線）
    if use_testnet:
        print("⚠ 警告：使用 Testnet 抓歷史 K 線。Testnet 資料不足且不準確，")
        print("  建議移除 --testnet 改用正式 API（回測全程不下單，安全無風險）。")
    client = Client(
        os.getenv("BINANCE_API_KEY", ""),
        os.getenv("BINANCE_SECRET", ""),
        testnet=use_testnet,
    )

    # 解析 --strategy（支援逗號分隔列表 + 短別名）
    _ALIAS_MAP = {
        "nkf": "naked_k_fib", "mr": "mean_reversion",
        "bd":  "breakdown_short", "ml": "momentum_long",
        "smc": "smc_sweep",
    }
    _VALID = {"naked_k_fib", "mean_reversion", "breakdown_short",
              "momentum_long", "smc_sweep", "all"}
    _raw_strats = [s.strip().lower() for s in args.strategy.split(",") if s.strip()]
    _expanded = []
    for s in _raw_strats:
        s = _ALIAS_MAP.get(s, s)
        if s not in _VALID:
            parser.error(
                f"無效策略名稱：{s}（有效：{', '.join(sorted(_VALID))}）"
            )
        _expanded.append(s)
    _strats_set = set(_expanded)
    _is_all = "all" in _strats_set

    run_nkf = _is_all or "naked_k_fib"     in _strats_set
    run_mr  = _is_all or "mean_reversion"  in _strats_set
    run_bd  = _is_all or "breakdown_short" in _strats_set
    run_ml  = _is_all or "momentum_long"   in _strats_set
    run_smc = _is_all or "smc_sweep"       in _strats_set

    # ── 掃描模式（優先）──────────────────────────────────────────
    if args.scan:
        _run_mr_scan(client, months=args.months, adx_max=args.adx_max,
                     balance=args.balance)
        return

    # ── 多幣回測模式（--symbols 或 --top-n 觸發）────────────────
    multi_mode = bool(args.symbols) or (args.top_n and args.top_n > 0)
    if multi_mode:
        symbols = _resolve_symbol_list(args, client)
        run_flags = {
            "nkf": run_nkf, "mr": run_mr, "bd": run_bd,
            "ml": run_ml, "smc": run_smc,
        }
        active_strats = [k.upper() for k, v in run_flags.items() if v]
        print(f"\n{'='*78}")
        print(f"  多幣多策略回測  共 {len(symbols)} 幣 × "
              f"{len(active_strats)} 策略 = {len(symbols)*len(active_strats)} 組合")
        print(f"  策略：{', '.join(active_strats)}  期間：{args.months} 個月")
        print(f"  起始資金：{args.balance} USDT  每筆保證金：{MARGIN_USDT:.0f} USDT")
        print(f"  資料來源：{'Testnet ⚠' if use_testnet else '正式 API ✓'}")
        print(f"  幣種列表：{', '.join(symbols[:10])}"
              + (f", ... +{len(symbols)-10}" if len(symbols) > 10 else ""))
        print(f"{'='*78}")
        results = _run_multi_coin_backtest(client, symbols, args, run_flags)
        _print_multi_summary(results, args.balance)
        return

    print(f"\n{'='*60}")
    print(f"  多策略回測")
    print(f"  幣種：{args.symbol}  策略：{args.strategy}  資料來源：{'Testnet ⚠' if use_testnet else '正式 API ✓'}")
    if run_nkf:
        print(f"  [NKF] 時間框架：{timeframes}  超時：{args.max_bars} 根")
    if run_mr:
        print(f"  [MR]  時間框架：{Config.MR_TIMEFRAME}  超時：{os.getenv('MR_TIMEOUT_BARS', '24')} 根")
    if run_bd:
        print(f"  [BD]  時間框架：{Config.BD_TIMEFRAME}  超時：{Config.BD_TIMEOUT_BARS} 根  做空突破策略")
    if run_ml:
        print(f"  [ML]  時間框架：{Config.ML_TIMEFRAME}  超時：{Config.ML_TIMEOUT_BARS} 根  做多突破策略")
    if run_smc:
        print(f"  [SMC] 時間框架：{Config.SMC_TIMEFRAME}  超時：{Config.SMC_TIMEOUT_BARS} 根  Liquidity Sweep + Reversal")
    print(f"  回測期間：最近 {args.months} 個月")
    print(f"  起始資金：{args.balance} USDT  槓桿：{LEVERAGE}x  每筆保證金：{MARGIN_USDT:.0f} USDT")
    if run_nkf:
        print(f"  [NKF] Fib 容忍度：±{BT_FIB_TOL*100:.1f}%  成交量門檻：{BT_VOL_MULT}x")
    print(f"{'='*60}")

    all_nkf = []
    all_mr  = []
    all_bd  = []
    all_ml  = []
    all_smc = []

    # ── NKF 回測 ──────────────────────────────────────────────────
    if run_nkf:
        for tf in timeframes:
            trades = run_backtest(client, args.symbol, tf, args.months,
                                  max_bars=args.max_bars)
            print_stats(trades, tf, args.symbol, args.balance, label="NKF")
            all_nkf.extend(trades)
        if len(timeframes) > 1 and all_nkf:
            print_stats(all_nkf, "+".join(timeframes), args.symbol,
                        args.balance, label="NKF 合併")

    # ── MR 回測 ───────────────────────────────────────────────────
    if run_mr:
        if args.debug_indicators:
            _print_mr_indicator_snapshot(client, args.symbol, args.months,
                                         adx_max=args.adx_max)
        trades_mr = run_backtest_mr(client, args.symbol, args.months,
                                    debug=args.debug_indicators,
                                    adx_max=args.adx_max)
        print_stats(trades_mr, Config.MR_TIMEFRAME, args.symbol,
                    args.balance, label="MR")
        all_mr.extend(trades_mr)

    # ── BD 回測 ───────────────────────────────────────────────────
    if run_bd:
        trades_bd = run_backtest_bd(client, args.symbol, args.months,
                                    debug=args.debug_indicators)
        print_stats(trades_bd, Config.BD_TIMEFRAME, args.symbol,
                    args.balance, label="BD")
        all_bd.extend(trades_bd)

    # ── ML 回測 ───────────────────────────────────────────────────
    if run_ml:
        trades_ml = run_backtest_ml(client, args.symbol, args.months,
                                    debug=args.debug_indicators)
        print_stats(trades_ml, Config.ML_TIMEFRAME, args.symbol,
                    args.balance, label="ML")
        all_ml.extend(trades_ml)

    # ── SMC 回測 ──────────────────────────────────────────────────
    if run_smc:
        trades_smc = run_backtest_smc(client, args.symbol, args.months,
                                      debug=args.debug_indicators)
        print_stats(trades_smc, Config.SMC_TIMEFRAME, args.symbol,
                    args.balance, label="SMC")
        all_smc.extend(trades_smc)

    # ── 合併統計（only when running all）─────────────────────────
    if args.strategy == "all" and (all_nkf or all_mr or all_bd or all_ml or all_smc):
        combined = all_nkf + all_mr + all_bd + all_ml + all_smc
        combined.sort(key=lambda t: t.open_time or datetime.min)
        strats = "+".join(
            s for s, flag in [
                ("NKF", bool(all_nkf)), ("MR", bool(all_mr)),
                ("BD",  bool(all_bd)),  ("ML", bool(all_ml)),
                ("SMC", bool(all_smc)),
            ] if flag
        )
        print_stats(combined, "ALL", args.symbol, args.balance, label=f"{strats} 合併")


if __name__ == "__main__":
    main()
