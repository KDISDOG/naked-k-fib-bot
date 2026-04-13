"""
Signal Engine v2 — 裸K + Fibonacci 訊號偵測引擎

重大改進：
  1. Swing point 改用 fractal 辨識（不再只取 max/min）
  2. TP/SL 基於 Fib 結構（不再用固定 ATR）
  3. 方向判定：多時間框架確認（日線趨勢 + 小時線入場）
  4. K 棒收盤確認：只在 K 棒收盤後才確認形態
  5. Engulfing 方向修正：檢查回傳值正負
  6. 分批止盈目標：TP1（近 Fib）+ TP2（遠 Fib）
"""
import pandas as pd
import pandas_ta as ta
import numpy as np
import logging
from dataclasses import dataclass
from typing import Optional
from datetime import datetime
from binance.client import Client

log = logging.getLogger("signal")


# ── 資料結構 ─────────────────────────────────────────────────────
@dataclass
class Signal:
    symbol:    str
    direction: str          # LONG / SHORT
    entry:     float
    sl:        float        # 止損（基於 Fib 結構）
    tp1:       float        # 第一止盈（近 Fib 位 = 1R）
    tp2:       float        # 第二止盈（遠 Fib 位）
    fib_level: str          # 進場 Fib 位 "0.618" / "0.500" / "0.382"
    pattern:   str          # 裸K 形態名稱
    score:     int          # 訊號強度 1–5
    timeframe: str
    swing_high: float
    swing_low:  float


# ── 常數 ─────────────────────────────────────────────────────────
KEY_FIB_LEVELS = [0.236, 0.382, 0.500, 0.618, 0.786]
FIB_TOLERANCE  = 0.005     # ±0.5%

# Fib TP/SL 映射：進場在哪個 Fib → 止損放哪、止盈放哪
# 做多（回撤買入）：進場在回撤位，止損在更深回撤，止盈在回撤淺處
# key 格式必須和 _calc_fib 產生的 key 一致（round(x, 3) → str）
FIB_LONG_MAP = {
    "0.786": {"sl_fib": 1.0,   "tp1_fib": 0.618, "tp2_fib": 0.382},
    "0.618": {"sl_fib": 0.786, "tp1_fib": 0.382, "tp2_fib": 0.0},
    "0.5":   {"sl_fib": 0.618, "tp1_fib": 0.382, "tp2_fib": 0.0},
    "0.382": {"sl_fib": 0.618, "tp1_fib": 0.236, "tp2_fib": 0.0},
    "0.236": {"sl_fib": 0.382, "tp1_fib": 0.0,   "tp2_fib": 0.0},
}
# 做空（反彈賣出）：鏡像
FIB_SHORT_MAP = {
    "0.236": {"sl_fib": 0.0,   "tp1_fib": 0.382, "tp2_fib": 0.618},
    "0.382": {"sl_fib": 0.236, "tp1_fib": 0.5,   "tp2_fib": 0.786},
    "0.5":   {"sl_fib": 0.382, "tp1_fib": 0.618, "tp2_fib": 0.786},
    "0.618": {"sl_fib": 0.382, "tp1_fib": 0.786, "tp2_fib": 1.0},
    "0.786": {"sl_fib": 0.618, "tp1_fib": 1.0,   "tp2_fib": 1.0},
}

def _normalize_fib_key(key: str) -> str:
    """統一 Fib key 格式：'0.5' 和 '0.500' 都能匹配"""
    return str(round(float(key), 3))


class SignalEngine:
    def __init__(self, client: Client):
        self.client = client

    # ── K 線取得 ─────────────────────────────────────────────────

    def _get_klines(self, symbol: str, interval: str,
                    limit=200) -> pd.DataFrame:
        raw = self.client.futures_klines(
            symbol=symbol, interval=interval, limit=limit
        )
        df = pd.DataFrame(raw, columns=[
            "time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "trades", "tbav", "tbqv", "ignore"
        ])
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = df[c].astype(float)
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
        return df.reset_index(drop=True)

    # ── Swing Point 辨識（Fractal 方法）───────────────────────────

    def _find_swing_fractal(self, df: pd.DataFrame,
                            left=5, right=5) -> list[dict]:
        """
        用 fractal 方式找 swing point：
        一個 swing high = 左邊 left 根和右邊 right 根都比它低
        """
        swings = []
        for i in range(left, len(df) - right):
            # Swing High
            window_h = df["high"].iloc[i - left:i + right + 1]
            if df["high"].iloc[i] == window_h.max():
                swings.append({
                    "idx": i,
                    "type": "high",
                    "price": df["high"].iloc[i],
                    "time": df["time"].iloc[i],
                })
            # Swing Low
            window_l = df["low"].iloc[i - left:i + right + 1]
            if df["low"].iloc[i] == window_l.min():
                swings.append({
                    "idx": i,
                    "type": "low",
                    "price": df["low"].iloc[i],
                    "time": df["time"].iloc[i],
                })
        swings.sort(key=lambda x: x["idx"])
        return swings

    def _get_latest_swing_pair(self, df: pd.DataFrame) -> Optional[tuple]:
        """
        找最近一組有效的 swing high + swing low pair
        回傳 (swing_high_price, swing_low_price, trend_direction)
        trend_direction: "up"（low→high，回撤做多）/ "down"（high→low，回撤做空）
        """
        swings = self._find_swing_fractal(df, left=5, right=5)
        if len(swings) < 2:
            return None

        # 找最近的 high 和 low
        last_high = None
        last_low = None
        for s in reversed(swings):
            if s["type"] == "high" and last_high is None:
                last_high = s
            if s["type"] == "low" and last_low is None:
                last_low = s
            if last_high and last_low:
                break

        if not last_high or not last_low:
            return None

        # 判斷趨勢：先出現的是起點
        if last_low["idx"] < last_high["idx"]:
            trend = "up"     # 先低後高 → 上升趨勢 → 回撤做多
        else:
            trend = "down"   # 先高後低 → 下降趨勢 → 回撤做空

        return last_high["price"], last_low["price"], trend

    # ── Fibonacci 計算 ───────────────────────────────────────────

    def _calc_fib(self, high: float, low: float) -> dict:
        diff = high - low
        return {
            str(round(l, 3)): high - diff * l
            for l in KEY_FIB_LEVELS
        }

    def _price_near_fib(self, price: float,
                        fib_levels: dict) -> Optional[str]:
        """檢查 price 是否在某個 Fib 位 ± FIB_TOLERANCE"""
        for level, fib_price in fib_levels.items():
            if fib_price == 0:
                continue
            if abs(price - fib_price) / fib_price <= FIB_TOLERANCE:
                return level
        return None

    # ── 方向判定（多時間框架）─────────────────────────────────────

    def _determine_direction(self, df_htf: pd.DataFrame,
                             swing_trend: str) -> str:
        """
        多時間框架方向判定：
        1. swing 結構方向（primary）
        2. 日線 EMA 趨勢（confirmation）
        兩者一致才出手
        """
        # 日線趨勢
        ema20 = ta.ema(df_htf["close"], length=20)
        ema50 = ta.ema(df_htf["close"], length=50)
        price = df_htf["close"].iloc[-1]

        if ema20 is None or ema50 is None:
            return "LONG" if swing_trend == "up" else "SHORT"

        htf_bullish = (price > ema20.iloc[-1]) and (ema20.iloc[-1] > ema50.iloc[-1])
        htf_bearish = (price < ema20.iloc[-1]) and (ema20.iloc[-1] < ema50.iloc[-1])

        # 上升 swing + 日線看多 → LONG（回撤買入）
        if swing_trend == "up" and htf_bullish:
            return "LONG"
        # 下降 swing + 日線看空 → SHORT（回彈賣出）
        if swing_trend == "down" and htf_bearish:
            return "SHORT"

        # 不一致 → 不交易（回傳 None 由 check() 處理）
        return ""

    # ── 裸K 形態偵測 ─────────────────────────────────────────────

    def _detect_pattern(self, df: pd.DataFrame,
                        direction: str) -> Optional[tuple[str, int]]:
        """
        偵測裸K形態
        修正：檢查 TA-Lib 回傳值的正負來區分看漲/看跌
        + fallback 手動偵測
        """
        last10 = df.tail(10).copy().reset_index(drop=True)
        o = last10["open"]
        h = last10["high"]
        l = last10["low"]
        c = last10["close"]

        # 定義所有要檢查的形態及其強度
        patterns_to_check = {
            "engulfing":     3,
            "morningstar":   4,
            "eveningstar":   4,
            "hammer":        3,
            "shootingstar":  3,
            "doji":          2,
            "harami":        2,
            "darkcloudcover":3,
            "piercing":      3,
            "invertedhammer":2,
        }

        for name, strength in patterns_to_check.items():
            try:
                result = ta.cdl_pattern(
                    open=o, high=h, low=l, close=c, name=name
                )
                if result is None or result.empty:
                    continue
                last_val = result.iloc[-1]
                if last_val == 0:
                    continue

                # 關鍵修正：用回傳值正負判斷方向
                if direction == "LONG" and last_val > 0:
                    return f"CDL{name.upper()}", strength
                elif direction == "SHORT" and last_val < 0:
                    return f"CDL{name.upper()}", strength
            except Exception:
                continue

        # Fallback: 手動偵測吞噬和 pin bar
        return self._manual_pattern_detect(df, direction)

    def _manual_pattern_detect(self, df: pd.DataFrame,
                               direction: str) -> Optional[tuple[str, int]]:
        """手動偵測：不依賴 pandas-ta 的 CDL 函數"""
        o = df["open"].values
        h = df["high"].values
        l = df["low"].values
        c = df["close"].values

        if direction == "SHORT":
            # 空頭吞噬：前紅後黑，後者實體完全包住前者
            if (c[-2] > o[-2] and c[-1] < o[-1] and
                    o[-1] >= c[-2] and c[-1] <= o[-2]):
                return "BEARISH_ENGULFING", 3

            # 射擊之星：上影線 >= 實體 2 倍，下影線極短
            body = abs(c[-1] - o[-1])
            upper = h[-1] - max(o[-1], c[-1])
            lower = min(o[-1], c[-1]) - l[-1]
            total = h[-1] - l[-1]
            if total > 0 and upper >= body * 2 and lower <= total * 0.1:
                return "SHOOTING_STAR", 3

            # Pin bar（長上影線）
            if total > 0 and upper / total >= 0.65:
                return "BEARISH_PINBAR", 2

        else:  # LONG
            # 多頭吞噬：前黑後紅，後者實體完全包住前者
            if (c[-2] < o[-2] and c[-1] > o[-1] and
                    o[-1] <= c[-2] and c[-1] >= o[-2]):
                return "BULLISH_ENGULFING", 3

            # 錘子線：下影線 >= 實體 2 倍，上影線極短
            body = abs(c[-1] - o[-1])
            upper = h[-1] - max(o[-1], c[-1])
            lower = min(o[-1], c[-1]) - l[-1]
            total = h[-1] - l[-1]
            if total > 0 and lower >= body * 2 and upper <= total * 0.1:
                return "HAMMER", 3

            # Pin bar（長下影線）
            if total > 0 and lower / total >= 0.65:
                return "BULLISH_PINBAR", 2

        return None

    # ── 成交量確認 ───────────────────────────────────────────────

    def _volume_confirmed(self, df: pd.DataFrame,
                          period=20, ratio=1.3) -> bool:
        """
        成交量確認（從 1.5x 放寬到 1.3x）
        因為 Fib 位的反應有時候不需要爆量，只需要量能高於均值
        """
        avg_vol = df["volume"].tail(period + 1).iloc[:-1].mean()
        last_vol = df["volume"].iloc[-1]
        return last_vol >= avg_vol * ratio

    # ── K 棒收盤確認 ─────────────────────────────────────────────

    def _is_candle_closed(self, df: pd.DataFrame) -> bool:
        """
        確認最後一根 K 棒是否已收盤
        避免在 K 棒進行中做判斷（形態可能改變）
        """
        if "close_time" not in df.columns:
            return True   # 無法判斷就預設已收盤
        last_close_time = df["close_time"].iloc[-1]
        now = pd.Timestamp.utcnow().tz_localize(None)  # UTC tz-naive，與 K 線資料一致
        # 已收盤的 K 棒：close_time 應該在現在之前
        return last_close_time <= now

    # ── Fib 結構 TP/SL ──────────────────────────────────────────

    def _calc_fib_tp_sl(self, direction: str, fib_level: str,
                        high: float, low: float) -> dict:
        """
        基於 Fib 結構計算 TP/SL
        而不是用固定的 ATR 倍數
        """
        diff = high - low
        fib_map = FIB_LONG_MAP if direction == "LONG" else FIB_SHORT_MAP

        # 找最接近的映射
        norm_key = _normalize_fib_key(fib_level)
        mapping = fib_map.get(norm_key)
        if not mapping:
            # 找最近的 key
            fib_val = float(fib_level)
            closest = min(fib_map.keys(), key=lambda k: abs(float(k) - fib_val))
            mapping = fib_map[closest]

        sl_price  = high - diff * mapping["sl_fib"]
        tp1_price = high - diff * mapping["tp1_fib"]
        tp2_price = high - diff * mapping["tp2_fib"]

        return {
            "sl":  sl_price,
            "tp1": tp1_price,
            "tp2": tp2_price,
        }

    # ── 主入口 ───────────────────────────────────────────────────

    def check(self, symbol: str, timeframe="1h") -> Optional[Signal]:
        """主入口：檢查某幣是否有入場訊號"""
        try:
            df = self._get_klines(symbol, timeframe, limit=200)
            df_daily = self._get_klines(symbol, "1d", limit=60)
        except Exception as e:
            log.warning(f"{symbol} 取 K 線失敗: {e}")
            return None

        # Step 0: K 棒收盤確認
        # 使用倒數第二根（已確認收盤的）做分析
        if not self._is_candle_closed(df):
            # 最後一根尚未收盤，改用倒數第二根
            df_analysis = df.iloc[:-1].copy().reset_index(drop=True)
        else:
            df_analysis = df

        if len(df_analysis) < 50:
            return None

        # Step 1: 找 swing high/low（日線 fractal）
        swing_result = self._get_latest_swing_pair(df_daily)
        if not swing_result:
            log.debug(f"{symbol} 找不到有效 swing pair")
            return None
        swing_h, swing_l, swing_trend = swing_result

        if swing_h <= swing_l:
            return None

        # Step 2: 計算 Fib 位
        fib_levels = self._calc_fib(swing_h, swing_l)

        # Step 3: 當前價格在哪個 Fib 位？
        price = df_analysis["close"].iloc[-1]
        fib_hit = self._price_near_fib(price, fib_levels)
        if not fib_hit:
            log.debug(
                f"{symbol} 價格 {price:.4f} 不在任何 Fib 位 "
                f"({', '.join(f'{k}={v:.4f}' for k,v in fib_levels.items())})"
            )
            return None

        # Step 4: 多時間框架方向判定
        direction = self._determine_direction(df_daily, swing_trend)
        if not direction:
            log.debug(
                f"{symbol} 方向不一致 swing={swing_trend} 但日線趨勢相反，跳過"
            )
            return None

        # Step 5: 裸K 形態確認（用已收盤的 K 棒）
        pattern_result = self._detect_pattern(df_analysis, direction)
        if not pattern_result:
            log.debug(f"{symbol} {direction} 無裸K形態")
            return None
        pattern_name, pattern_strength = pattern_result

        # Step 6: 成交量確認
        if not self._volume_confirmed(df_analysis, period=20, ratio=1.3):
            log.debug(f"{symbol} 成交量未達 1.3x 均量，跳過")
            return None

        # Step 7: 基於 Fib 結構計算 TP/SL
        tp_sl = self._calc_fib_tp_sl(direction, fib_hit, swing_h, swing_l)

        # 驗證 TP/SL 合理性
        if direction == "LONG":
            if tp_sl["sl"] >= price or tp_sl["tp1"] <= price:
                log.debug(f"{symbol} LONG TP/SL 不合理，跳過")
                return None
        else:
            if tp_sl["sl"] <= price or tp_sl["tp1"] >= price:
                log.debug(f"{symbol} SHORT TP/SL 不合理，跳過")
                return None

        # 計算訊號強度
        score = min(5, pattern_strength
                    + (1 if float(fib_hit) == 0.618 else 0)
                    + (1 if swing_trend == ("up" if direction == "LONG" else "down") else 0))

        log.info(
            f">>> [{symbol}] {direction} @ {price:.4f}  "
            f"Fib={fib_hit}  Pattern={pattern_name}  "
            f"SL={tp_sl['sl']:.4f}  TP1={tp_sl['tp1']:.4f}  "
            f"TP2={tp_sl['tp2']:.4f}  Score={score}"
        )

        return Signal(
            symbol     = symbol,
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
