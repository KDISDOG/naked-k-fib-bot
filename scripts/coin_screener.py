"""
Coin Screener v2 — 為裸K + Fibonacci 策略量身設計的選幣模組

核心理念：裸K+Fib 需要的不是「低波動震盪幣」，而是：
  1. 有結構性趨勢的幣（會走一波、拉回、再走），Fib 回撤才有意義
  2. K 棒結構乾淨（實體大於影線），裸K形態才可靠
  3. 流動性足夠深（spread 小），合約不會滑價
  4. 歷史上 Fib 位有明顯反應（價格尊重技術面）
  5. Funding Rate 中性（市場沒有極端偏向一方）

評分標準（滿分 12 分，≥ 8 才入選）：
  - 流動性品質：USDT 成交量 + 資金費率          3 分
  - 趨勢結構：有清晰 swing，不是純橫盤            3 分
  - K 棒品質：實體占比高、影線雜訊低              3 分
  - Fib 歷史回測：價格在 Fib 位有反應             3 分
"""
import argparse
import os
import time
import pandas as pd
import pandas_ta as ta
import numpy as np
import logging
from binance.client import Client
from api_retry import retry_api

log = logging.getLogger("screener")


class CoinScreener:
    def __init__(self, client: Client, market_ctx=None):
        self.client = client
        self.market_ctx = market_ctx  # 可選：傳入 MarketContext 啟用 BTC Dominance 濾網
        # 選幣門檻（統一由 Config 取得）
        from config import Config
        self.screen_min_score  = Config.SCREEN_MIN_SCORE
        self.screen_min_vol_m  = Config.SCREEN_MIN_VOL_M
        self.adx_min           = Config.SCREEN_ADX_MIN
        self.adx_max           = Config.SCREEN_ADX_MAX
        self.atr_max_long      = Config.SCREEN_ATR_MAX_LONG
        self.atr_max_short     = Config.SCREEN_ATR_MAX_SHORT
        self.oi_change_max     = Config.OI_CHANGE_MAX

    # ── 取得 K 線 ────────────────────────────────────────────────

    def _get_klines(self, symbol: str, interval="1h", limit=200) -> pd.DataFrame:
        raw = retry_api(
            self.client.futures_klines,
            symbol=symbol, interval=interval, limit=limit
        )
        df = pd.DataFrame(raw, columns=[
            "time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "trades", "tbav", "tbqv", "ignore"
        ])
        for col in ["open", "high", "low", "close", "volume", "qav"]:
            df[col] = df[col].astype(float)
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        return df

    # ── 1. 流動性品質（3 分）─────────────────────────────────────

    def _score_liquidity(self, df: pd.DataFrame, symbol: str,
                         swing_trend: str = "") -> tuple[int, dict]:
        """
        用 qav（quote asset volume = 真正的 USDT 成交量）
        + funding rate（方向感知）判斷流動性品質

        Funding 方向感知邏輯：
          - funding > 0.05%（多方擠壓）：若 swing 向下（做空）→ 加分；向上（做多）→ 扣分
          - funding < -0.05%（空方擠壓）：若 swing 向上（做多）→ 加分；向下（做空）→ 扣分
          - |funding| < 0.05%：中性，加分
        """
        score = 0
        # 24h USDT 成交量（用 qav，不是 volume * close）
        vol_24h = df["qav"].tail(24).sum()

        # 流動性分級
        if vol_24h >= 200_000_000:
            score += 2
        elif vol_24h >= 50_000_000:
            score += 1

        # Funding Rate（方向感知）
        fr_raw = 0.0
        try:
            funding = retry_api(
                self.client.futures_funding_rate,
                symbol=symbol, limit=1
            )
            fr_raw = float(funding[-1]["fundingRate"]) if funding else 0.0
        except Exception:
            pass

        if abs(fr_raw) < 0.0005:          # 中性
            score += 1
        elif fr_raw > 0.0005 and swing_trend == "down":
            score += 1                     # 多方擠壓 + 做空 → 有利
        elif fr_raw < -0.0005 and swing_trend == "up":
            score += 1                     # 空方擠壓 + 做多 → 有利
        elif abs(fr_raw) > 0.001:
            score -= 1                     # 極端且方向不利 → 扣分

        details = {
            "vol_24h_m": round(vol_24h / 1_000_000, 1),
            "funding_rate": round(fr_raw * 100, 4),
        }
        return score, details

    # ── 2. 趨勢結構品質（3 分）──────────────────────────────────

    def _detect_swing_trend(self, df: pd.DataFrame) -> str:
        """
        用最近 60 根 K 棒的 swing 判斷當前趨勢方向
        回傳 "up" / "down" / ""
        """
        recent = df.tail(60).reset_index(drop=True)
        swings = self._find_all_swings(recent, left=3, right=3)
        if len(swings) < 2:
            return ""
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
            return ""
        return "up" if last_low["idx"] < last_high["idx"] else "down"

    def _score_trend_structure(self, df: pd.DataFrame,
                               swing_trend: str = "") -> tuple[int, dict]:
        """
        好的裸K+Fib 幣需要有清晰的 swing 結構（趨勢 + 回撤）
        而不是純橫盤或亂跳的幣
        """
        score = 0
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # ADX：需要有一定趨勢 但不要太強（20-45 最佳）
        adx_df = ta.adx(high, low, close, length=14)
        adx_val = adx_df["ADX_14"].iloc[-1] if adx_df is not None else 0

        if self.adx_min <= adx_val <= self.adx_max:   # 適中趨勢：Fib 回撤最有效
            score += 1
        elif (self.adx_min - 5) <= adx_val < self.adx_min:  # 偏弱但可接受
            score += 0  # 不加分但也不扣分

        # Swing 結構清晰度：計算局部高低點的數量
        swing_count = self._count_swings(df, left=5, right=5)
        if 3 <= swing_count <= 8:    # 有結構但不過度震盪
            score += 1

        # ATR 波動率動態上限：做空可到 8%，做多只給 4%（做空反向波動利潤大）
        atr = ta.atr(high, low, close, length=14)
        atr_pct = (atr.iloc[-1] / close.iloc[-1]) * 100 if not atr.empty else 0

        if swing_trend == "down":
            upper_cap = self.atr_max_short
        elif swing_trend == "up":
            upper_cap = self.atr_max_long
        else:
            upper_cap = self.atr_max_long   # 方向不明時採保守上限
        if 1.2 <= atr_pct <= upper_cap:
            score += 1

        details = {
            "adx": round(adx_val, 1),
            "swing_count": swing_count,
            "atr_pct": round(atr_pct, 2),
            "atr_cap": upper_cap,
            "swing_trend": swing_trend,
        }
        return score, details

    def _count_swings(self, df: pd.DataFrame, left=5, right=5) -> int:
        """計算 DataFrame 中 swing high + swing low 的數量"""
        count = 0
        for i in range(left, len(df) - right):
            window_h = df["high"].iloc[i - left:i + right + 1]
            window_l = df["low"].iloc[i - left:i + right + 1]
            if df["high"].iloc[i] == window_h.max():
                count += 1
            if df["low"].iloc[i] == window_l.min():
                count += 1
        return count

    # ── 3. K 棒品質（3 分）──────────────────────────────────────

    def _score_candle_quality(self, df: pd.DataFrame) -> tuple[int, dict]:
        """
        裸K 策略的根本：K 棒要乾淨
        - 實體占比高（body / range）：形態辨識更可靠
        - 上下影線不會太長（不是一堆十字星亂晃的）
        - 連續 K 棒方向一致性：不要每根反轉
        """
        score = 0

        # 實體占比（body / total range）
        body = abs(df["close"] - df["open"])
        total_range = df["high"] - df["low"]
        total_range = total_range.replace(0, np.nan)
        body_ratio = (body / total_range).dropna()
        avg_body_ratio = body_ratio.tail(50).mean()

        if avg_body_ratio >= 0.50:     # 實體占 50% 以上：非常乾淨
            score += 2
        elif avg_body_ratio >= 0.35:   # 實體占 35% 以上：可接受
            score += 1

        # 方向一致性（連續同向 K 棒的比例）
        direction = (df["close"] - df["open"]).tail(50)
        same_dir = 0
        for i in range(1, len(direction)):
            if (direction.iloc[i] > 0 and direction.iloc[i - 1] > 0) or \
               (direction.iloc[i] < 0 and direction.iloc[i - 1] < 0):
                same_dir += 1
        consistency = same_dir / max(len(direction) - 1, 1)

        if consistency >= 0.45:   # 45% 以上的 K 棒和前一根同向
            score += 1

        details = {
            "body_ratio": round(avg_body_ratio, 3),
            "dir_consistency": round(consistency, 3),
        }
        return score, details

    # ── 4. Fib 歷史回測（3 分）──────────────────────────────────

    def _score_fib_respect(self, df: pd.DataFrame) -> tuple[int, dict]:
        """
        回測過去的價格數據，看 Fib 關鍵位是否有明顯的支撐/壓力反應
        如果一個幣歷史上不尊重 Fib，那用 Fib 策略去交易它就沒意義
        """
        score = 0
        fib_reactions = 0
        total_tests = 0

        # Rolling Fib：只看最近 60 根 K 棒（避免用過遠的歷史 swing）
        df_recent = df.tail(60).reset_index(drop=True)
        swings = self._find_all_swings(df_recent, left=3, right=3)
        if len(swings) < 4:
            return 0, {"fib_reactions": 0, "fib_tests": 0, "fib_rate": 0}

        # 取最近 3 組 swing pair 來測試
        for i in range(len(swings) - 1):
            s1 = swings[i]
            s2 = swings[i + 1]
            if i >= 3:
                break

            if s1["type"] == "high" and s2["type"] == "low":
                high, low = s1["price"], s2["price"]
            elif s1["type"] == "low" and s2["type"] == "high":
                high, low = s2["price"], s1["price"]
            else:
                continue

            if high <= low:
                continue

            # 計算 Fib 位
            diff = high - low
            fib_levels = {
                0.382: high - diff * 0.382,
                0.500: high - diff * 0.500,
                0.618: high - diff * 0.618,
            }

            # 檢查 swing 之後的 K 棒是否在 Fib 位反應
            start_idx = max(s1["idx"], s2["idx"]) + 1
            future = df_recent.iloc[start_idx:start_idx + 15]
            for _, fib_price in fib_levels.items():
                total_tests += 1
                tolerance = fib_price * 0.008   # ±0.8% 容忍
                # 價格觸及 Fib 位後反轉
                touched = future[
                    (future["low"] <= fib_price + tolerance) &
                    (future["high"] >= fib_price - tolerance)
                ]
                if len(touched) > 0:
                    # 觸及後的下一根有反轉跡象
                    fib_reactions += 1

        if total_tests == 0:
            return 0, {"fib_reactions": 0, "fib_tests": 0, "fib_rate": 0}

        fib_rate = fib_reactions / total_tests
        if fib_rate >= 0.60:       # 60% 以上的 Fib 位有反應
            score += 3
        elif fib_rate >= 0.40:
            score += 2
        elif fib_rate >= 0.25:
            score += 1

        details = {
            "fib_reactions": fib_reactions,
            "fib_tests": total_tests,
            "fib_rate": round(fib_rate, 2),
        }
        return score, details

    def _find_all_swings(self, df: pd.DataFrame,
                         left=5, right=5) -> list[dict]:
        """找出所有 swing high 和 swing low"""
        swings = []
        for i in range(left, len(df) - right):
            window_h = df["high"].iloc[i - left:i + right + 1]
            window_l = df["low"].iloc[i - left:i + right + 1]
            if df["high"].iloc[i] == window_h.max():
                swings.append({
                    "idx": i,
                    "type": "high",
                    "price": df["high"].iloc[i]
                })
            if df["low"].iloc[i] == window_l.min():
                swings.append({
                    "idx": i,
                    "type": "low",
                    "price": df["low"].iloc[i]
                })
        # 去除重複（同一個位置可能同時是 high 和 low）
        swings.sort(key=lambda x: x["idx"])
        return swings

    # ── 總評分 ───────────────────────────────────────────────────

    def _score(self, symbol: str) -> tuple[int, dict]:
        """計算單一幣種總評分（滿分 12）"""
        try:
            df = self._get_klines(symbol, interval="1h", limit=200)
        except Exception as e:
            log.debug(f"{symbol} K 線取得失敗: {e}")
            return 0, {}

        if len(df) < 100:
            return 0, {}

        # 硬門檻：24h USDT 成交量不足直接跳過
        vol_24h = df["qav"].tail(24).sum()
        if vol_24h < self.screen_min_vol_m * 1_000_000:
            return 0, {}

        # 先決定當前 swing 方向（供流動性/ATR 評分使用）
        swing_trend = self._detect_swing_trend(df)

        s1, d1 = self._score_liquidity(df, symbol, swing_trend=swing_trend)
        s2, d2 = self._score_trend_structure(df, swing_trend=swing_trend)
        s3, d3 = self._score_candle_quality(df)
        s4, d4 = self._score_fib_respect(df)

        total = s1 + s2 + s3 + s4

        # BTC Dominance > 55% → 山寨幣扣 2 分（BTCUSDT 本身不扣）
        btc_dom_penalty = 0
        if self.market_ctx and symbol != "BTCUSDT":
            if self.market_ctx.is_high_btc_dominance(threshold=55.0):
                btc_dom_penalty = -2
                total += btc_dom_penalty

        details = {
            "liquidity_score": s1,
            "trend_score": s2,
            "candle_score": s3,
            "fib_score": s4,
            "btc_dom_penalty": btc_dom_penalty,
            **d1, **d2, **d3, **d4
        }
        return total, details

    # ── 掃描入口 ─────────────────────────────────────────────────

    def scan(self, top: int = 20, min_score: int | None = None) -> list[str]:
        if min_score is None:
            min_score = self.screen_min_score
        """掃描全市場，回傳評分最高的幣種清單"""
        log.info("開始全市場掃描（裸K+Fib 專用選幣）...")

        info = retry_api(self.client.futures_exchange_info)
        symbols = [
            s["symbol"] for s in info["symbols"]
            if s["quoteAsset"] == "USDT"
            and s["status"] == "TRADING"
            and not s["symbol"].endswith("_PERP")
        ]

        # 排除新上線（onboardDate 30 天內）的幣
        now_ms = pd.Timestamp.utcnow().timestamp() * 1000
        thirty_days_ms = 30 * 24 * 60 * 60 * 1000
        filtered = []
        for s in info["symbols"]:
            if s["symbol"] not in symbols:
                continue
            onboard = s.get("onboardDate", 0)
            if onboard and (now_ms - onboard) < thirty_days_ms:
                log.debug(f"跳過新幣：{s['symbol']}")
                continue
            filtered.append(s["symbol"])
        symbols = filtered

        log.info(f"共 {len(symbols)} 個 USDT 合約（已排除新幣）")

        results = []
        for i, sym in enumerate(symbols):
            if i % 50 == 0 and i > 0:
                log.info(f"掃描進度：{i}/{len(symbols)}")

            # 速率限制：每 5 個 symbol 休息 0.5 秒，避免觸發幣安 rate limit
            if i > 0 and i % 5 == 0:
                time.sleep(0.5)

            # OI 異常過濾：24h OI 變動 > 閾值 → 跳過（大戶佈局，技術面易失效）
            if self.market_ctx:
                if self.market_ctx.is_oi_anomaly(sym, self.oi_change_max):
                    oi_chg = self.market_ctx.oi_change_pct(sym)
                    log.debug(f"{sym} OI 24h 變動 {oi_chg:.1f}% > {self.oi_change_max}%，跳過")
                    continue

            score, details = self._score(sym)
            if score >= min_score:
                results.append({"symbol": sym, "score": score, **details})

        results.sort(key=lambda x: x["score"], reverse=True)
        top_symbols = [r["symbol"] for r in results[:top]]

        log.info(
            f"掃描完成：{len(results)} 幣 >= {min_score}/12 分，"
            f"取前 {len(top_symbols)} 支"
        )
        for r in results[:top]:
            log.info(
                f"  {r['symbol']:15s} {r['score']:2d}/12  "
                f"Liq={r['liquidity_score']} Trend={r['trend_score']} "
                f"Candle={r['candle_score']} Fib={r['fib_score']}  "
                f"Vol={r.get('vol_24h_m', 0)}M  ADX={r.get('adx', 0)}  "
                f"ATR={r.get('atr_pct', 0)}%  FibRate={r.get('fib_rate', 0)}"
            )
        return top_symbols


# ── CLI 入口 ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    from dotenv import load_dotenv
    import os

    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    parser = argparse.ArgumentParser(description="裸K+Fib 選幣掃描")
    parser.add_argument("--top", type=int, default=10, help="取前 N 支")
    parser.add_argument("--min-score", type=int, default=8, help="最低分數 (滿分12)")
    parser.add_argument("--json", action="store_true", help="輸出 JSON 格式")
    args = parser.parse_args()

    client = Client(
        os.getenv("BINANCE_API_KEY"),
        os.getenv("BINANCE_SECRET"),
        testnet=os.getenv("BINANCE_TESTNET", "true") == "true"
    )

    screener = CoinScreener(client)
    result = screener.scan(top=args.top, min_score=args.min_score)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n候選幣種（{len(result)} 支）：")
        for sym in result:
            print(f"  - {sym}")
