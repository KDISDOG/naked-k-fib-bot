"""
ma_sr_short.py — MA + 水平支撐破位做空策略（MASR Short）

────────────────────────────────────────────────────────────────────────
P12B 版本變更（2026-04-30）— v1 → v2 移植
────────────────────────────────────────────────────────────────────────
HISTORY:
  v1 (2026-04-30 之前): 5 條 mandatory filter（BTC regime + 個幣日線 EMA cross
    + 7d 跌≥5% + 距 30d 高≤-15% + 反追殺保險），39m × 10 幣只 3 trades 全部 dead。
    audit reports/p12_masr_short_diagnosis_*.md。
  v2 (本檔案 2026-04-30 起): 從 backtest.run_backtest_masr_short_v2 移植。
    - mandatory BTC regime → tiered (strong/weak)，弱做空 0.5× 半倉
    - 個幣日線 EMA cross → 4H EMA fast/slow gate
    - 7d 跌≥-5% → 7d 漲≤+3%（放寬 + 翻方向）
    - 距 30d 高≤-15% → ≤-8%（放寬）
    - 2-bar 破位 → 1-bar (fast) / 1-bar + i+1 offset (slow)
    - vol 1.5× → 1.2×；4H RSI>30 → >35；距 EMA200<10% → <12%

  v1 logic 仍保留在 scripts/backtest.py:run_backtest_masr_short（歷史證據）+
  本檔案的 MaSrShortV1Deprecated（純 archive，不註冊 bot_main / 不暴露為 live）。
  bot_main.py 透過 `from strategies.ma_sr_short import MaSrShortStrategy` 自動拿到 v2。

  Audit 證據:
    - P12 audit (baseline config):
      - v2_slow baseline: ROBUST, adj +24.15U
      - v2_fast baseline: ROBUST, adj +11.66U
    - P12C sweep 後 (LOOKBACK=150 / TOL=0.4 / TP1=1.5 / SL=2.5):
      - v2_fast top3: ROBUST, adj +124.23U（wr_std 2.2pp 最低）
      - v2_slow top1: ROBUST, adj +83.58U
    - default variant = "fast"（P12C sweep 後參數下 fast 顯著領先）

  Production config: P12C fast.top3 — 見 .env.example MASR_SHORT_* 區塊。
  reports/p12c_masr_short_sweep_*.md / p12c5_config_apply_*.md

────────────────────────────────────────────────────────────────────────
v2 規範摘要（active path）
────────────────────────────────────────────────────────────────────────
進場 timeframe: 1H

BTC 大盤 tiered gate（任一觸發即可）:
  - strong：BTC 1D EMA50 < EMA200 → 全倉
  - weak：BTC 4H close < EMA50 且 BTC 24h<+1% → 半倉
  二者都不成立 → 不做空

個幣 4H 趨勢: EMA fast(50) < EMA slow(200)

個幣日線結構（放寬版）:
  - 7d 漲幅 ≤ +3%（不做最近大漲幣）
  - 距 30d 高 ≤ -8%

進場（1H K 線）:
  a. 找支撐 S：近 100 根至少 2 次測試
  b. close < S（1-bar 確認）；slow variant 加 i+1 close < S - 0.2×ATR
  c. EMA20 < EMA50（短期空頭排列）
  d. ATR 不在前 20%
  e. vol > 20 根均量 × 1.2
  f. 4H RSI > 35
  g. 距日線 EMA200 < 12%

出場（同 v1）:
  SL = entry + MASR_SHORT_SL_ATR_MULT × ATR
  TP1 = entry - TP1_RR × sl_dist
  TP2 = entry - TP2_RR × sl_dist
  TP1 後 SL → entry（保本）
  TIMEOUT_BARS 強制平倉

backlog（不在本輪做）:
  - sweep v2 SL/TP/lookback (P12C)
  - shadow comparison hook (P12D)
  - testnet checklist short pair (P12E)
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


class MaSrShortV1Deprecated(BaseStrategy):
    """Deprecated 2026-04-30 (P12B): 5 條 mandatory filter 過嚴 → 39m only 3 trades.
    Logic preserved for archeology only. Not registered in bot_main; not callable.
    See reports/p12_masr_short_diagnosis_*.md.
    """

    def __init__(self, client: Client, market_ctx=None, db=None):
        self._client = client
        self._market_ctx = market_ctx
        self._db = db
        # cache BTC regime check 結果，避免每幣重抓
        self._btc_regime_cached: Optional[bool] = None
        self._btc_regime_ts: float = 0.0

    @property
    def name(self) -> str:
        return "ma_sr_short_v1_deprecated"

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
            from api_retry import get_exchange_info_cached
            info = get_exchange_info_cached(self._client)
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

# ═══════════════════════════════════════════════════════════════════════
# v2 (live-grade，從 backtest.run_backtest_masr_short_v2 移植)
# 任何邏輯變更必須同步更新 backtest.py 並重跑 P12 audit。
# ═══════════════════════════════════════════════════════════════════════


def _align_higher_to_lower_value(df_higher: pd.DataFrame, series: pd.Series,
                                   target_time) -> float:
    """從 higher-TF series 取出 time <= target_time 的最後一個值。
    mirror backtest.py:_align_higher_to_lower 的單點版本。
    """
    if series is None or df_higher is None or len(df_higher) == 0:
        return float("nan")
    times = df_higher["time"].values
    target_np = np.datetime64(pd.Timestamp(target_time))
    idx = np.searchsorted(times, target_np, side="right") - 1
    if idx < 0:
        return float("nan")
    val = series.iloc[idx]
    if pd.isna(val):
        return float("nan")
    return float(val)


def _v2_find_support(lows_arr: np.ndarray, end_idx: int, atr: float,
                      lookback: int, tol_mult: float, min_touches: int,
                      cur_close: float) -> Optional[float]:
    """mirror backtest.py:_bt_masr_short_find_support。
    從 lows_arr[end_idx-lookback:end_idx] 找出 ≥min_touches 次測試的水平支撐，
    取剛被跌破的（≥ cur_close - tolerance 的最低一個）。
    """
    if end_idx < lookback or atr <= 0:
        return None
    window = lows_arr[end_idx - lookback:end_idx]
    if len(window) < lookback:
        return None
    tolerance = atr * tol_mult
    if tolerance <= 0:
        return None

    used = np.zeros(len(window), dtype=bool)
    clusters: list[float] = []
    for i in range(len(window)):
        if used[i]:
            continue
        cluster = [window[i]]
        used[i] = True
        for j in range(i + 1, len(window)):
            if used[j]:
                continue
            if abs(window[i] - window[j]) <= tolerance:
                cluster.append(window[j])
                used[j] = True
        if len(cluster) >= min_touches:
            clusters.append(float(np.mean(cluster)))

    if not clusters:
        return None
    breakable = [s for s in clusters if s >= cur_close - tolerance]
    if not breakable:
        return None
    return min(breakable)


def _v2_check_at_bar(
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    df_1d: pd.DataFrame,
    df_btc_1d: pd.DataFrame,
    df_btc_4h: pd.DataFrame,
    bar_idx_1h: int,
    variant: str = "slow",
) -> Optional[dict]:
    """
    對 df_1h 在 bar_idx_1h 跑 MASR Short v2 進場邏輯。

    這是 source-of-truth port from scripts/backtest.py:run_backtest_masr_short_v2
    （該 fn line 2846+），逐條件對應。任何 logic 變更必須兩邊同步並重跑 P12 audit。

    bar_idx_1h: 已收盤的目標 1H bar index（bar 收盤後產生 signal）。
    回傳 Signal-shaped dict 或 None。

    fast variant: 1-bar 確認
    slow variant: 1-bar 確認 + i+1 close < S - SLOW_OFFSET_ATR × ATR
    """
    from config import Config

    if df_1h is None or len(df_1h) == 0:
        return None
    if bar_idx_1h < 60:
        return None
    lookback = int(Config.MASR_SHORT_RES_LOOKBACK)
    if bar_idx_1h < lookback + 5:
        return None
    # slow variant 要求 i+1 在範圍內
    if variant == "slow" and bar_idx_1h + 1 >= len(df_1h):
        return None
    if bar_idx_1h >= len(df_1h):
        return None

    bar_time = df_1h["time"].iloc[bar_idx_1h]

    # ── 1H 指標 ──────────────────────────────────────────────────
    ema20_s = ta.ema(df_1h["close"], length=20)
    ema50_s = ta.ema(df_1h["close"], length=50)
    atr_s = ta.atr(df_1h["high"], df_1h["low"], df_1h["close"], length=14)
    avg_vol_s = df_1h["volume"].rolling(21).mean().shift(1)
    if ema20_s is None or ema50_s is None or atr_s is None:
        return None

    ema20_v = ema20_s.iloc[bar_idx_1h]
    ema50_v = ema50_s.iloc[bar_idx_1h]
    atr_v = atr_s.iloc[bar_idx_1h]
    avg_vol = avg_vol_s.iloc[bar_idx_1h]
    cur_close = float(df_1h["close"].iloc[bar_idx_1h])
    cur_vol = float(df_1h["volume"].iloc[bar_idx_1h])

    if pd.isna(ema20_v) or pd.isna(ema50_v) or pd.isna(atr_v) \
            or pd.isna(avg_vol) or float(avg_vol) <= 0:
        return None
    ema20_v = float(ema20_v)
    ema50_v = float(ema50_v)
    atr_v = float(atr_v)

    # ── 分級 BTC regime ──────────────────────────────────────────
    btc_d_fast_s = ta.ema(df_btc_1d["close"],
                            length=int(Config.MASR_SHORT_V2_STRONG_FAST_EMA))
    btc_d_slow_s = ta.ema(df_btc_1d["close"],
                            length=int(Config.MASR_SHORT_V2_STRONG_SLOW_EMA))
    bd_fast = _align_higher_to_lower_value(df_btc_1d, btc_d_fast_s, bar_time)
    bd_slow = _align_higher_to_lower_value(df_btc_1d, btc_d_slow_s, bar_time)

    btc_4h_ema_s = ta.ema(df_btc_4h["close"],
                           length=int(Config.MASR_SHORT_V2_WEAK_EMA))
    b4_close = _align_higher_to_lower_value(df_btc_4h, df_btc_4h["close"], bar_time)
    b4_ema = _align_higher_to_lower_value(df_btc_4h, btc_4h_ema_s, bar_time)

    btc_d_close = df_btc_1d["close"]
    btc_d_prev = df_btc_1d["close"].shift(1)
    btc_24h_pct_s = (btc_d_close - btc_d_prev) / btc_d_prev
    b_24h = _align_higher_to_lower_value(df_btc_1d, btc_24h_pct_s, bar_time)

    weak_btc_24h_max = float(Config.MASR_SHORT_V2_WEAK_BTC_24H_MAX)

    strong_short = (not np.isnan(bd_fast) and not np.isnan(bd_slow)
                    and bd_fast < bd_slow)
    weak_short = (not np.isnan(b4_close) and not np.isnan(b4_ema)
                  and not np.isnan(b_24h)
                  and b4_close < b4_ema
                  and b_24h < weak_btc_24h_max)

    if not (strong_short or weak_short):
        return None
    regime_mode = "strong" if strong_short else "weak"

    # ── 個幣 4H 趨勢結構 ──────────────────────────────────────────
    e4f_s = ta.ema(df_4h["close"],
                    length=int(Config.MASR_SHORT_V2_TREND_FAST_EMA))
    e4s_s = ta.ema(df_4h["close"],
                    length=int(Config.MASR_SHORT_V2_TREND_SLOW_EMA))
    e4f = _align_higher_to_lower_value(df_4h, e4f_s, bar_time)
    e4s = _align_higher_to_lower_value(df_4h, e4s_s, bar_time)
    if np.isnan(e4f) or np.isnan(e4s) or e4f >= e4s:
        return None

    # ── 7d 漲幅 ≤ +3% + 距 30d 高 ≤ -8% ─────────────────────────
    close_7d_prev_s = df_1d["close"].shift(7)
    high_30d_s = df_1d["high"].rolling(30).max()
    c_d = _align_higher_to_lower_value(df_1d, df_1d["close"], bar_time)
    c_7d = _align_higher_to_lower_value(df_1d, close_7d_prev_s, bar_time)
    h_30 = _align_higher_to_lower_value(df_1d, high_30d_s, bar_time)
    if np.isnan(c_d) or np.isnan(c_7d) or np.isnan(h_30) or c_7d <= 0 or h_30 <= 0:
        return None
    pct_7d = (c_d - c_7d) / c_7d
    max_7d_pct = float(Config.MASR_SHORT_V2_7D_MAX_RETURN)
    if pct_7d > max_7d_pct:
        return None
    dist_high = (c_d - h_30) / h_30
    min_dist_high = float(Config.MASR_SHORT_V2_DIST_HIGH_PCT)
    if dist_high > -min_dist_high:
        return None

    # ── 1H EMA20 < EMA50 ─────────────────────────────────────────
    if ema20_v >= ema50_v:
        return None

    # ── 找支撐 ───────────────────────────────────────────────────
    lows_arr = df_1h["low"].values
    res_tol_mult = float(Config.MASR_SHORT_RES_TOL_ATR_MULT)
    res_min_touches = int(Config.MASR_SHORT_RES_MIN_TOUCHES)
    support = _v2_find_support(
        lows_arr, bar_idx_1h, atr_v, lookback,
        res_tol_mult, res_min_touches, cur_close,
    )
    if support is None:
        return None

    # ── 1-bar 確認 + slow variant 額外 ───────────────────────────
    if cur_close >= support:
        return None
    if variant == "slow":
        slow_offset_atr = float(Config.MASR_SHORT_V2_SLOW_OFFSET_ATR)
        next_close = float(df_1h["close"].iloc[bar_idx_1h + 1])
        if next_close >= (support - slow_offset_atr * atr_v):
            return None

    # ── 量能 > 1.2× ──────────────────────────────────────────────
    vol_mult = float(Config.MASR_SHORT_V2_VOL_MULT)
    vol_ratio = cur_vol / float(avg_vol)
    if vol_ratio < vol_mult:
        return None

    # ── 4H RSI > 35 ──────────────────────────────────────────────
    rsi_period = int(Config.MASR_SHORT_RSI_PERIOD)
    rsi_4h_s = ta.rsi(df_4h["close"], length=rsi_period)
    rsi_v = _align_higher_to_lower_value(df_4h, rsi_4h_s, bar_time)
    rsi_min = float(Config.MASR_SHORT_V2_RSI_MIN)
    if np.isnan(rsi_v) or rsi_v <= rsi_min:
        return None

    # ── 距日線 EMA200 < 12% ──────────────────────────────────────
    if len(df_1d) >= 200:
        ema200_d_s = ta.ema(df_1d["close"], length=200)
        e200_d = _align_higher_to_lower_value(df_1d, ema200_d_s, bar_time)
        max_dist_e200 = float(Config.MASR_SHORT_V2_MAX_DIST_FROM_EMA200)
        if not np.isnan(e200_d) and e200_d > 0:
            dist_e200 = (cur_close - e200_d) / e200_d
            if dist_e200 < -max_dist_e200:
                return None

    # ── SL / TP（SHORT）─────────────────────────────────────────
    # 注意：mirror backtest v2 (line 3072-3088) — fast 用 i 收盤、slow 用 i+1
    # 收盤當 entry。entry_time 也跟著對齊。backtest v2 沒有 ATR 過熱前置 check
    # （v1 才有），所以本 helper 也不加。
    sl_atr_mult = float(Config.MASR_SHORT_SL_ATR_MULT)
    if variant == "slow":
        entry_idx = bar_idx_1h + 1
        entry = float(df_1h["close"].iloc[entry_idx])
        entry_time = df_1h["time"].iloc[entry_idx]
    else:
        entry_idx = bar_idx_1h
        entry = cur_close
        entry_time = bar_time
    sl = entry + sl_atr_mult * atr_v
    if sl <= entry:
        return None
    sl_dist = sl - entry
    tp1_rr = float(Config.MASR_SHORT_TP1_RR)
    tp2_rr = float(Config.MASR_SHORT_TP2_RR)
    tp1 = entry - tp1_rr * sl_dist
    tp2 = entry - tp2_rr * sl_dist
    if tp1 <= 0 or tp2 <= 0:
        return None

    # ── 評分（mirror backtest v2 score logic）────────────────────
    score = 1
    ema_gap_pct = (ema50_v - ema20_v) / ema20_v if ema20_v > 0 else 0
    if ema_gap_pct >= 0.01:
        score += 1
    if vol_ratio >= 2.0:
        score += 1
    if 35 <= float(rsi_v) <= 55:
        score += 1
    score = min(score, 5)

    min_score = int(Config.MASR_SHORT_MIN_SCORE)
    if score < min_score:
        return None

    return {
        "direction": "SHORT",
        "entry": entry,                 # fast: closes[i]; slow: closes[i+1]
        "entry_time": entry_time,       # match backtest open_time exactly
        "entry_idx": entry_idx,         # for verifier alignment
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "score": score,
        "atr": atr_v,
        "ema20": ema20_v,
        "ema50": ema50_v,
        "support": support,
        "vol_ratio": vol_ratio,
        "rsi_4h": float(rsi_v),
        "regime_mode": regime_mode,    # "strong" / "weak"
    }


class MaSrShortStrategy(BaseStrategy):
    """MASR Short v2 — live-grade strategy (P12B 移植自 backtest)。

    audit: reports/p12_masr_short_diagnosis_*.md
      - v2_slow baseline: ROBUST (adj +24.15U)
      - v2_fast baseline: ROBUST (adj +11.66U)
      - P12C fast.top3 (LOOK=150/TOL=0.4/TP1=1.5/SL=2.5): ROBUST adj +124.23U
    default variant = "fast"（P12C sweep 後 fast 顯著領先；
    P12 baseline 階段 slow 較好但那是不同 config 的對比）。
    """

    def __init__(self, client: Client, market_ctx=None, db=None,
                 variant: Optional[str] = None):
        self._client = client
        self._market_ctx = market_ctx
        self._db = db
        # variant from arg or env (default fast per P12C sweep)
        from config import Config
        v = (variant
              or getattr(Config, "MASR_SHORT_VARIANT", None)
              or "fast").lower()
        if v not in ("slow", "fast"):
            log.warning(f"[MASR_SHORT] 未知 variant={v}，fallback fast")
            v = "fast"
        self.variant = v
        # === P12D：cooldown 狀態（per-symbol Timestamp）─────────
        # 移植自 backtest.py:run_backtest_masr_short_v2 line 3151-3152
        # 觸發條件：raw SL trade 結束（非 BE）；duration = COOLDOWN_BARS × timeframe
        # 詳見 reports/p12d_cooldown_logic.md
        self._cooldown_until: dict[str, "pd.Timestamp"] = {}
        log.info(f"[MASR_SHORT] init variant={self.variant} (cooldown gate enabled)")

    @property
    def name(self) -> str:
        return "ma_sr_short"

    @property
    def default_timeframe(self) -> str:
        from config import Config
        return Config.MASR_SHORT_TIMEFRAME

    # ── K 線取得（同 v1）─────────────────────────────────────────
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

    # ── 選幣（cfd filter + v2 放寬條件）─────────────────────────
    def screen_coins(self, candidates: List[str]) -> List[str]:
        from config import Config

        # ── P10 phase 2 對稱：cfd asset_class filter（疊加）──
        # 純名稱判定，無 cache 依賴；fail-open。
        # P12 audit：cfd filter 對 short 影響微小，但保留對稱性。
        try:
            from feature_filter import classify_asset, load_feature_filter_config
            ff_cfg = load_feature_filter_config()
            excluded_classes = ff_cfg.get("MASR_EXCLUDE_ASSET_CLASSES", ["cfd"])
        except Exception as e:
            log.warning(f"[MASR_SHORT screen] feature_filter 讀取失敗: {e}")
            excluded_classes = []

        if excluded_classes:
            pre_count = len(candidates)
            after_cfd: list[str] = []
            skipped: list[str] = []
            for sym in candidates:
                ac = classify_asset(sym)
                if ac in excluded_classes:
                    skipped.append(f"{sym}({ac})")
                    continue
                after_cfd.append(sym)
            if skipped:
                log.info(
                    f"[MASR_SHORT screen] cfd filter: {pre_count} → {len(after_cfd)} "
                    f"({len(skipped)} skipped: {skipped[:5]}"
                    f"{'...' if len(skipped) > 5 else ''})"
                )
            candidates = after_cfd

        # ── 上市/槓桿/穩定幣排除（同 v1）──
        onboard_map: dict[str, int] = {}
        try:
            from api_retry import get_exchange_info_cached
            info = get_exchange_info_cached(self._client)
            for s in info.get("symbols", []):
                onboard_map[s["symbol"]] = int(s.get("onboardDate", 0))
        except Exception as e:
            log.debug(f"[MASR_SHORT 篩選] onboardDate 取得失敗（略過）: {e}")

        min_listing_days = int(Config.MASR_SHORT_MIN_LISTING_DAYS)
        listing_cutoff_ms = int(
            (pd.Timestamp.utcnow() - pd.Timedelta(days=min_listing_days)).timestamp() * 1000
        )

        # MASR_SHORT_V2_VOL_M（30M USDT，比 v1 50M 鬆）
        vol_min = float(Config.MASR_SHORT_V2_VOL_M) * 1_000_000

        scored: list[tuple[str, float]] = []
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
                df_d = self._get_klines(sym, "1d", limit=35)
                if len(df_d) < 30:
                    continue
                avg_qav = float(df_d["qav"].tail(30).mean())
                if avg_qav < vol_min:
                    continue
                # 距 30d 高排序鍵
                price_d = float(df_d["close"].iloc[-1])
                high_30d = float(df_d["high"].tail(30).max())
                if high_30d <= 0:
                    continue
                dist_high = (price_d - high_30d) / high_30d  # 負值
                scored.append((sym, dist_high))
            except Exception as e:
                log.debug(f"[MASR_SHORT 篩選] {sym} 失敗: {e}")

        # 距 30d 高跌幅由深到淺（最深前面）
        scored.sort(key=lambda x: x[1])
        top_n = int(Config.MASR_SHORT_V2_TOP_N)
        selected = [s[0] for s in scored[:top_n]]
        log.info(
            f"[MASR_SHORT] 選幣完成：{len(selected)} 支入選 "
            f"（{len(candidates)} 候選中通過 {len(scored)} 後取 top {top_n}）"
        )
        return selected

    # ── 訊號偵測 ─────────────────────────────────────────────────
    def check_signal(self, symbol: str) -> Optional[Signal]:
        """逐行對應 backtest.run_backtest_masr_short_v2 的 per-bar 邏輯。
        透過共用 _v2_check_at_bar helper 確保 live 與 backtest 等價。
        含 P12D cooldown gate：raw SL 後 COOLDOWN_BARS × timeframe 時間內不下單。
        """
        from config import Config

        try:
            df_1h = self._get_klines(
                symbol, self.default_timeframe,
                limit=max(150, int(Config.MASR_SHORT_RES_LOOKBACK) + 30),
            )
            df_4h = self._get_klines(symbol, "4h", limit=250)
            df_1d = self._get_klines(symbol, "1d", limit=210)
            df_btc_1d = self._get_klines("BTCUSDT", "1d", limit=250)
            df_btc_4h = self._get_klines("BTCUSDT", "4h", limit=100)
        except Exception as e:
            log.warning(f"[{symbol}] MASR_SHORT K 線取得失敗: {e}")
            return None

        # === P12D：cooldown gate（raw SL 後 COOLDOWN_BARS × timeframe）───
        # mirror backtest.py:run_backtest_masr_short_v2 line 2972-2975 的
        # `if i <= cooldown_until: continue`
        if len(df_1h) >= 2:
            bar_time = pd.Timestamp(df_1h["time"].iloc[-2])  # 倒數第二根 = 已收盤目標 bar
            cooldown_end = self._cooldown_until.get(symbol)
            if cooldown_end is not None and bar_time <= cooldown_end:
                log.debug(
                    f"[{symbol}] MASR_SHORT cooldown 中（直到 {cooldown_end}）"
                )
                return None

        if (len(df_1h) < int(Config.MASR_SHORT_RES_LOOKBACK) + 5
                or len(df_4h) < 50 or len(df_1d) < 30
                or len(df_btc_1d) < 200 or len(df_btc_4h) < 50):
            return None

        # 用倒數第二根作為「已收盤」當前 bar（live K 線最後一根可能還在 forming）
        df_a = df_1h.iloc[:-1].reset_index(drop=True)
        if len(df_a) < int(Config.MASR_SHORT_RES_LOOKBACK) + 3:
            return None

        # slow variant 需要 bar_idx 跟 bar_idx+1 兩根已收盤 → 給 df_a（不含
        # forming bar），bar_idx = len(df_a) - 2 即倒數第二根（i+1 為倒數第一根，
        # 兩根都已收盤）。這意味著 slow live signal 比 fast 晚 1 根 1H 觸發。
        # 對齊 backtest 的 range(warmup, len(df_tf) - 2)：實質範圍相同。
        if self.variant == "slow":
            bar_idx = len(df_a) - 2
            if bar_idx < 60:
                return None
        else:
            bar_idx = len(df_a) - 1

        sig_dict = _v2_check_at_bar(
            df_a, df_4h, df_1d, df_btc_1d, df_btc_4h,
            bar_idx_1h=bar_idx, variant=self.variant,
        )
        if sig_dict is None:
            return None

        sig = Signal(
            symbol        = symbol,
            side          = "SHORT",
            entry_price   = sig_dict["entry"],
            stop_loss     = sig_dict["sl"],
            take_profit_1 = sig_dict["tp1"],
            take_profit_2 = sig_dict["tp2"],
            score         = sig_dict["score"],
            strategy_name = self.name,
            timeframe     = self.default_timeframe,
            pattern       = "MASR_BREAKDOWN_V2",
            use_trailing  = True,
            trailing_atr  = sig_dict["atr"],
            metadata      = {
                "support":    round(sig_dict["support"], 6),
                "ema20":      round(sig_dict["ema20"], 6),
                "ema50":      round(sig_dict["ema50"], 6),
                "atr":        round(sig_dict["atr"], 6),
                "vol_ratio":  round(sig_dict["vol_ratio"], 2),
                "rsi_4h":     round(sig_dict["rsi_4h"], 2),
                "regime":     sig_dict["regime_mode"],
                "variant":    self.variant,
            },
        )

        if not self.validate_signal(sig):
            log.debug(f"[{symbol}] MASR_SHORT TP/SL 不合理，捨棄")
            return None

        log.info(
            f"[{symbol}] MASR_SHORT v2:{self.variant} 訊號：SHORT "
            f"S={sig_dict['support']:.4f} close={sig_dict['entry']:.4f} "
            f"regime={sig_dict['regime_mode']} 強度={sig_dict['score']}"
        )

        # ── P12D：Shadow comparison（live vs backtest 訊號等價性）──
        # 訊號產生時觸發 backtest path 比對（呼叫同 _v2_check_at_bar helper）。
        # 因為 live + backtest 共用同 helper，理論上應 100% match；shadow 主要
        # 偵測 cooldown drift / 未來 logic divergence 的早期警報。
        if getattr(Config, "ENABLE_SHADOW_COMPARE", False):
            try:
                from shadow_runner import shadow_compare_signal_short
                bar_time_for_shadow = (df_a["time"].iloc[bar_idx]
                                        if "time" in df_a.columns else None)
                live_sig_dict = {
                    "direction": sig.side,
                    "entry": sig.entry_price,
                    "sl": sig.stop_loss,
                    "tp1": sig.take_profit_1,
                    "tp2": sig.take_profit_2,
                    "score": sig.score,
                }
                shadow_res = shadow_compare_signal_short(
                    strategy_name="masr_short",
                    symbol=symbol,
                    bar_time=bar_time_for_shadow,
                    live_signal=live_sig_dict,
                    df_klines_1h=df_a,
                    df_klines_4h=df_4h,
                    df_klines_1d=df_1d,
                    df_btc_1d=df_btc_1d,
                    df_btc_4h=df_btc_4h,
                    in_cooldown=False,  # check_signal 已經過 cooldown gate
                    variant=self.variant,
                )
                real_mm = shadow_res.get("real_mismatches", [])
                if real_mm:
                    log.error(
                        f"[{symbol}] SHADOW SHORT REAL_MISMATCH "
                        f"@ {bar_time_for_shadow}: {[d['field'] for d in real_mm]}"
                    )
                    try:
                        from notifier import notify
                        notify.error(
                            f"⚠️ SHADOW SHORT MISMATCH [{symbol}]",
                            extra=(f"bar_time={bar_time_for_shadow}\n"
                                    f"variant={self.variant}\n"
                                    f"fields={[d['field'] for d in real_mm]}\n"
                                    f"live={live_sig_dict}\n"
                                    f"bt={shadow_res.get('backtest_signal')}"),
                        )
                    except Exception as e:
                        log.error(f"[{symbol}] notifier failed: {e}")
                elif shadow_res.get("diffs"):
                    log.info(
                        f"[{symbol}] shadow short accept_diff: "
                        f"{[(d['field'], d.get('classification')) for d in shadow_res['diffs']]}"
                    )
                else:
                    log.debug(f"[{symbol}] shadow short exact match")
            except Exception as e:
                log.error(f"[{symbol}] shadow_compare_signal_short failed: {e}")

        return sig

    # ── P12D：on_position_close hook（cooldown 設定）─────────────
    def on_position_close(self, symbol: str, exit_reason: str,
                           exit_time) -> None:
        """部位關閉後 hook：mirror backtest.py:run_backtest_masr_short_v2
        line 3151-3152 的 `if "SL" in result and "BE" not in result:
        cooldown_until = trade.close_bar + COOLDOWN_BARS`。

        只在 raw SL 觸發 cooldown；TP1+TP2 / TIMEOUT / BE / SL+BE 不觸發。
        cooldown duration = COOLDOWN_BARS × timeframe minutes（fallback to
        `Config.COOLDOWN_BARS=6` if MASR_SHORT_COOLDOWN_BARS unset, mirror
        backtest 用同一 const）。

        Args:
            symbol: 部位幣種
            exit_reason: 關閉原因 string，常見：'SL', 'TP1+TP2', 'TP1+SL',
                         'TP1+BE', 'TIMEOUT', 'MANUAL'
            exit_time: 部位關閉時刻（pd.Timestamp 或 datetime）
        """
        if not exit_reason:
            return
        # 只在 raw SL 觸發 cooldown（mirror backtest）
        # "SL" 必須在 result 內，但 "BE" 不可在 result 內
        if "SL" not in exit_reason or "BE" in exit_reason:
            return
        from config import Config
        # 預設取 MASR_SHORT_COOLDOWN_BARS；fallback 至通用 COOLDOWN_BARS
        cooldown_bars = int(getattr(Config, "MASR_SHORT_COOLDOWN_BARS", 0)) \
                          or int(getattr(Config, "COOLDOWN_BARS", 6))
        # 換算 timeframe 分鐘數
        tf = (Config.MASR_SHORT_TIMEFRAME or "1h").lower()
        tf_min_map = {"1m": 1, "3m": 3, "5m": 5, "15m": 15,
                      "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1d": 1440}
        tf_min = tf_min_map.get(tf, 60)
        try:
            t = pd.Timestamp(exit_time)
        except Exception as e:
            log.warning(f"[{symbol}] on_position_close: 無法 parse exit_time={exit_time}: {e}")
            return
        cooldown_end = t + pd.Timedelta(minutes=cooldown_bars * tf_min)
        self._cooldown_until[symbol] = cooldown_end
        log.info(
            f"[{symbol}] MASR_SHORT cooldown 設定 → 直到 {cooldown_end} "
            f"(reason={exit_reason}, bars={cooldown_bars} × {tf})"
        )
