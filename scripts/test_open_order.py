"""
開單測試腳本 — 直接呼叫 OrderExecutor.open_position()
在 Testnet 上下一張真實測試單，不需等待訊號。

執行方式：
    c:/python312/python.exe scripts/test_open_order.py

可調整下方 TEST_* 參數來測試不同情境。
"""
import os
import sys
import logging
from dotenv import load_dotenv
from binance.client import Client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from state_manager import StateManager
from order_executor import OrderExecutor

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)-10s %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("test_order")

# ── 測試參數（依需求修改）─────────────────────────────────────────
TEST_SYMBOL    = "BTCUSDT"     # 要測試的幣種
TEST_DIRECTION = "LONG"        # LONG 或 SHORT
TEST_LEVERAGE  = 2             # 槓桿倍率

# 價格：先查一下現價，SL/TP 設在合理距離
# 腳本會自動取得現價，這裡只需設定「距離現價的百分比」
SL_DISTANCE_PCT  = 0.005       # 止損：距現價 0.5%（比最小距離大一點）
TP1_DISTANCE_PCT = 0.01        # TP1：距現價 1%
TP2_DISTANCE_PCT = 0.02        # TP2：距現價 2%

# 下單金額（USDT），不走風控計算，直接指定
# BTC 現價約 7 萬，至少要 100 USDT 才能換到 0.001 BTC（最小手數）
NOTIONAL_USDT = 200.0          # 下 200 USDT 名義價值做測試
# ─────────────────────────────────────────────────────────────────


def main():
    testnet = os.getenv("BINANCE_TESTNET", "true") == "true"
    log.info(f"連線模式：{'Testnet' if testnet else '真實環境'}")

    if not testnet:
        ans = input("⚠️  目前是真實環境！確定要繼續嗎？(yes/no): ")
        if ans.strip().lower() not in ("yes", "y"):
            log.info("取消")
            return

    client = Client(
        os.getenv("BINANCE_API_KEY"),
        os.getenv("BINANCE_SECRET"),
        testnet=testnet,
    )

    db       = StateManager()
    executor = OrderExecutor(client, db)

    # ── 取得現價 ────────────────────────────────────────────────
    ticker = client.futures_symbol_ticker(symbol=TEST_SYMBOL)
    price  = float(ticker["price"])
    log.info(f"{TEST_SYMBOL} 現價：{price}")

    # ── 計算 SL / TP ───────────────────────────────────────────
    if TEST_DIRECTION == "LONG":
        sl  = price * (1 - SL_DISTANCE_PCT)
        tp1 = price * (1 + TP1_DISTANCE_PCT)
        tp2 = price * (1 + TP2_DISTANCE_PCT)
    else:  # SHORT
        sl  = price * (1 + SL_DISTANCE_PCT)
        tp1 = price * (1 - TP1_DISTANCE_PCT)
        tp2 = price * (1 - TP2_DISTANCE_PCT)

    # ── 計算數量（50/50 分批止盈）──────────────────────────────
    qty     = round(NOTIONAL_USDT / price, 3)
    qty_tp1 = round(qty * 0.5, 3)
    qty_tp2 = qty - qty_tp1
    margin  = round(qty * price / TEST_LEVERAGE, 2)   # 所需保證金

    log.info(
        f"準備下單：{TEST_DIRECTION} {TEST_SYMBOL}\n"
        f"  數量={qty}  TP1數量={qty_tp1}  TP2數量={qty_tp2}\n"
        f"  入場≈{price:.2f}  SL={sl:.2f}  TP1={tp1:.2f}  TP2={tp2:.2f}\n"
        f"  槓桿={TEST_LEVERAGE}x  保證金≈{margin} USDT"
    )

    if qty <= 0:
        log.error(f"數量計算結果為 0（{NOTIONAL_USDT} USDT / {price} = {NOTIONAL_USDT/price:.6f}），請增加 NOTIONAL_USDT")
        return

    ans = input("\n確認下單？(yes/no): ")
    if ans.strip().lower() not in ("yes", "y"):
        log.info("取消")
        return

    # ── 執行開倉 ───────────────────────────────────────────────
    result = executor.open_position(
        symbol    = TEST_SYMBOL,
        direction = TEST_DIRECTION,
        qty       = qty,
        qty_tp1   = qty_tp1,
        qty_tp2   = qty_tp2,
        entry     = price,
        sl        = sl,
        tp1       = tp1,
        tp2       = tp2,
        leverage  = TEST_LEVERAGE,
        meta      = {
            "fib_level": "test",
            "pattern":   "manual_test",
            "score":     5,
            "timeframe": "1h",
        },
    )

    if result:
        log.info(f"\n✅ 開單成功！回傳資料：\n{result}")
        log.info("\n去 Binance Testnet 網頁確認：")
        log.info("  https://testnet.binancefuture.com/zh-TW/futures/BTCUSDT")
    else:
        log.error("\n❌ 開單失敗，請檢查上方 log 訊息")


if __name__ == "__main__":
    main()
