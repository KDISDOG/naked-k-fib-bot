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
import signal
import logging
import argparse
import threading
import schedule
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
executor    = OrderExecutor(client, db)
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
bot_paused = False
shutdown_event = threading.Event()
# CLI 覆蓋策略（由 --strategy 參數設定）
_active_strategy_override: str | None = None


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


# ── 訊號檢查任務（每 SIGNAL_CHECK_MIN 分鐘）────────────────────
def check_signals():
    global bot_paused
    if bot_paused:
        log.warning("機器人已暫停，跳過訊號檢查")
        return

    # 每日虧損檢查
    if risk.daily_loss_exceeded():
        bot_paused = True
        today_pnl = db.get_today_pnl()
        total_bal = risk._get_total_balance()
        loss_pct = abs(min(today_pnl, 0)) / total_bal if total_bal > 0 else 0
        notify.daily_loss_paused(today_pnl, loss_pct)
        log.error("每日虧損上限觸發，機器人已暫停")
        return

    open_count = db.count_open_positions()
    if open_count >= Config.MAX_POSITIONS:
        log.info(f"已達最大倉位數 {Config.MAX_POSITIONS}，等待空位")
        return

    # 策略超時檢查（MR + BD，每次訊號檢查時順便執行）
    try:
        check_strategy_timeout()
    except Exception as e:
        log.error(f"策略超時檢查失敗: {e}")

    for strategy in load_strategies():
        symbols = candidate_symbols.get(strategy.name, [])
        min_score_map = {
            "naked_k_fib":     Config.NKF_MIN_SIGNAL_SCORE,
            "mean_reversion":  Config.MR_MIN_SCORE,
            "breakdown_short": Config.BD_MIN_SCORE,
            "momentum_long":   Config.ML_MIN_SCORE,
        }
        min_score = min_score_map.get(strategy.name, 3)

        for symbol in symbols:
            if open_count >= Config.MAX_POSITIONS:
                break

            if db.has_open_position(symbol):
                log.debug(f"[{symbol}] 已有開倉，跳過")
                continue

            # per-strategy cooldown：不同策略間不互相封鎖
            bar_min = 15  # 兩策略皆 15m timeframe
            if db.in_cooldown(symbol, cooldown_bars=Config.COOLDOWN_BARS,
                              bar_minutes=bar_min, strategy=strategy.name):
                log.debug(f"[{symbol}][{strategy.name}] 冷卻期中，跳過")
                continue

            # 訊號檢查
            sig = strategy.check_signal(symbol)
            if not sig:
                continue

            if sig.score < min_score:
                log.debug(
                    f"[{symbol}][{strategy.name}] 訊號強度 {sig.score} "
                    f"< {min_score}，跳過"
                )
                continue

            # 跨策略反向互斥：同幣種若已有反向倉位（他策略），跳過
            if db.has_opposite_position(symbol, sig.side):
                log.info(
                    f"[{symbol}][{strategy.name}] 已有反向倉位，跳過"
                    f"（避免跨策略自打架）"
                )
                continue

            log.info(
                f"[{symbol}][{strategy.name}] 訊號確認："
                f"{sig.pattern} 方向={sig.side} 強度={sig.score}"
            )

            # 單邊倉位上限：避免 MAX_POSITIONS=6 全押同向（MAX_LONGS/MAX_SHORTS）
            if not risk.can_open_more_in_direction(sig.side):
                continue

            # 相關性控管（兩策略都需要，避免同向累積曝險）
            if not risk.can_open_direction(symbol, sig.side):
                continue

            # per-strategy 的 R:R 門檻
            min_rr_map = {
                "naked_k_fib":     Config.NKF_MIN_RR,
                "mean_reversion":  Config.MR_MIN_RR,
                "breakdown_short": Config.BD_MIN_RR,
                "momentum_long":   Config.ML_MIN_RR,
            }
            min_rr = min_rr_map.get(strategy.name, 1.2)
            # 分倉策略（TP1 成交比例）：
            #   MR 0.7：快速反轉、TP2 鮮少觸及，先鎖利
            #   BD/ML/NKF 0.3：DB 證據顯示 9 筆 TP2 貢獻 +486（總盈利 68%），
            #     極端 fat-tail 分佈下保留 70% 跑尾比 50/50 期望值高
            tp1_split_map = {
                "mean_reversion": 0.7,
                "breakdown_short": 0.3,
                "momentum_long":   0.3,
                "naked_k_fib":     0.3,
            }
            tp1_split = tp1_split_map.get(strategy.name, 0.3)
            # 風控計算（傳入 score 供 SL 災區過濾使用）
            pos = risk.calc_position(
                entry     = sig.entry_price,
                stop_loss = sig.stop_loss,
                tp1       = sig.take_profit_1,
                tp2       = sig.take_profit_2,
                min_rr    = min_rr,
                tp1_split_pct = tp1_split,
                signal_score  = sig.score,
            )
            if not pos:
                log.warning(f"[{symbol}] 風控拒絕")
                continue

            # 執行開倉
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
                open_count += 1
                log.info(
                    f"[{symbol}][{strategy.name}] 開倉成功："
                    f"{sig.side} qty={pos['qty']} "
                    f"SL={pos['sl']} TP1={pos['tp1']} TP2={pos['tp2']}"
                )
            else:
                log.error(f"[{symbol}] 開倉失敗")


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
        notify.daily_summary(
            today_pnl=today_pnl,
            total_pnl=stats.get("net_pnl", 0),
            win_rate=stats.get("win_rate", 0),
            open_count=open_count,
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

        notify.positions_report(items)
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
    schedule.every(Config.SIGNAL_CHECK_MIN).minutes.do(check_signals)
    schedule.every(Config.SYNC_SEC).seconds.do(sync_positions)
    schedule.every().day.at("23:55").do(send_daily_summary)
    schedule.every().hour.do(send_positions_report)
    log.info(
        f"排程設定：掃幣={Config.RESCAN_MIN}分 訊號={Config.SIGNAL_CHECK_MIN}分 "
        f"同步={Config.SYNC_SEC}秒 每日總結=23:55 持倉小時報=每 1 小時"
    )


# ── 主程式 ───────────────────────────────────────────────────────
def main():
    global _active_strategy_override

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

    notify.bot_started()
    setup_schedule()

    # 啟動時清理幣安上殘留的孤兒掛單（SL/TP 未被撤銷的殘留）
    try:
        syncer.cleanup_orphan_orders()
        log.info("孤兒掛單清理完成")
    except Exception as e:
        log.warning(f"啟動孤兒清理失敗（不阻斷啟動）: {e}")

    scan_coins()

    if not args.skip_wait:
        wait_for_candle_close()

    if not shutdown_event.is_set():
        check_signals()

    log.info("進入主迴圈（Ctrl+C 可安全退出）")
    while not shutdown_event.is_set():
        schedule.run_pending()
        time.sleep(1)

    notify.bot_stopped()
    log.warning("機器人安全關閉")


if __name__ == "__main__":
    main()

