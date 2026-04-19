"""
cleanup_orphans.py — 一次性孤兒單清理工具

用法：
  python scripts/cleanup_orphans.py           # dry-run：只列出不取消
  python scripts/cleanup_orphans.py --apply   # 實際取消

判定「孤兒單」條件（需全部成立）：
  1. 幣安上該 symbol 有 open order
  2. 幣安上該 symbol 實際持倉為 0
  3. DB 中該 symbol 沒有 open / partial 的 trade

跑機器人前或看到殘單時可手動執行。
"""
import sys
import logging
from binance.client import Client

from config import Config
from state_manager import StateManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("cleanup")


def main():
    apply = "--apply" in sys.argv

    client = Client(
        Config.BINANCE_API_KEY,
        Config.BINANCE_SECRET,
        testnet=Config.BINANCE_TESTNET,
    )
    db = StateManager(Config.DB_PATH)

    log.info(
        f"連線：{'TESTNET' if Config.BINANCE_TESTNET else 'MAINNET'} / "
        f"模式：{'APPLY (實際取消)' if apply else 'DRY-RUN (只顯示)'}"
    )

    # 1. 取所有未成交掛單
    try:
        all_open = client.futures_get_open_orders()
    except Exception as e:
        log.error(f"取得全站掛單失敗: {e}")
        return 1

    if not all_open:
        log.info("幣安上沒有任何掛單")
        return 0

    # 2. 取所有持倉
    try:
        positions = client.futures_position_information()
        has_pos = {
            p["symbol"] for p in positions
            if abs(float(p.get("positionAmt", 0))) > 0
        }
    except Exception as e:
        log.error(f"取得持倉資訊失敗: {e}")
        return 1

    # 3. DB 中 open/partial 的 symbols
    open_trades = db.get_open_trades()
    db_symbols = {t["symbol"] for t in open_trades}

    log.info(
        f"幣安掛單 {len(all_open)} 筆，持倉 symbols={len(has_pos)}，"
        f"DB open trades symbols={len(db_symbols)}"
    )

    # 4. 按 symbol 分組
    orders_by_symbol: dict[str, list] = {}
    for o in all_open:
        orders_by_symbol.setdefault(o["symbol"], []).append(o)

    orphan_symbols = []
    for sym, orders in orders_by_symbol.items():
        if sym in has_pos:
            continue
        if sym in db_symbols:
            continue
        orphan_symbols.append((sym, orders))

    if not orphan_symbols:
        log.info("沒有發現孤兒單 ✓")
        return 0

    log.warning(f"發現 {len(orphan_symbols)} 個 symbol 有孤兒單：")
    total_cnt = 0
    for sym, orders in orphan_symbols:
        log.warning(f"  [{sym}] {len(orders)} 筆：")
        for o in orders:
            log.warning(
                f"    - {o['type']} {o['side']} "
                f"stop={o.get('stopPrice', 'N/A')} "
                f"qty={o.get('origQty', 'N/A')} "
                f"orderId={o['orderId']}"
            )
        total_cnt += len(orders)

    log.warning(f"合計孤兒單 {total_cnt} 筆")

    if not apply:
        log.info("DRY-RUN 模式結束。加 --apply 實際取消。")
        return 0

    # 5. 實際取消
    cleaned = 0
    for sym, orders in orphan_symbols:
        try:
            client.futures_cancel_all_open_orders(symbol=sym)
            cleaned += len(orders)
            log.info(f"[{sym}] 已取消 {len(orders)} 筆")
        except Exception as e:
            log.error(f"[{sym}] 取消失敗: {e}")

    log.info(f"完成：實際清除 {cleaned}/{total_cnt} 筆孤兒單")
    return 0


if __name__ == "__main__":
    sys.exit(main())
