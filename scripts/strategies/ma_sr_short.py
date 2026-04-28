"""
ma_sr_short.py — MA + 水平支撐破位做空策略（MASR Short）

核心設計原則（來自規格，絕對不對稱反向 MASR Long）：
  1. 只在「BTC 偏空 + 個幣已破位 + 短時間框架確認」三條件都成立才開倉
  2. 1H 時間框架（vs MASR Long 4H）：抓「破位後續跌」短週期
  3. 移動停利更積極（SL 1.2×ATR vs Long 1.5×ATR）
  4. 24h 強制平倉，不過夜套

選幣（每次 scan，日線 + BTC 大盤）：
  1. BTC 4H EMA50 < EMA200 且 BTC 24h 漲幅 < +2%（mandatory regime gate）
  2. 流動性：30 日均量 > 50M USDT、上市 ≥ 6 個月
  3. 趨勢結構：日線 EMA50 < EMA200
  4. 已破位：過去 7 日跌幅 > 5% 且 距 30 日高已下跌 > 15%
  5. 排除避險資產（PAXGUSDT/XAUUSDT）、穩定幣、槓桿代幣

進場（1H K 線）：
  a. 找支撐位 S：近 100 根 1H 至少 2 次測試（容差 ATR×0.3）
  b. 上一根 1H close < S（破位）
  c. 當前 1H close < S（2-bar 確認）
  d. EMA20 < EMA50（短期空頭排列）
  e. 量能 > 20 根均量 × 1.5
  f. ATR 不在近 100 根的最高 20%

避免錯誤做空（追殺保險）：
  - 4H RSI > 30（不在超賣做空）
  - 距日線 EMA200 跌幅 < 10%（不追深殺）
  - 24h 跌幅 < 8%（不追加速殺）

出場：
  SL  = entry + 1.2×ATR（比 Long 1.5 緊）
  TP1 = entry - 2×sl_dist（50%）
  TP2 = entry - 4×sl_dist（50%，live 改用 EMA20 trail；
        backtest 用 fixed 4R 模擬「漲破 EMA20 出場」的近似）
  TP1 後 SL → entry（保本）
  24h 強制平倉
"""
import logging
import numpy as np
import pandas as pd
import pandas_ta as ta
from typing import Optional, List

from binance.client import Client
from .base_strategy import BaseStrategy, Signal

log = logging.getLogger("strategy.masr_short")


_LEVERAGE_TAGS  = ("UP", "DOWN", "BULL", "BEAR")
_STABLE_PREFIX  = ("USDC", "FDUSD", "TUSD", "BUSD", "DAI")


class MaSrShortStrategy(BaseStrategy):

    def __init__(self, client: Client, market_ctx=None, db=None):
        self._client = client
        self._market_ctx = market_ctx
        self._db = db
        # cache BTC regime check 結果，避免每幣重抓
        self._btc_regime_cached: Optional[bool] = None
        self._btc_regime_ts: float = 0.0

    @property
    def name(self) -> str:
        return "ma_sr_short"

    @property
    def default_timeframe(self) -> str:
        from config import Config
        return Config.MASR_SHORT_TIMEFRAME

    # ── K 線取得 ─────────────────────────────────────────────────
    def _get_klines(self, symbol: str, interval: str,
                    limit: int = 200) -> pd.DataFrame:
        if self._market_ctx is not None and hasattr(
                self._market_ctx, "get_klines"):
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

    # ── BTC 大盤 regime 過濾（mandatory）───────────────────────────
    def _btc_regime_allows_short(self) -> bool:
        """
        BTC 4H EMA50 < EMA200 且 BTC 24h 漲幅 < +2% 才允許做空。
        不在此條件則整個策略全幣停擺。
        cache 60 秒避免每次 check 都重抓。
        """
        from config import Config
        import time

        now = time.time()
        if (self._btc_regime_cached is not None
                and now - self._btc_regime_ts < 60):
            return self._btc_regime_cached

        try:
            tf = Config.MASR_SHORT_BTC_HTF_TIMEFRAME
            df_btc = self._get_klines("BTCUSDT", tf, limit=250)
            if len(df_btc) < 200:
                log.debug("[MASR_SHORT] BTC 4h 樣本不足，regime 拒絕")
                self._btc_regime_cached = False
                self._btc_regime_ts = now
                return False

            close_btc = df_btc["close"]
            ema_fast = ta.ema(close_btc, length=int(Config.MASR_SHORT_BTC_FAST_EMA))
            ema_slow = ta.ema(close_btc, length=int(Config.MASR_SHORT_BTC_SLOW_EMA))
            if ema_fast is None or ema_slow is None:
                self._btc_regime_cached = False
                self._btc_regime_ts = now
                return False
            f_v = float(ema_fast.iloc[-1])
            s_v = float(ema_slow.iloc[-1])
            if pd.isna(f_v) or pd.isna(s_v):
                self._btc_regime_cached = False
                self._btc_regime_ts = now
                return False

            cond_ema = f_v < s_v

            # BTC 24h 漲幅：用 1d 線 1 根變化（live 也可用 ticker 24h pct）
            df_btc_d = self._get_klines("BTCUSDT", "1d", limit=3)
            if len(df_btc_d) < 2:
                self._btc_regime_cached = False
                self._btc_regime_ts = now
                return False
            prev_close = float(df_btc_d["close"].iloc[-2])
            cur_close_btc = float(df_btc_d["close"].iloc[-1])
            if prev_close <= 0:
                self._btc_regime_cached = False
                self._btc_regime_ts = now
                return False
            btc_24h_pct = (cur_close_btc - prev_close) / prev_close
            cond_24h = btc_24h_pct < float(Config.MASR_SHORT_BTC_MAX_24H_PCT)

            ok = bool(cond_ema and cond_24h)
            log.info(
                f"[MASR_SHORT] BTC regime: EMA50<{Config.MASR_SHORT_BTC_SLOW_EMA}={cond_ema} "
                f"({f_v:.0f} vs {s_v:.0f}) | 24h={btc_24h_pct*100:+.2f}% "
                f"<{Config.MASR_SHORT_BTC_MAX_24H_PCT*100:.1f}%={cond_24h} → "
                f"{'ALLOW' if ok else 'BLOCK'}"
            )
            self._btc_regime_cached = ok
            self._btc_regime_ts = now
            return ok
        except Exception as e:
            log.warning(f"[MASR_SHORT] BTC regime 檢查失敗: {e}")
            return False

    # ── 選幣 ─────────────────────────────────────────────────────
    def screen_coins(self, candidates: List[str]) -> List[str]:
        from config import Config

        # BTC regime 不允許 → 直接 return 空
        if not self._btc_regime_allows_short():
            log.info("[MASR_SHORT] BTC 大盤 regime 拒絕做空，本輪無候選")
            return []

        scored: list[tuple[str, float]] = []

        # 取得 onboardDate
        onboard_map: dict[str, int] = {}
        try:
            info = self._client.futures_exchange_info()
            for s in info.get("symbols", []):
                onboard_map[s["symbol"]] = int(s.get("onboardDate", 0))
        except Exception as e:
            log.debug(f"[MASR_SHORT 篩選] 取得 onboardDate 失敗（略過）: {e}")

        min_listing_days = int(Config.MASR_SHORT_MIN_LISTING_DAYS)
        listing_cutoff_ms = int(
            (pd.Timestamp.utcnow() - pd.Timedelta(days=min_listing_days)).timestamp() * 1000
        )

        vol_min = float(Config.MASR_SHORT_SCREEN_VOL_M) * 1_000_000
        min_7d_drop = float(Config.MASR_SHORT_SCREEN_7D_DROP_PCT)
        min_dist_high = float(Config.MASR_SHORT_SCREEN_DIST_HIGH_PCT)

        # 額外排除（避險資產等）
        excluded = {
            s.strip().upper()
            for s in str(Config.MASR_SHORT_EXCLUDED_SYMBOLS).split(",")
            if s.strip()
        }

        for sym in candidates:
            try:
                upper = sym.upper()

                # 額外排除清單
                if upper in excluded:
                    continue

                # 排除槓桿代幣
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

                # 抓日線
                df_d = self._get_klines(sym, "1d", limit=210)
                if len(df_d) < 200:
                    continue

                # 1. 流動性：30 日均量
                avg_qav = float(df_d["qav"].tail(30).mean())
                if avg_qav < vol_min:
                    continue

                # 2. 日線 EMA 排列：EMA50 < EMA200（空頭結構）
                close_d = df_d["close"]
                ema50_d = ta.ema(close_d, length=50)
                ema200_d = ta.ema(close_d, length=200)
                if ema50_d is None or ema200_d is None:
                    continue
                ema50_v = float(ema50_d.iloc[-1])
                ema200_v = float(ema200_d.iloc[-1])
                if pd.isna(ema50_v) or pd.isna(ema200_v):
                    continue
                if not (ema50_v < ema200_v):
                    continue

                price_d = float(close_d.iloc[-1])

                # 3. 過去 7 日跌幅 > 5%
                if len(close_d) < 8:
                    continue
                price_7d_ago = float(close_d.iloc[-8])
                if price_7d_ago <= 0:
                    continue
                pct_7d = (price_d - price_7d_ago) / price_7d_ago  # 負值代表下跌
                if pct_7d > -min_7d_drop:
                    continue  # 跌不夠

                # 4. 距 30 日高已下跌 > 15%
                if len(df_d) < 31:
                    continue
                high_30d = float(df_d["high"].tail(30).max())
                if high_30d <= 0:
                    continue
                dist_high = (price_d - high_30d) / high_30d  # 負值（已跌離高點）
                if dist_high > -min_dist_high:
                    continue  # 離高點不夠遠

                # 5. 排序鍵：跌得越深排越前面（dist_high 越負越前）
                scored.append((sym, dist_high))
            except Exception as e:
                log.debug(f"[MASR_SHORT 篩選] {sym} 失敗: {e}")

        # 距 30d 高跌幅排序由深到淺
        scored.sort(key=lambda x: x[1])
        top_n = int(Config.MASR_SHORT_TOP_N)
        selected = [s[0] for s in scored[:top_n]]
        log.info(
            f"[MASR_SHORT] 選幣完成：{len(selected)} 支入選 "
            f"（{len(candidates)} 候選中通過 {len(scored)} 後取 top {top_n}）"
        )
        return selected

    # ── 訊號偵測 ─────────────────────────────────────────────────
    def check_signal(self, symbol: str) -> Optional[Signal]:
        from config import Config

        # BTC regime 不允許 → 不開倉
        if not self._btc_regime_allows_short():
            return None

        try:
            df = self._get_klines(
                symbol, self.default_timeframe,
                limit=max(150, int(Config.MASR_SHORT_RES_LOOKBACK) + 30),
            )
        except Exception as e:
            log.warning(f"[{symbol}] MASR_SHORT K 線取得失敗: {e}")
            return None

        if len(df) < int(Config.MASR_SHORT_RES_LOOKBACK) + 5:
            return None

        # 用倒數第二根（已收盤確認）作為「當前」i
        df_a = df.iloc[:-1].copy().reset_index(drop=True)
        if len(df_a) < int(Config.MASR_SHORT_RES_LOOKBACK) + 3:
            return None

        latest = df_a.iloc[-1]   # i: 當前 1H K 線
        prev   = df_a.iloc[-2]   # i-1: 上一根
        cur_close = float(latest["close"])
        cur_vol   = float(latest["volume"])
        prev_close = float(prev["close"])

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

        # 條件 d: EMA20 < EMA50（短期空頭排列）
        if ema20_v >= ema50_v:
            log.debug(f"[{symbol}] MASR_SHORT 拒絕：EMA20 ≥ EMA50")
            return None

        # ATR 不在近 100 根的最高 20%（避免在波動爆炸時追殺）
        atr_recent = atr_s.iloc[-int(Config.MASR_SHORT_RES_LOOKBACK):]
        atr_q = float(atr_recent.quantile(0.80))
        if atr_v >= atr_q:
            log.debug(
                f"[{symbol}] MASR_SHORT 拒絕：ATR 過熱 {atr_v:.4f} ≥ q80={atr_q:.4f}"
            )
            return None

        # 找支撐位 S
        support = self._find_active_support(df_a, atr_v)
        if support is None:
            log.debug(
                f"[{symbol}] MASR_SHORT 拒絕：找不到 ≥{Config.MASR_SHORT_RES_MIN_TOUCHES} 次測試的支撐"
            )
            return None

        # 條件 a + b: 2-bar 確認 — i-1 close < S 且 i close < S
        if not (prev_close < support and cur_close < support):
            log.debug(
                f"[{symbol}] MASR_SHORT 拒絕：2-bar 確認失敗 "
                f"(prev_close={prev_close:.4f}, cur_close={cur_close:.4f}, S={support:.4f})"
            )
            return None

        # 條件 c: 量能 > 1.5× 均量
        avg_vol = float(df_a["volume"].iloc[-21:-1].mean())
        if avg_vol <= 0:
            return None
        vol_ratio = cur_vol / avg_vol
        if vol_ratio < float(Config.MASR_SHORT_VOL_MULT):
            log.debug(
                f"[{symbol}] MASR_SHORT 拒絕：量能 {vol_ratio:.2f}× < "
                f"{Config.MASR_SHORT_VOL_MULT}×"
            )
            return None

        # ── 反追殺保險 ─────────────────────────────────────────
        # (A) 4H RSI > 30（不在超賣做空）
        try:
            df_4h = self._get_klines(
                symbol, Config.MASR_SHORT_RSI_HTF_TIMEFRAME, limit=80
            )
            if len(df_4h) < int(Config.MASR_SHORT_RSI_PERIOD) + 5:
                log.debug(f"[{symbol}] MASR_SHORT 拒絕：4H 樣本不足無法算 RSI")
                return None
            rsi_s = ta.rsi(df_4h["close"], length=int(Config.MASR_SHORT_RSI_PERIOD))
            if rsi_s is None or pd.isna(rsi_s.iloc[-1]):
                return None
            rsi_v = float(rsi_s.iloc[-1])
            if rsi_v <= float(Config.MASR_SHORT_RSI_MIN):
                log.debug(
                    f"[{symbol}] MASR_SHORT 拒絕：4H RSI {rsi_v:.1f} ≤ "
                    f"{Config.MASR_SHORT_RSI_MIN}（已超賣，不追殺）"
                )
                return None
        except Exception as e:
            log.warning(f"[{symbol}] MASR_SHORT 4H RSI 檢查失敗: {e}")
            return None

        # (B) 距日線 EMA200 跌幅 < 10%
        try:
            df_d = self._get_klines(symbol, "1d", limit=210)
            if len(df_d) >= 200:
                ema200_d = ta.ema(df_d["close"], length=200)
                if ema200_d is not None and not pd.isna(ema200_d.iloc[-1]):
                    ema200_dv = float(ema200_d.iloc[-1])
                    if ema200_dv > 0:
                        dist_ema200 = (cur_close - ema200_dv) / ema200_dv
                        # dist_ema200 < 0 = 在 EMA200 下方
                        # 若 |dist| > MAX 表示已跌太深
                        if dist_ema200 < -float(Config.MASR_SHORT_MAX_DIST_FROM_EMA200):
                            log.debug(
                                f"[{symbol}] MASR_SHORT 拒絕：距日線 EMA200 "
                                f"{dist_ema200*100:.1f}%（已跌太深，不追殺）"
                            )
                            return None
        except Exception as e:
            log.debug(f"[{symbol}] MASR_SHORT 日線 EMA200 檢查失敗: {e}")

        # (C) 24h 跌幅 < 8%（不追加速殺）
        try:
            if len(df_d) >= 2:
                close_24h_ago = float(df_d["close"].iloc[-2])
                if close_24h_ago > 0:
                    pct_24h = (cur_close - close_24h_ago) / close_24h_ago
                    if pct_24h < -float(Config.MASR_SHORT_MAX_24H_DROP_PCT):
                        log.debug(
                            f"[{symbol}] MASR_SHORT 拒絕：24h 跌幅 "
                            f"{pct_24h*100:.1f}%（加速殺，不追）"
                        )
                        return None
        except Exception as e:
            log.debug(f"[{symbol}] MASR_SHORT 24h 跌幅檢查失敗: {e}")

        # ── 計算 SL / TP ─────────────────────────────────────────
        sl = cur_close + float(Config.MASR_SHORT_SL_ATR_MULT) * atr_v
        if sl <= cur_close:
            log.debug(f"[{symbol}] MASR_SHORT 拒絕：SL {sl:.4f} ≤ entry {cur_close:.4f}")
            return None
        sl_dist = sl - cur_close
        tp1 = cur_close - float(Config.MASR_SHORT_TP1_RR) * sl_dist
        tp2 = cur_close - float(Config.MASR_SHORT_TP2_RR) * sl_dist
        if tp1 <= 0 or tp2 <= 0:
            return None

        # ── 評分 ─────────────────────────────────────────────────
        score = self._score_signal(
            df_a, cur_close, ema20_v, ema50_v, atr_v,
            atr_recent, vol_ratio, support, rsi_v,
        )
        if score < int(Config.MASR_SHORT_MIN_SCORE):
            log.debug(
                f"[{symbol}] MASR_SHORT 訊號強度 {score} < {Config.MASR_SHORT_MIN_SCORE}"
            )
            return None

        sig = Signal(
            symbol        = symbol,
            side          = "SHORT",
            entry_price   = cur_close,
            stop_loss     = sl,
            take_profit_1 = tp1,
            take_profit_2 = tp2,
            score         = score,
            strategy_name = self.name,
            timeframe     = self.default_timeframe,
            pattern       = "MASR_BREAKDOWN",
            use_trailing  = True,        # TP1 後啟用 trailing（live 用 EMA20 trail）
            trailing_atr  = atr_v,
            metadata      = {
                "support":   round(support, 6),
                "ema20":     round(ema20_v, 6),
                "ema50":     round(ema50_v, 6),
                "atr":       round(atr_v, 6),
                "vol_ratio": round(vol_ratio, 2),
                "rsi_4h":    round(rsi_v, 2),
            },
        )

        if not self.validate_signal(sig):
            log.debug(f"[{symbol}] MASR_SHORT TP/SL 不合理，捨棄")
            return None

        log.info(
            f"[{symbol}] MASR_SHORT 訊號：SHORT S={support:.4f} "
            f"close={cur_close:.4f} EMA20={ema20_v:.4f} RSI4h={rsi_v:.1f} 強度={score}"
        )
        return sig

    # ── 找關鍵支撐位 ──────────────────────────────────────────────
    def _find_active_support(
        self,
        df: pd.DataFrame,
        atr: float,
    ) -> Optional[float]:
        """
        從近 lookback 根 lows 中聚類找出至少 min_touches 次測試的水平支撐，
        取剛被跌破的（即 close 剛跌破的最近支撐）。
        """
        from config import Config
        lookback = int(Config.MASR_SHORT_RES_LOOKBACK)
        tolerance = atr * float(Config.MASR_SHORT_RES_TOL_ATR_MULT)
        min_touches = int(Config.MASR_SHORT_RES_MIN_TOUCHES)

        if len(df) < lookback or atr <= 0 or tolerance <= 0:
            return None

        lows = df["low"].iloc[-lookback:].values
        cur_close = float(df["close"].iloc[-1])

        clusters: list[float] = []
        used: set[int] = set()
        for i in range(len(lows)):
            if i in used:
                continue
            cluster_vals = [lows[i]]
            used.add(i)
            for j in range(i + 1, len(lows)):
                if j in used:
                    continue
                if abs(lows[i] - lows[j]) <= tolerance:
                    cluster_vals.append(lows[j])
                    used.add(j)
            if len(cluster_vals) >= min_touches:
                clusters.append(float(np.mean(cluster_vals)))

        if not clusters:
            return None

        # 取剛被跌破的：≥ cur_close - tolerance 的最低一個（= 最接近 close 從上方）
        breakable = [s for s in clusters if s >= cur_close - tolerance]
        if not breakable:
            return None
        return min(breakable)

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
        support: float,
        rsi_4h: float,
    ) -> int:
        score = 1  # 基礎分（已通過所有 hard filters）

        # ADX > 30 加分（確認下跌結構強）
        try:
            adx_df = ta.adx(df["high"], df["low"], df["close"], length=14)
            adx_val = float(adx_df["ADX_14"].iloc[-1]) if adx_df is not None else 0.0
            if adx_val >= 30:
                score += 1
        except Exception:
            pass

        # EMA50 / EMA20 距離（趨勢清晰度）
        if ema20_v > 0:
            ema_gap_pct = (ema50_v - ema20_v) / ema20_v
            if ema_gap_pct >= 0.01:  # ≥ 1%
                score += 1

        # 量能爆量加分
        if vol_ratio >= 2.0:
            score += 1

        # RSI 中位區（30-50）= 還沒超賣，動能仍在
        if 35 <= rsi_4h <= 55:
            score += 1

        return min(score, 5)
