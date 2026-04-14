"""
裸K + Fib 幣安合約機器人 v2 — 主程式

改進：
  1. 整合 Position Syncer（倉位同步）
  2. 防重複開倉 + 冷卻期
  3. K 棒收盤對齊排程（整點後才檢查訊號）
  4. Dashboard 可選啟動
  5. Graceful shutdown

執行方式:
  python scripts/bot_main.py
  python scripts/bot_main.py --no-dashboard
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

from coin_screener import CoinScreener
from signal_engine import SignalEngine
from risk_manager import RiskManager
from order_executor import OrderExecutor
from state_manager import StateManager
from position_syncer import PositionSyncer
from market_context import MarketContext

# ── 設定 ───────────────────────────────────────────────────────
load_dotenv()
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)-10s %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("bot")
# 避免第三方套件噪音
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.WARNING)

BINANCE_TESTNET  = os.getenv("BINANCE_TESTNET", "true") == "true"
MAX_POSITIONS    = int(os.getenv("MAX_POSITIONS", 5))
RESCAN_MIN       = int(os.getenv("RESCAN_MIN", 15))
SIGNAL_CHECK_MIN = int(os.getenv("SIGNAL_CHECK_MIN", 5))
SYNC_SEC         = int(os.getenv("SYNC_SEC", 30))
COOLDOWN_BARS    = int(os.getenv("COOLDOWN_BARS", 6))
MIN_SIGNAL_SCORE = int(os.getenv("MIN_SIGNAL_SCORE", 3))

# ── 初始化 ──────────────────────────────────────────────────────
client = Client(
    os.getenv("BINANCE_API_KEY"),
    os.getenv("BINANCE_SECRET"),
    testnet=BINANCE_TESTNET
)
db          = StateManager()
market_ctx  = MarketContext(client)
screener    = CoinScreener(client, market_ctx=market_ctx)
signal_e    = SignalEngine(client, market_ctx=market_ctx)
risk        = RiskManager(client, db, market_ctx=market_ctx)
executor    = OrderExecutor(client, db)
syncer      = PositionSyncer(client, db, executor)

candidate_symbols: list[str] = []
bot_paused = False
shutdown_event = threading.Event()


# ── 選幣任務（每 RESCAN_MIN 分鐘）──────────────────────────────
def scan_coins():
    global candidate_symbols
    log.info("=" * 40)
    log.info("選幣掃描開始")
    try:
        candidate_symbols = screener.scan(top=20, min_score=8)
        log.info(f"候選幣種（{len(candidate_symbols)} 支）：{candidate_symbols}")
    except Exception as e:
        log.error(f"選幣掃描失敗: {e}")


# ── 訊號檢查任務（每 SIGNAL_CHECK_MIN 分鐘）────────────────────
def check_signals():
    global bot_paused
    if bot_paused:
        log.warning("機器人已暫停，跳過訊號檢查")
        return


    open_count = db.count_open_positions()
    if open_count >= MAX_POSITIONS:
        log.info(f"已達最大倉位數 {MAX_POSITIONS}，等待空位")
        return

    for symbol in candidate_symbols:
        if open_count >= MAX_POSITIONS:
            break

        # 防重複開倉
        if db.has_open_position(symbol):
            log.debug(f"[{symbol}] 已有開倉，跳過")
            continue

        # 冷卻期檢查
        if db.in_cooldown(symbol, cooldown_bars=COOLDOWN_BARS):
            log.debug(f"[{symbol}] 冷卻期中，跳過")
            continue

        # 訊號檢查：15m K 棒
        sig = signal_e.check(symbol, timeframe="15m")
        if not sig:
            continue

        # 訊號強度過濾
        if sig.score < MIN_SIGNAL_SCORE:
            log.debug(f"[{symbol}] 訊號強度 {sig.score} < {MIN_SIGNAL_SCORE}，跳過")
            continue

        log.info(
            f"[{symbol}] 訊號確認：{sig.pattern} @ Fib {sig.fib_level} "
            f"方向={sig.direction} 強度={sig.score}"
        )

        # 相關性控管：高相關 + 同方向倉位滿 → 拒絕
        if not risk.can_open_direction(symbol, sig.direction):
            continue

        # 風控計算
        pos = risk.calc_position(
            entry     = sig.entry,
            stop_loss = sig.sl,
            tp1       = sig.tp1,
            tp2       = sig.tp2,
        )
        if not pos:
            log.warning(f"[{symbol}] 風控拒絕")
            continue

        # 執行開倉
        order = executor.open_position(
            symbol    = symbol,
            direction = sig.direction,
            qty       = pos["qty"],
            qty_tp1   = pos["qty_tp1"],
            qty_tp2   = pos["qty_tp2"],
            entry     = sig.entry,
            sl        = pos["sl"],
            tp1       = pos["tp1"],
            tp2       = pos["tp2"],
            leverage  = pos["leverage"],
            meta      = {
                "fib_level": sig.fib_level,
                "pattern":   sig.pattern,
                "score":     sig.score,
                "timeframe": sig.timeframe,
            },
            use_trailing = sig.use_trailing,
            trailing_atr = sig.trailing_atr,
            btc_corr     = sig.btc_corr,
        )
        if order:
            open_count += 1
            log.info(
                f"[{symbol}] 開倉成功：{sig.direction} qty={pos['qty']} "
                f"SL={pos['sl']} TP1={pos['tp1']} TP2={pos['tp2']} "
                f"NetR:R={pos['net_rr']}"
            )
        else:
            log.error(f"[{symbol}] 開倉失敗")


# ── 倉位同步任務（每 SYNC_SEC 秒）──────────────────────────────
def sync_positions():
    try:
        syncer.sync()
    except Exception as e:
        log.error(f"倉位同步失敗: {e}")


# ── K 棒收盤對齊 ────────────────────────────────────────────────
def wait_for_candle_close():
    """
    等待到下一個 15m K 棒收盤之後才開始第一次訊號檢查
    避免在 K 棒進行中判斷形態
    """
    now = datetime.utcnow()
    # 下一個 15 分鐘整點
    minutes_to_next = 15 - (now.minute % 15)
    next_15m = now.replace(second=0, microsecond=0) + timedelta(minutes=minutes_to_next)
    # 加 10 秒 buffer 確保 K 棒資料已更新
    wait_until = next_15m + timedelta(seconds=10)
    wait_sec = (wait_until - now).total_seconds()

    if wait_sec > 0 and wait_sec < 900:
        log.info(
            f"等待 15m K 棒收盤：{wait_sec:.0f} 秒後"
            f"（{wait_until.strftime('%H:%M:%S')} UTC）"
        )
        # 分段 sleep 以便響應 shutdown
        while wait_sec > 0 and not shutdown_event.is_set():
            time.sleep(min(wait_sec, 10))
            wait_sec -= 10


# ── Graceful Shutdown ──────────────────────────────────────────
def handle_shutdown(signum, frame):
    log.warning(f"收到信號 {signum}，準備關閉...")
    shutdown_event.set()


# ── Dashboard 啟動 ─────────────────────────────────────────────
def start_dashboard(port=8089):
    """在背景執行 Dashboard"""
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
    schedule.every(RESCAN_MIN).minutes.do(scan_coins)
    schedule.every(SIGNAL_CHECK_MIN).minutes.do(check_signals)
    schedule.every(SYNC_SEC).seconds.do(sync_positions)
    log.info(
        f"排程設定：掃幣={RESCAN_MIN}分 訊號={SIGNAL_CHECK_MIN}分 "
        f"同步={SYNC_SEC}秒"
    )


# ── 主程式 ───────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="裸K + Fib 合約機器人")
    parser.add_argument("--no-dashboard", action="store_true",
                        help="不啟動 Dashboard")
    parser.add_argument("--port", type=int, default=8089,
                        help="Dashboard 埠號")
    parser.add_argument("--skip-wait", action="store_true",
                        help="跳過等待 K 棒收盤")
    args = parser.parse_args()

    # 註冊信號處理
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    log.info("=" * 60)
    log.info(f"裸K + Fib 機器人 v2 啟動 "
             f"{'[TESTNET]' if BINANCE_TESTNET else '[LIVE]'}")
    log.info(f"設定：MAX_POS={MAX_POSITIONS} RESCAN={RESCAN_MIN}m "
             f"SIGNAL={SIGNAL_CHECK_MIN}m SYNC={SYNC_SEC}s "
             f"COOLDOWN={COOLDOWN_BARS}bars")
    log.info("=" * 60)

    # 啟動 Dashboard
    if not args.no_dashboard:
        start_dashboard(args.port)

    # 設定排程
    setup_schedule()

    # 啟動時先掃幣
    scan_coins()

    # 等待 K 棒收盤
    if not args.skip_wait:
        wait_for_candle_close()

    # 啟動時先檢查一次
    if not shutdown_event.is_set():
        check_signals()

    # 主迴圈
    log.info("進入主迴圈（Ctrl+C 可安全退出）")
    while not shutdown_event.is_set():
        schedule.run_pending()
        time.sleep(1)

    # 關閉
    log.warning("機器人安全關閉")


if __name__ == "__main__":
    main()
