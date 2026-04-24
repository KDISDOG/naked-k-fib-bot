"""
裸K + Fib 幣安合約機器人 v5 — 多策略主程式

新增功能：
  1. 多策略架構：naked_k_fib / mean_reversion / all
  2. 策略插件調度（Strategy Router）
  3. 均值回歸超時平倉機制
  4. 統一 Config 管理
  5. Dashboard 可選啟動

執行方式:
  python scripts/bot_main.py
  python scripts/bot_main.py --no-dashboard
  python scripts/bot_main.py --strategy mean_reversion
"""
import os
import sys
import time
import queue
import signal
import logging
import argparse
import threading
import schedule
import numpy as np
from datetime import datetime, timedelta
from dotenv import load_dotenv
from binance.client import Client

# 確保 scripts/ 在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config
from coin_screener import CoinScreener
from signal_engine import SignalEngine
from risk_manager import RiskManager
from order_executor import OrderExecutor
from state_manager import StateManager
from position_syncer import PositionSyncer
from market_context import MarketContext
from strategies.naked_k_fib import NakedKFibStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.breakdown_short import BreakdownShortStrategy
from strategies.momentum_long import MomentumLongStrategy
from notifier import notify
from kline_ws import KlineWSManager

# ── 設定 ───────────────────────────────────────────────────────
load_dotenv()
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)-12s %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("bot")
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.WARNING)

# ── 初始化 ──────────────────────────────────────────────────────
client      = Client(Config.BINANCE_API_KEY, Config.BINANCE_SECRET,
                     testnet=Config.BINANCE_TESTNET)
db          = StateManager(db_path=Config.DB_PATH)
market_ctx  = MarketContext(client)
screener    = CoinScreener(client, market_ctx=market_ctx)
signal_e    = SignalEngine(client, market_ctx=market_ctx)
risk        = RiskManager(client, db, market_ctx=market_ctx)
executor    = OrderExecutor(client, db, market_ctx=market_ctx)
syncer      = PositionSyncer(client, db, executor)

# 策略實例
_nkf_strategy = NakedKFibStrategy(
    client, market_ctx=market_ctx,
    coin_screener=screener, signal_engine=signal_e
)
_mr_strategy = MeanReversionStrategy(client, market_ctx=market_ctx)
_bd_strategy = BreakdownShortStrategy(client, market_ctx=market_ctx)
_ml_strategy = MomentumLongStrategy(client, market_ctx=market_ctx)

# 全局狀態
candidate_symbols: dict[str, list[str]] = {}   # strategy_name → symbols
_candidates_lock = threading.RLock()           # 保護 candidate_symbols（WS worker + main thread 同時讀寫）
bot_paused = False
shutdown_event = threading.Event()
# CLI 覆蓋策略（由 --strategy 參數設定）
_active_strategy_override: str | None = None

# Regime 變化追蹤（啟動時填入、每次 check_signals 比對）
_last_regime_state: dict = {"value": ""}

# ── WS 即時訊號 ────────────────────────────────────────────────
# WS 事件 queue 與 worker thread 由 main() 初始化。
# 用 per-symbol lock 防止 WS 事件與 schedule fallback 同時對同一 symbol 開倉。
ws_event_queue: queue.Queue = queue.Queue(maxsize=10_000)
ws_manager: KlineWSManager | None = None
_symbol_locks: dict[str, threading.Lock] = {}
_symbol_locks_master = threading.Lock()


def _get_symbol_lock(symbol: str) -> threading.Lock:
    with _symbol_locks_master:
        lk = _symbol_locks.get(symbol)
        if lk is None:
            lk = threading.Lock()
            _symbol_locks[symbol] = lk
        return lk


def _strategy_timeframe(strategy_name: str) -> str:
    return {
        "naked_k_fib":      Config.NKF_TIMEFRAME,
        "mean_reversion":   Config.MR_TIMEFRAME,
        "breakdown_short":  Config.BD_TIMEFRAME,
        "momentum_long":    Config.ML_TIMEFRAME,
    }.get(strategy_name, "1h")


def check_regime_change():
    """偵測 BTC regime 變化，切換時透過 Telegram 推播一次"""
    try:
        new = market_ctx.current_regime()
    except Exception as e:
        log.debug(f"regime 取得失敗: {e}")
        return
    old = _last_regime_state.get("value", "")
    if new and old and new != old:
        log.warning(f"BTC Regime 變化：{old} → {new}")
        try:
            notify.regime_changed(old, new)
        except Exception as e:
            log.debug(f"regime 通知失敗: {e}")
    if new:
        _last_regime_state["value"] = new


# ── 策略載入 ─────────────────────────────────────────────────────
def load_strategies() -> list:
    active = _active_strategy_override or Config.ACTIVE_STRATEGY
    all_strategies = {
        "naked_k_fib":      _nkf_strategy,
        "mean_reversion":   _mr_strategy,
        "breakdown_short":  _bd_strategy,
        "momentum_long":    _ml_strategy,
    }
    if active == "all":
        return list(all_strategies.values())
    elif active in all_strategies:
        return [all_strategies[active]]
    else:
        raise ValueError(f"未知策略: {active}")


# ── 選幣後：相關性去重 ─────────────────────────────────────────
def _dedupe_correlated_symbols(symbols: list[str],
                               threshold: float = 0.85,
                               interval: str = "1h",
                               limit: int = 100) -> list[str]:
    """
    依序遍歷候選清單，將與「已保留」幣種 1h 收盤相關係數 > threshold
    的後位幣剔除（保留分數高 / 早出現的那支）。
    避免同一板塊 4~5 支同向押注造成曝險集中。
    """
    if not symbols or len(symbols) <= 1:
        return symbols
    if not getattr(Config, "SCREEN_CORR_DEDUPE_ENABLED", True):
        return symbols

    kept: list[str] = []
    kept_closes: dict[str, np.ndarray] = {}
    dropped_pairs: list[str] = []

    for sym in symbols:
        try:
            df = market_ctx.get_klines(sym, interval, limit)
            close = df["close"].to_numpy()
        except Exception:
            # 抓不到 K 線就保守放行（不因 API 失敗而誤殺候選）
            kept.append(sym)
            continue

        skip = False
        for k in kept:
            k_close = kept_closes.get(k)
            if k_close is None or len(k_close) != len(close) or len(close) < 20:
                continue
            try:
                corr = float(np.corrcoef(close, k_close)[0, 1])
            except Exception:
                continue
            if np.isnan(corr):
                continue
            if corr > threshold:
                dropped_pairs.append(f"{sym}~{k}({corr:.2f})")
                skip = True
                break

        if not skip:
            kept.append(sym)
            kept_closes[sym] = close

    if dropped_pairs:
        log.info(
            f"相關性去重：{len(symbols)} → {len(kept)}，"
            f"剔除 {dropped_pairs[:5]}{'...' if len(dropped_pairs) > 5 else ''}"
        )
    return kept


# ── 選幣任務（每 RESCAN_MIN 分鐘）──────────────────────────────
def scan_coins():
    global candidate_symbols
    log.info("=" * 40)
    log.info("選幣掃描開始")

    # 先取得全市場 USDT 合約列表（所有策略共用）
    # 統一過濾：黑名單 + 30 天新幣（新幣流動性差、MM 操縱風險高）
    try:
        info = client.futures_exchange_info()
        now_ms = int(time.time() * 1000)
        thirty_days_ms = 30 * 24 * 60 * 60 * 1000
        all_symbols = [
            s["symbol"] for s in info["symbols"]
            if s["quoteAsset"] == "USDT"
            and s["status"] == "TRADING"
            and not s["symbol"].endswith("_PERP")
            and not Config.is_excluded_symbol(s["symbol"])
            and not (
                s.get("onboardDate", 0)
                and (now_ms - s["onboardDate"]) < thirty_days_ms
            )
        ]
        log.info(
            f"全市場候選池：{len(all_symbols)} 支（已排除黑名單 + 30 天新幣）"
        )
    except Exception as e:
        log.error(f"取得全市場 symbol 失敗: {e}")
        all_symbols = []

    for strategy in load_strategies():
        try:
            symbols = strategy.screen_coins(all_symbols)
            # 相關性去重（使用 cache 中的 1h K 線，不額外 API 成本）
            corr_thr = getattr(Config, "SCREEN_CORR_THRESHOLD", 0.85)
            symbols = _dedupe_correlated_symbols(symbols, threshold=corr_thr)
            with _candidates_lock:
                candidate_symbols[strategy.name] = symbols
            log.info(
                f"[{strategy.name}] 候選幣種（{len(symbols)} 支）：{symbols}"
            )
        except Exception as e:
            log.error(f"[{strategy.name}] 選幣失敗: {e}")

    # 掃幣後清空 K 線 cache（避免記憶體累積；訊號檢查會重抓最新 K 線）
    try:
        if hasattr(market_ctx, "clear_kline_cache"):
            market_ctx.clear_kline_cache()
    except Exception:
        pass

    # 更新 WS 訂閱集合（新候選池 × 各策略 timeframe）
    try:
        _reconcile_ws_subscriptions()
    except Exception as e:
        log.error(f"WS 訂閱同步失敗: {e}")


def _reconcile_ws_subscriptions():
    """依最新 candidate_symbols 與各策略 timeframe 同步 WS 訂閱。"""
    if ws_manager is None:
        return
    targets: set[tuple[str, str]] = set()
    with _candidates_lock:
        for strat in load_strategies():
            tf = _strategy_timeframe(strat.name)
            for sym in candidate_symbols.get(strat.name, []):
                targets.add((sym, tf))
    ws_manager.reconcile(targets)


# ── 超時平倉（均值回歸 + Breakdown Short）────────────────────────
def check_strategy_timeout():
    """
    策略超時機制（以時間計算，不受排程頻率影響）：
    持倉時間 >= TIMEOUT_BARS × timeframe → 強制市價平倉
    支援：mean_reversion, breakdown_short
    """
    tf_map = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
              "1h": 60, "2h": 120, "4h": 240}

    # 各策略的超時配置
    timeout_configs = [
        {
            "strategy": "mean_reversion",
            "timeframe": Config.MR_TIMEFRAME,
            "timeout_bars": Config.MR_TIMEOUT_BARS,
        },
        {
            "strategy": "breakdown_short",
            "timeframe": Config.BD_TIMEFRAME,
            "timeout_bars": Config.BD_TIMEOUT_BARS,
        },
        {
            "strategy": "momentum_long",
            "timeframe": Config.ML_TIMEFRAME,
            "timeout_bars": Config.ML_TIMEOUT_BARS,
        },
    ]

    now = datetime.now()

    for cfg in timeout_configs:
        tf_min = tf_map.get(cfg["timeframe"], 15)
        timeout_sec = cfg["timeout_bars"] * tf_min * 60
        half_sec    = timeout_sec * 0.5
        trades = db.get_open_by_strategy(cfg["strategy"])

        for t in trades:
            if t["status"] not in ("open", "partial"):
                continue
            if not t.get("opened_at"):
                continue
            try:
                opened = datetime.fromisoformat(t["opened_at"])
            except Exception:
                continue
            elapsed = (now - opened).total_seconds()

            # ── 全時：強制市價平倉 ───────────────────────────
            if elapsed >= timeout_sec:
                bars = int(elapsed / 60 / tf_min)
                log.warning(
                    f"[{t['symbol']}] {cfg['strategy']} 超時平倉"
                    f"（持倉 {bars} 根 {cfg['timeframe']} K 棒）"
                )
                try:
                    executor.close_position_market(
                        t["symbol"], t["id"], close_reason="TIMEOUT"
                    )
                except Exception as e:
                    log.error(
                        f"[{t['symbol']}] {cfg['strategy']} "
                        f"超時平倉失敗: {e}"
                    )
                continue

            # ── 半時：砍半倉 + 移 SL 到保本（未觸發過才做）────
            # 用 breakeven 旗標當 idempotent 保險；
            # status=='partial' 代表 TP1 已打到，已經縮過倉，略過階段止損
            if elapsed >= half_sec \
                    and t["status"] == "open" \
                    and not t.get("breakeven"):
                bars = int(elapsed / 60 / tf_min)
                log.warning(
                    f"[{t['symbol']}] {cfg['strategy']} 半時砍半+保本"
                    f"（已持倉 {bars} 根 {cfg['timeframe']} K 棒，"
                    f"半時 {cfg['timeout_bars']//2} 根）"
                )
                try:
                    ok = executor.partial_close_market(
                        t["symbol"], t["id"], pct=0.5,
                        close_reason="PARTIAL_TIMEOUT",
                    )
                    if ok:
                        executor.move_to_breakeven(
                            t["symbol"], t["id"],
                            entry_price=t.get("entry", 0),
                            direction=t.get("direction", ""),
                        )
                except Exception as e:
                    log.error(
                        f"[{t['symbol']}] {cfg['strategy']} "
                        f"半時階段止損失敗: {e}"
                    )


# ── 進場前快檢查 ────────────────────────────────────────────────
def _entry_still_valid(symbol: str, side: str,
                       signal_entry: float = 0.0) -> bool:
    """
    選幣到進場之間可能隔數分鐘～十幾分鐘，期間市場條件會變。
    進場前快查最敏感的濾網，避免選幣當下適合、進場時已不適合：
      1. funding rate 沒飆極端（|fr| > 0.15%/8h 直接擋）
      2. 相對 BTC 強弱方向仍匹配（LONG 沒跑輸 BTC 太多 / SHORT 沒跑贏太多）
      3. mark price 沒偏離訊號 entry（擋市價單薄市滑價，v4 新增）
    任一項失敗回 False。API 失敗時 fail-open（不阻斷正常交易流程）。
    """
    if not getattr(Config, "PRE_ENTRY_RECHECK_ENABLED", True):
        return True

    # 1. Funding rate 極端檢查（共用 CoinScreener 同門檻 0.15%/8h）
    try:
        fr_data = client.futures_funding_rate(symbol=symbol, limit=1)
        if fr_data:
            fr = float(fr_data[-1]["fundingRate"])
            if abs(fr) > 0.0015:
                log.warning(
                    f"[{symbol}] 進場前檢查：funding {fr*100:.4f}%/8h 極端，擋"
                )
                return False
    except Exception as e:
        log.debug(f"[{symbol}] 進場前 funding 檢查失敗（略過）: {e}")

    # 2. 相對強弱方向仍匹配
    if market_ctx and symbol != "BTCUSDT" and \
            getattr(Config, "SCREEN_REL_STRENGTH_ENABLED", True):
        try:
            coin_pct = market_ctx.price_change_pct_24h(symbol)
            btc_pct = market_ctx.btc_change_pct_24h()
            if coin_pct is not None and btc_pct is not None:
                diff = coin_pct - btc_pct
                min_diff = float(
                    getattr(Config, "SCREEN_REL_STRENGTH_MIN_DIFF", 1.0)
                )
                if side == "LONG" and diff < -min_diff:
                    log.warning(
                        f"[{symbol}] 進場前檢查：LONG 但 rel diff "
                        f"{diff:+.2f}% < -{min_diff}%，擋"
                    )
                    return False
                if side == "SHORT" and diff > min_diff:
                    log.warning(
                        f"[{symbol}] 進場前檢查：SHORT 但 rel diff "
                        f"{diff:+.2f}% > +{min_diff}%，擋"
                    )
                    return False
        except Exception as e:
            log.debug(f"[{symbol}] 進場前 rel strength 檢查失敗: {e}")

    # 3. Mark price 偏離預檢（v4 新增）
    # 訊號→送單之間若 mark price 已偏離 signal entry 過大，下市價單會再放大
    # 滑價（吃訂單簿），order_executor 會觸發「偏離過大，放棄開倉」的緊急平倉，
    # 等於白白吃手續費 + 訊號結構失真。這裡提前擋掉。
    max_dev = float(
        getattr(Config, "PRE_ENTRY_MAX_MARK_DEVIATION", 0.005)
    )
    if signal_entry > 0 and max_dev > 0:
        try:
            mark = client.futures_mark_price(symbol=symbol)
            mark_price = float(mark["markPrice"])
            dev = (mark_price - signal_entry) / signal_entry
            # 方向感知：LONG 若 mark 已拉高（dev > +max_dev）進場就追高；
            #           SHORT 若 mark 已下殺（dev < -max_dev）進場就追殺。
            # 反向偏離（對進場有利那側）不擋，仍入場。
            if side == "LONG" and dev > max_dev:
                log.warning(
                    f"[{symbol}] 進場前檢查：LONG mark={mark_price} 偏離訊號 "
                    f"{signal_entry}（{dev*100:+.2f}%）> {max_dev*100:.2f}%，擋"
                )
                return False
            if side == "SHORT" and dev < -max_dev:
                log.warning(
                    f"[{symbol}] 進場前檢查：SHORT mark={mark_price} 偏離訊號 "
                    f"{signal_entry}（{dev*100:+.2f}%）< -{max_dev*100:.2f}%，擋"
                )
                return False
        except Exception as e:
            log.debug(f"[{symbol}] 進場前 mark price 檢查失敗: {e}")

    return True


_MIN_SCORE_MAP = {
    "naked_k_fib":     lambda: Config.NKF_MIN_SIGNAL_SCORE,
    "mean_reversion":  lambda: Config.MR_MIN_SCORE,
    "breakdown_short": lambda: Config.BD_MIN_SCORE,
    "momentum_long":   lambda: Config.ML_MIN_SCORE,
}
_MIN_RR_MAP = {
    "naked_k_fib":     lambda: Config.NKF_MIN_RR,
    "mean_reversion":  lambda: Config.MR_MIN_RR,
    "breakdown_short": lambda: Config.BD_MIN_RR,
    "momentum_long":   lambda: Config.ML_MIN_RR,
}
# 分倉策略（TP1 成交比例）：
#   MR 0.7：快速反轉、TP2 鮮少觸及，先鎖利
#   BD/ML/NKF 0.3：DB 證據顯示 9 筆 TP2 貢獻 +486（總盈利 68%），
#     極端 fat-tail 分佈下保留 70% 跑尾比 50/50 期望值高
_TP1_SPLIT_MAP = {
    "mean_reversion":  0.7,
    "breakdown_short": 0.3,
    "momentum_long":   0.3,
    "naked_k_fib":     0.3,
}


def _try_open_for_symbol(symbol: str, strategy) -> bool:
    """
    單一 (symbol, strategy) 的訊號檢查 + 開倉流程。

    被 WS 事件 worker 與 schedule fallback check_signals 共用，
    以 per-symbol Lock 保證不會兩條路徑同時對同一幣開倉。

    回傳：True = 開倉成功、False = 未進場（訊號不符 / 風控擋 / 例外）
    """
    lock = _get_symbol_lock(symbol)
    if not lock.acquire(blocking=False):
        log.debug(f"[{symbol}] 另一路徑正在處理，跳過")
        return False
    try:
        # TOCTOU 二次檢查（lock 取得後才算數）
        if db.has_open_position(symbol):
            return False

        # per-strategy cooldown
        bar_min = 15
        if db.in_cooldown(symbol, cooldown_bars=Config.COOLDOWN_BARS,
                          bar_minutes=bar_min, strategy=strategy.name):
            log.debug(f"[{symbol}][{strategy.name}] 冷卻期中，跳過")
            return False

        min_score = _MIN_SCORE_MAP.get(strategy.name, lambda: 3)()

        sig = strategy.check_signal(symbol)
        if not sig:
            return False

        if sig.score < min_score:
            log.debug(
                f"[{symbol}][{strategy.name}] 訊號強度 {sig.score} "
                f"< {min_score}，跳過"
            )
            return False

        # 跨策略反向互斥
        if db.has_opposite_position(symbol, sig.side):
            log.info(
                f"[{symbol}][{strategy.name}] 已有反向倉位，跳過"
                f"（避免跨策略自打架）"
            )
            return False

        log.info(
            f"[{symbol}][{strategy.name}] 訊號確認："
            f"{sig.pattern} 方向={sig.side} 強度={sig.score}"
        )

        # 單邊倉位上限
        if not risk.can_open_more_in_direction(sig.side):
            return False

        # 相關性控管
        if not risk.can_open_direction(symbol, sig.side):
            return False

        min_rr = _MIN_RR_MAP.get(strategy.name, lambda: 1.2)()
        tp1_split = _TP1_SPLIT_MAP.get(strategy.name, 0.3)

        pos = risk.calc_position(
            entry         = sig.entry_price,
            stop_loss     = sig.stop_loss,
            tp1           = sig.take_profit_1,
            tp2           = sig.take_profit_2,
            min_rr        = min_rr,
            tp1_split_pct = tp1_split,
            signal_score  = sig.score,
        )
        if not pos:
            log.warning(f"[{symbol}] 風控拒絕")
            return False

        # 進場前快檢查
        if not _entry_still_valid(symbol, sig.side, sig.entry_price):
            return False

        order = executor.open_position(
            symbol    = symbol,
            direction = sig.side,
            qty       = pos["qty"],
            qty_tp1   = pos["qty_tp1"],
            qty_tp2   = pos["qty_tp2"],
            entry     = sig.entry_price,
            sl        = pos["sl"],
            tp1       = pos["tp1"],
            tp2       = pos["tp2"],
            leverage  = pos["leverage"],
            meta      = {
                "fib_level": sig.fib_level,
                "pattern":   sig.pattern,
                "score":     sig.score,
                "timeframe": sig.timeframe,
                "strategy":  strategy.name,
            },
            use_trailing = sig.use_trailing,
            trailing_atr = sig.trailing_atr,
            btc_corr     = sig.btc_corr,
            strategy     = strategy.name,
        )
        if order:
            log.info(
                f"[{symbol}][{strategy.name}] 開倉成功："
                f"{sig.side} qty={pos['qty']} "
                f"SL={pos['sl']} TP1={pos['tp1']} TP2={pos['tp2']}"
            )
            return True
        log.error(f"[{symbol}] 開倉失敗")
        return False
    except Exception as e:
        log.error(f"[{symbol}][{strategy.name}] 開倉流程例外: {e}")
        return False
    finally:
        lock.release()


# ── 訊號檢查 fallback（每 SIGNAL_CHECK_MIN 分鐘；WS 斷線時兜底）────
def check_signals():
    """
    Schedule 驅動的兜底訊號檢查。
    主要路徑已改為 WS K 線收盤事件（_ws_worker_loop → _dispatch_kline_close），
    此函式存在是為了：
      1. WS 連線異常 / 剛啟動未訂閱完成時仍能觸發訊號
      2. 執行每次輪詢附帶的 regime / 每日虧損 / 超時平倉檢查
    """
    global bot_paused
    if bot_paused:
        log.warning("機器人已暫停，跳過訊號檢查")
        return

    # Regime 變化偵測
    try:
        check_regime_change()
    except Exception as _e:
        log.debug(f"regime 檢查失敗: {_e}")

    # 每日虧損檢查
    if risk.daily_loss_exceeded():
        bot_paused = True
        today_pnl = db.get_today_pnl()
        total_bal = risk._get_total_balance()
        loss_pct = abs(min(today_pnl, 0)) / total_bal if total_bal > 0 else 0
        notify.daily_loss_paused(today_pnl, loss_pct)
        log.error("每日虧損上限觸發，機器人已暫停")
        return

    # 策略超時檢查
    try:
        check_strategy_timeout()
    except Exception as e:
        log.error(f"策略超時檢查失敗: {e}")

    if db.count_open_positions() >= Config.MAX_POSITIONS:
        log.info(f"已達最大倉位數 {Config.MAX_POSITIONS}，等待空位")
        return

    for strategy in load_strategies():
        with _candidates_lock:
            symbols = list(candidate_symbols.get(strategy.name, []))
        for symbol in symbols:
            if db.count_open_positions() >= Config.MAX_POSITIONS:
                return
            if db.has_open_position(symbol):
                continue
            _try_open_for_symbol(symbol, strategy)


# ── WS K 線事件派發 ────────────────────────────────────────────
def _dispatch_kline_close(symbol: str, interval: str, close_time: int):
    """WS worker 拉到一筆 K 線收盤事件後的統一入口。"""
    global bot_paused
    if bot_paused:
        return
    if shutdown_event.is_set():
        return
    try:
        if risk.daily_loss_exceeded():
            return
    except Exception as e:
        log.debug(f"daily_loss 檢查失敗: {e}")

    if db.count_open_positions() >= Config.MAX_POSITIONS:
        return
    if db.has_open_position(symbol):
        return

    for strat in load_strategies():
        if _strategy_timeframe(strat.name) != interval:
            continue
        with _candidates_lock:
            if symbol not in candidate_symbols.get(strat.name, []):
                continue
        try:
            opened = _try_open_for_symbol(symbol, strat)
        except Exception as e:
            log.error(f"[{symbol}][{strat.name}] WS 觸發例外: {e}")
            continue
        if opened:
            # 同 symbol 開倉成功後不繼續跑其他策略
            break


def _ws_worker_loop():
    """
    WS 事件 worker thread 主迴圈。
    從 ws_event_queue 拉 (symbol, interval, close_time_ms) 三元組，
    交給 _dispatch_kline_close 處理。
    """
    log.info("WS worker thread 啟動")
    while not shutdown_event.is_set():
        try:
            item = ws_event_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        except Exception as e:
            log.error(f"WS queue 取訊息失敗: {e}")
            continue

        if item is None:
            continue
        try:
            symbol, interval, close_time = item
            _dispatch_kline_close(symbol, interval, close_time)
        except Exception as e:
            log.error(f"WS 事件分發失敗: {e}")
    log.info("WS worker thread 結束")


# ── 倉位同步任務（每 SYNC_SEC 秒）──────────────────────────────
def sync_positions():
    try:
        syncer.sync()
    except Exception as e:
        log.error(f"倉位同步失敗: {e}")


# ── 每日總結 ────────────────────────────────────────────────────
def send_daily_summary():
    """每日總結通知（每天 23:55 執行）"""
    try:
        today_pnl = db.get_today_pnl()
        stats = db.get_stats()
        open_count = db.count_open_positions()
        # 分策略統計
        per_strat = {}
        try:
            per_strat = db.get_today_stats_by_strategy()
        except Exception as se:
            log.warning(f"取得分策略統計失敗: {se}")
        # 當前 regime
        regime = ""
        try:
            if market_ctx:
                regime = market_ctx.current_regime()
        except Exception:
            pass
        notify.daily_summary(
            today_pnl=today_pnl,
            total_pnl=stats.get("net_pnl", 0),
            win_rate=stats.get("win_rate", 0),
            open_count=open_count,
            per_strategy=per_strat,
            regime=regime,
        )
    except Exception as e:
        log.error(f"每日總結發送失敗: {e}")


# ── 持倉小時報 ─────────────────────────────────────────────────
def send_positions_report():
    """每小時推送所有開倉的即時成效（當前價/TP/SL/ROE/方向/策略）"""
    try:
        trades = db.get_open_trades()
        if not trades:
            notify.positions_report([])
            return

        # 一次抓全市場 mark price（單次 API call）
        price_map: dict[str, float] = {}
        try:
            all_prices = client.futures_mark_price()
            price_map = {
                p["symbol"]: float(p["markPrice"]) for p in all_prices
            }
        except Exception as e:
            log.warning(f"取得 mark price 失敗: {e}")

        items = []
        for t in trades:
            symbol    = t.get("symbol", "")
            direction = t.get("direction", "LONG")
            entry     = float(t.get("entry") or 0)
            qty_total = float(t.get("qty") or 0)
            qty_done  = float(t.get("qty_closed") or 0)
            qty_open  = max(qty_total - qty_done, 0)
            margin    = float(t.get("margin") or 0)
            current   = price_map.get(symbol, entry)

            if direction == "LONG":
                pnl = (current - entry) * qty_open
                price_pct = ((current - entry) / entry * 100) if entry else 0
            else:
                pnl = (entry - current) * qty_open
                price_pct = ((entry - current) / entry * 100) if entry else 0
            roe_pct = (pnl / margin * 100) if margin else 0

            items.append({
                "symbol":         symbol,
                "direction":      direction,
                "strategy":       t.get("strategy", "") or "",
                "entry":          entry,
                "current":        current,
                "tp1":            float(t.get("tp1") or 0),
                "tp2":            float(t.get("tp2") or 0),
                "sl":             float(t.get("sl") or 0),
                "qty":            qty_open,
                "margin":         margin,
                "unrealized_pnl": pnl,
                "price_pct":      price_pct,
                "roe_pct":        roe_pct,
            })

        # 附帶目前 regime（讓用戶一眼看出 bot 為何擋/放行）
        regime = ""
        try:
            regime = market_ctx.current_regime()
        except Exception:
            pass
        notify.positions_report(items, regime=regime)
    except Exception as e:
        log.error(f"持倉小時報發送失敗: {e}")


# ── K 棒收盤對齊 ────────────────────────────────────────────────
def wait_for_candle_close():
    now = datetime.now()
    minutes_to_next = 15 - (now.minute % 15)
    next_15m = now.replace(second=0, microsecond=0) + timedelta(minutes=minutes_to_next)
    wait_until = next_15m + timedelta(seconds=10)
    wait_sec = (wait_until - now).total_seconds()
    if wait_sec > 0 and wait_sec < 900:
        log.info(
            f"等待 15m K 棒收盤：{wait_sec:.0f} 秒後"
            f"（{wait_until.strftime('%H:%M:%S')}）"
        )
        while wait_sec > 0 and not shutdown_event.is_set():
            time.sleep(min(wait_sec, 10))
            wait_sec -= 10


# ── Graceful Shutdown ──────────────────────────────────────────
def handle_shutdown(signum, frame):
    log.warning(f"收到信號 {signum}，準備關閉...")
    shutdown_event.set()


# ── Dashboard 啟動 ─────────────────────────────────────────────
def start_dashboard(port=8089):
    try:
        dashboard_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "dashboard"
        )
        sys.path.insert(0, dashboard_dir)
        import uvicorn
        from server import app
        thread = threading.Thread(
            target=uvicorn.run,
            kwargs={"app": app, "host": "0.0.0.0", "port": port,
                    "log_level": "warning"},
            daemon=True
        )
        thread.start()
        log.info(f"Dashboard 啟動於 http://localhost:{port}")
    except ImportError:
        log.warning("Dashboard 相依套件不足，跳過啟動")
    except Exception as e:
        log.warning(f"Dashboard 啟動失敗: {e}")


# ── 排程設定 ─────────────────────────────────────────────────────
def setup_schedule():
    schedule.every(Config.RESCAN_MIN).minutes.do(scan_coins)
    # 訊號檢查改為 WS 事件驅動（K 線收盤即觸發）；
    # schedule 這條是兜底用：WS 斷線時仍能跑，且順便做 regime / daily loss /
    # 超時平倉檢查。頻率取 max(SIGNAL_CHECK_MIN, 15) 避免浪費 API。
    fallback_min = max(Config.SIGNAL_CHECK_MIN, 15)
    schedule.every(fallback_min).minutes.do(check_signals)
    schedule.every(Config.SYNC_SEC).seconds.do(sync_positions)
    schedule.every().day.at("23:55").do(send_daily_summary)
    schedule.every().hour.do(send_positions_report)
    log.info(
        f"排程設定：掃幣={Config.RESCAN_MIN}分 訊號兜底={fallback_min}分 "
        f"（主路徑 = WS 事件驅動）"
        f" 同步={Config.SYNC_SEC}秒 每日總結=23:55 持倉小時報=每 1 小時"
    )


# ── 主程式 ───────────────────────────────────────────────────────
def main():
    global _active_strategy_override, ws_manager

    parser = argparse.ArgumentParser(description="多策略幣安合約機器人 v5")
    parser.add_argument("--no-dashboard", action="store_true",
                        help="不啟動 Dashboard")
    parser.add_argument("--port", type=int, default=8089,
                        help="Dashboard 埠號")
    parser.add_argument("--skip-wait", action="store_true",
                        help="跳過等待 K 棒收盤")
    parser.add_argument("--strategy",
                        choices=["naked_k_fib", "mean_reversion",
                                 "breakdown_short", "momentum_long", "all"],
                        default=None,
                        help="覆蓋 .env 的 ACTIVE_STRATEGY")
    args = parser.parse_args()

    if args.strategy:
        _active_strategy_override = args.strategy

    active = _active_strategy_override or Config.ACTIVE_STRATEGY

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    log.info("=" * 60)
    log.info(
        f"多策略機器人 v5 啟動 "
        f"{'[TESTNET]' if Config.BINANCE_TESTNET else '[LIVE]'}"
    )
    log.info(
        f"策略：{active}  MAX_POS={Config.MAX_POSITIONS}  "
        f"RESCAN={Config.RESCAN_MIN}m  SIGNAL={Config.SIGNAL_CHECK_MIN}m  "
        f"SYNC={Config.SYNC_SEC}s"
    )
    log.info("=" * 60)

    if not args.no_dashboard:
        start_dashboard(args.port)

    # 啟動時告知當前 regime（CHOPPY 會擋掉 MR/ML/BD，避免用戶以為 bot 壞了）
    _initial_regime = ""
    try:
        _initial_regime = market_ctx.current_regime()
        _last_regime_state["value"] = _initial_regime
    except Exception as _e:
        log.warning(f"啟動時 regime 判定失敗: {_e}")
    notify.bot_started(regime=_initial_regime)
    setup_schedule()

    # 啟動時清理幣安上殘留的孤兒掛單（SL/TP 未被撤銷的殘留）
    try:
        syncer.cleanup_orphan_orders()
        log.info("孤兒掛單清理完成")
    except Exception as e:
        log.warning(f"啟動孤兒清理失敗（不阻斷啟動）: {e}")

    # 初始化 WS 管理器 + worker thread（先建立 manager，scan_coins 會 reconcile）
    try:
        ws_manager = KlineWSManager(
            api_key=Config.BINANCE_API_KEY,
            api_secret=Config.BINANCE_SECRET,
            testnet=Config.BINANCE_TESTNET,
            event_queue=ws_event_queue,
        )
        ws_manager.start()
    except Exception as e:
        log.error(f"WS 管理器啟動失敗，將只依賴 schedule 兜底: {e}")
        ws_manager = None

    ws_worker = threading.Thread(
        target=_ws_worker_loop, name="ws-worker", daemon=True
    )
    ws_worker.start()

    scan_coins()

    if not args.skip_wait:
        wait_for_candle_close()

    # 剛啟動若 WS 還沒訂閱完成就已經錯過 K 線收盤，先跑一次 check_signals 補
    if not shutdown_event.is_set():
        check_signals()

    log.info("進入主迴圈（Ctrl+C 可安全退出）")
    while not shutdown_event.is_set():
        schedule.run_pending()
        time.sleep(1)

    # 關閉流程
    try:
        if ws_manager:
            ws_manager.stop()
    except Exception as e:
        log.warning(f"停止 WS 失敗: {e}")
    try:
        ws_worker.join(timeout=3.0)
    except Exception:
        pass

    notify.bot_stopped()
    log.warning("機器人安全關閉")


if __name__ == "__main__":
    main()

