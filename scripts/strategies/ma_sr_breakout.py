"""
ma_sr_breakout.py — MA + 水平支撐阻力突破策略

核心邏輯：
  在多頭趨勢中（日線 EMA50 > EMA200、價格 > EMA200），
  4h 級別找出近 100 根至少測試 2 次的水平阻力 R，
  當 4h 收盤突破 R + EMA20 > EMA50 + 量能 + ATR 不過熱時做多。

選幣（每次 scan 用日線指標）：
  1. 流動性：30 日均量 > 50M USDT、上市 ≥ 6 個月
  2. 趨勢：日線 EMA50 > EMA200、price > EMA200、距 EMA200 < 50%
  3. 波動度：14 日 ATR / price ∈ [2%, 8%]
  4. 排除穩定幣 / 槓桿代幣（UP/DOWN/BULL/BEAR）
  按 30 日漲幅由高到低取 top N（預設 10）

進場（4h K 線）：
  a. 4h close > R（突破水平阻力）
  b. EMA20 > EMA50（短期多頭排列）
  c. 突破 K 棒量 > 20 根均量 × 1.3
  d. 當前 ATR 不在近 100 根的最高 20%（避免追高在波動爆炸時）
  e. 距 EMA50 漲幅 < 8%（避免追過熱）

出場：
  SL  = max(entry - 1.5×ATR, EMA50_value)（取較緊的 stop）
  TP1 = entry + 2×sl_dist（50% 平倉）
  TP2 = entry + 4×sl_dist（50% 平倉，live 改用 ATR trailing；
        backtest 用 fixed 4R 模擬「跌破 EMA20 出場」的近似）

只做 LONG。多空雙向版本可未來擴充。
"""
import logging
import numpy as np
import pandas as pd
import pandas_ta as ta
from typing import Optional, List

from binance.client import Client
from .base_strategy import BaseStrategy, Signal

log = logging.getLogger("strategy.masr")


_LEVERAGE_TAGS  = ("UP", "DOWN", "BULL", "BEAR")
_STABLE_PREFIX  = ("USDC", "FDUSD", "TUSD", "BUSD", "DAI")


class MaSrBreakoutStrategy(BaseStrategy):

    def __init__(self, client: Client, market_ctx=None, db=None):
        self._client = client
        self._market_ctx = market_ctx
        self._db = db

    @property
    def name(self) -> str:
        return "ma_sr_breakout"

    @property
    def default_timeframe(self) -> str:
        from config import Config
        return Config.MASR_TIMEFRAME

    # ── K 線取得 ─────────────────────────────────────────────────
    def _get_klines(self, symbol: str, interval: str,
                    limit: int = 200) -> pd.DataFrame:
        if self._market_ctx is not None and hasattr(
                self._market_ctx, "get_klines"):
            return self._market_ctx.get_klines(symbol, interval, limit)
        from api_retry import weight_aware_call, klines_weight
        raw = weight_aware_call(
            self._client.futures_klines, weight=klines_weight(limit),
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
        return df.reset_index(drop=True)

    # ── 選幣 ─────────────────────────────────────────────────────
    def screen_coins(self, candidates: List[str]) -> List[str]:
        from config import Config
        scored: list[tuple[str, float]] = []

        # 嘗試從 client 取得交易對 onboardDate（過濾上市時間）
        onboard_map: dict[str, int] = {}
        try:
            from api_retry import get_exchange_info_cached
            info = get_exchange_info_cached(self._client)
            for s in info.get("symbols", []):
                onboard_map[s["symbol"]] = int(s.get("onboardDate", 0))
        except Exception as e:
            log.debug(f"[MASR 篩選] 取得 onboardDate 失敗（略過該過濾）: {e}")

        min_listing_days = int(getattr(Config, "MASR_MIN_LISTING_DAYS", 180))
        listing_cutoff_ms = int(
            (pd.Timestamp.utcnow() - pd.Timedelta(days=min_listing_days)).timestamp() * 1000
        )

        vol_min = float(getattr(Config, "MASR_SCREEN_VOL_M", 50.0)) * 1_000_000
        atr_min = float(getattr(Config, "MASR_SCREEN_ATR_MIN_PCT", 2.0))
        atr_max = float(getattr(Config, "MASR_SCREEN_ATR_MAX_PCT", 8.0))
        ema200_max_pct = float(getattr(Config, "MASR_SCREEN_EMA200_MAX_PCT", 0.50))

        for sym in candidates:
            try:
                # 排除槓桿代幣
                upper = sym.upper()
                if any(tag in upper for tag in _LEVERAGE_TAGS):
                    continue
                # 排除穩定幣
                base = upper.replace("USDT", "")
                if any(base.startswith(p) for p in _STABLE_PREFIX):
                    continue

                # 上市時間過濾
                if onboard_map and sym in onboard_map:
                    if onboard_map[sym] > listing_cutoff_ms:
                        continue  # 不滿 6 個月

                # 抓日線（210 根 = 7 個月，足以算 EMA200）
                df_d = self._get_klines(sym, "1d", limit=210)
                if len(df_d) < 200:
                    continue

                # 1. 流動性：30 日均量
                avg_qav = float(df_d["qav"].tail(30).mean())
                if avg_qav < vol_min:
                    continue

                # 2. 日線 EMA 排列
                close_d = df_d["close"]
                ema50_d = ta.ema(close_d, length=50)
                ema200_d = ta.ema(close_d, length=200)
                if ema50_d is None or ema200_d is None:
                    continue
                ema50_v = float(ema50_d.iloc[-1])
                ema200_v = float(ema200_d.iloc[-1])
                price_d = float(close_d.iloc[-1])
                if pd.isna(ema50_v) or pd.isna(ema200_v):
                    continue
                if not (ema50_v > ema200_v and price_d > ema200_v):
                    continue
                if ema200_v <= 0 or (price_d - ema200_v) / ema200_v > ema200_max_pct:
                    continue

                # 3. ATR / price 在 2-8%
                atr_d = ta.atr(df_d["high"], df_d["low"], df_d["close"], length=14)
                if atr_d is None or pd.isna(atr_d.iloc[-1]):
                    continue
                atr_pct = float(atr_d.iloc[-1]) / price_d * 100
                if not (atr_min <= atr_pct <= atr_max):
                    continue

                # 4. 30 日累積漲幅（排序用 + 最低門檻）
                if len(close_d) < 31:
                    continue
                old_price = float(close_d.iloc[-31])
                if old_price <= 0:
                    continue
                pct_30d = (price_d - old_price) / old_price * 100

                # v3：30 日漲幅最低門檻過濾（過濾橫盤幣）
                min_30d_pct = float(
                    getattr(Config, "MASR_MIN_30D_RETURN_PCT", 0.05)
                ) * 100
                if pct_30d < min_30d_pct:
                    continue

                scored.append((sym, pct_30d))
            except Exception as e:
                log.debug(f"[MASR 篩選] {sym} 失敗: {e}")

        # 按 30 日漲幅排序由高到低
        scored.sort(key=lambda x: x[1], reverse=True)
        top_n = int(getattr(Config, "MASR_TOP_N", 5))
        selected = [s[0] for s in scored[:top_n]]
        log.info(
            f"[MASR] 選幣完成：{len(selected)} 支入選 "
            f"（{len(candidates)} 候選中通過 {len(scored)} 後取 top {top_n}）"
        )
        return selected

    # ── 訊號偵測 ─────────────────────────────────────────────────
    def check_signal(self, symbol: str) -> Optional[Signal]:
        from config import Config

        try:
            df = self._get_klines(
                symbol, self.default_timeframe,
                limit=max(150, int(Config.MASR_RES_LOOKBACK) + 30),
            )
        except Exception as e:
            log.warning(f"[{symbol}] MASR K 線取得失敗: {e}")
            return None

        if len(df) < int(Config.MASR_RES_LOOKBACK) + 5:
            return None

        # 用倒數第二根（已收盤確認）
        df_a = df.iloc[:-1].copy().reset_index(drop=True)
        latest = df_a.iloc[-1]
        cur_close = float(latest["close"])
        cur_vol   = float(latest["volume"])

        # 指標
        ema20 = ta.ema(df_a["close"], length=20)
        ema50 = ta.ema(df_a["close"], length=50)
        atr_s = ta.atr(df_a["high"], df_a["low"], df_a["close"], length=14)
        if ema20 is None or ema50 is None or atr_s is None:
            return None

        ema20_v = float(ema20.iloc[-1])
        ema50_v = float(ema50.iloc[-1])
        atr_v   = float(atr_s.iloc[-1])
        if pd.isna(ema20_v) or pd.isna(ema50_v) or pd.isna(atr_v):
            return None

        # 條件 b: EMA20 > EMA50（短期多頭排列）
        if ema20_v <= ema50_v:
            log.debug(f"[{symbol}] MASR 拒絕：EMA20 ≤ EMA50")
            return None

        # 距 EMA50 漲幅 > 8% 不進場（避免追高）
        if ema50_v <= 0:
            return None
        dist_ema50 = (cur_close - ema50_v) / ema50_v
        if dist_ema50 > float(Config.MASR_MAX_DIST_FROM_EMA50):
            log.debug(
                f"[{symbol}] MASR 拒絕：距 EMA50 +{dist_ema50*100:.1f}% > "
                f"{Config.MASR_MAX_DIST_FROM_EMA50*100:.1f}%（追高）"
            )
            return None

        # 條件 d: ATR 不在近 100 根的最高 20%
        atr_recent = atr_s.iloc[-int(Config.MASR_RES_LOOKBACK):]
        atr_q = float(atr_recent.quantile(float(Config.MASR_ATR_PERCENTILE_MAX)))
        if atr_v >= atr_q:
            log.debug(
                f"[{symbol}] MASR 拒絕：ATR 過熱 {atr_v:.4f} ≥ "
                f"q{int(Config.MASR_ATR_PERCENTILE_MAX*100)}={atr_q:.4f}"
            )
            return None

        # 找阻力位 R
        resistance = self._find_active_resistance(df_a, atr_v)
        if resistance is None:
            log.debug(f"[{symbol}] MASR 拒絕：找不到 ≥{Config.MASR_RES_MIN_TOUCHES} 次測試的阻力")
            return None

        # 條件 a: close > R × (1 + MIN_BREAKOUT_PCT)
        # 加最小突破幅度過濾：避免「貼 R 上方一點點」的假突破
        # 12m 回測：49.6% SL 命中率主因是這種弱突破
        min_break = float(getattr(Config, "MASR_MIN_BREAKOUT_PCT", 0.005))
        if cur_close <= resistance * (1 + min_break):
            breakout_pct = (cur_close - resistance) / resistance
            log.debug(
                f"[{symbol}] MASR 拒絕：突破幅度 {breakout_pct*100:.2f}% < "
                f"{min_break*100:.2f}%（close {cur_close:.4f} R {resistance:.4f}）"
            )
            return None

        # 條件 c: 量能 > 1.3× 均量
        avg_vol = float(df_a["volume"].iloc[-21:-1].mean())
        if avg_vol <= 0:
            return None
        vol_ratio = cur_vol / avg_vol
        if vol_ratio < float(Config.MASR_VOL_MULT):
            log.debug(
                f"[{symbol}] MASR 拒絕：量能 {vol_ratio:.2f}× < "
                f"{Config.MASR_VOL_MULT}×"
            )
            return None

        # ── 計算 SL / TP ─────────────────────────────────────────
        sl_atr = cur_close - float(Config.MASR_SL_ATR_MULT) * atr_v
        sl = max(sl_atr, ema50_v)  # 取較近者（較高的 SL = 較緊的 stop）
        if sl >= cur_close:
            log.debug(f"[{symbol}] MASR 拒絕：SL {sl:.4f} ≥ entry {cur_close:.4f}")
            return None
        sl_dist = cur_close - sl
        tp1 = cur_close + float(Config.MASR_TP1_RR) * sl_dist
        tp2 = cur_close + float(Config.MASR_TP2_RR) * sl_dist

        # ── 評分 ─────────────────────────────────────────────────
        score = self._score_signal(
            df_a, cur_close, ema20_v, ema50_v, atr_v,
            atr_recent, vol_ratio, resistance,
        )
        if score < int(Config.MASR_MIN_SCORE):
            log.debug(
                f"[{symbol}] MASR 訊號強度 {score} < {Config.MASR_MIN_SCORE}"
            )
            return None

        sig = Signal(
            symbol        = symbol,
            side          = "LONG",
            entry_price   = cur_close,
            stop_loss     = sl,
            take_profit_1 = tp1,
            take_profit_2 = tp2,
            score         = score,
            strategy_name = self.name,
            timeframe     = self.default_timeframe,
            pattern       = "MASR_BREAKOUT",
            use_trailing  = True,        # TP1 後啟用 trailing（live 用 ATR trail 近似 EMA20）
            trailing_atr  = atr_v,
            metadata      = {
                "resistance": round(resistance, 6),
                "ema20":      round(ema20_v, 6),
                "ema50":      round(ema50_v, 6),
                "atr":        round(atr_v, 6),
                "vol_ratio":  round(vol_ratio, 2),
                "dist_ema50_pct": round(dist_ema50 * 100, 2),
            },
        )

        if not self.validate_signal(sig):
            log.debug(f"[{symbol}] MASR TP/SL 不合理，捨棄")
            return None

        log.info(
            f"[{symbol}] MASR 訊號：LONG R={resistance:.4f} "
            f"close={cur_close:.4f} EMA20={ema20_v:.4f} 強度={score}"
        )
        return sig

    # ── 找關鍵阻力位 ──────────────────────────────────────────────
    def _find_active_resistance(
        self,
        df: pd.DataFrame,
        atr: float,
    ) -> Optional[float]:
        """
        從近 lookback 根中找出至少 min_touches 次測試的水平阻力，
        取剛被突破的那個（即 close 剛站上去的最高水平）。
        """
        from config import Config
        lookback = int(Config.MASR_RES_LOOKBACK)
        tolerance = atr * float(Config.MASR_RES_TOL_ATR_MULT)
        min_touches = int(Config.MASR_RES_MIN_TOUCHES)

        if len(df) < lookback or atr <= 0 or tolerance <= 0:
            return None

        highs = df["high"].iloc[-lookback:].values
        cur_close = float(df["close"].iloc[-1])

        # 對每個 high 嘗試聚類附近的 highs
        clusters: list[float] = []
        used: set[int] = set()
        for i in range(len(highs)):
            if i in used:
                continue
            cluster_vals = [highs[i]]
            used.add(i)
            for j in range(i + 1, len(highs)):
                if j in used:
                    continue
                if abs(highs[i] - highs[j]) <= tolerance:
                    cluster_vals.append(highs[j])
                    used.add(j)
            if len(cluster_vals) >= min_touches:
                clusters.append(float(np.mean(cluster_vals)))

        if not clusters:
            return None

        # 取剛被突破的：≤ cur_close + tolerance 的最高一個（= 最接近 close 從下方）
        breakable = [l for l in clusters if l <= cur_close + tolerance]
        if not breakable:
            return None
        return max(breakable)

    # ── 訊號評分 ─────────────────────────────────────────────────
    def _score_signal(
        self,
        df: pd.DataFrame,
        cur_close: float,
        ema20_v: float,
        ema50_v: float,
        atr_v: float,
        atr_recent: pd.Series,
        vol_ratio: float,
        resistance: float,
    ) -> int:
        score = 1  # 基礎分（已通過所有 hard filters）

        # ADX > 25 加分
        try:
            adx_df = ta.adx(df["high"], df["low"], df["close"], length=14)
            adx_val = float(adx_df["ADX_14"].iloc[-1]) if adx_df is not None else 0.0
            if adx_val >= 30:
                score += 1
        except Exception:
            pass

        # EMA20 / EMA50 距離（趨勢清晰度）
        if ema50_v > 0:
            ema_gap_pct = (ema20_v - ema50_v) / ema50_v
            if ema_gap_pct >= 0.01:  # ≥ 1%
                score += 1

        # 量能爆量加分
        if vol_ratio >= 2.0:
            score += 1

        # ATR 處於低位（清晰趨勢中突破）
        if len(atr_recent) >= 50:
            q40 = float(atr_recent.quantile(0.40))
            if atr_v <= q40:
                score += 1

        return min(score, 5)
