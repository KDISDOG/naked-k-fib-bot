"""
臨時驗證腳本：對 BTCUSDT 12 個月跑「舊版 vs 新版」NKF 回測，
確認每筆 trade 的 entry_time / entry_price / exit_price 完全一致。

PASS → 可 commit
FAIL → 列出第一個 diverge 的 trade 並 abort
"""
import os
import sys
import time
from pathlib import Path
from binance.client import Client
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

from config import Config
import backtest as bt


def run_old_version(client, symbol, tf, months, max_bars):
    """直接複製原 run_backtest 邏輯（不走 vectorized 路徑）"""
    df_tf = bt.fetch_klines(client, symbol, tf, months)
    df_daily = bt.fetch_klines(client, symbol, "1d", months + 1)
    if len(df_tf) < 100:
        return []

    warmup = 60
    trades = []
    balance = bt.INITIAL_BALANCE
    cooldown_until = -1

    engine = bt.BacktestSignalEngine(
        fib_tol=Config.NKF_FIB_TOL,
        vol_mult=Config.NKF_VOL_RATIO,
        skip_vol_rise=not Config.NKF_VOL_RISING,
        skip_bad_fib=False,
    )

    print(f"  [OLD] 掃描 {len(df_tf) - warmup} 根 K 棒...", end="", flush=True)
    t0 = time.time()
    for i in range(warmup, len(df_tf) - 1):
        if i <= cooldown_until:
            continue
        df_slice = df_tf.iloc[:i + 1].copy().reset_index(drop=True)
        bar_time = df_tf["time"].iloc[i]
        df_d_slice = df_daily[df_daily["time"] <= bar_time].copy().reset_index(drop=True)
        if len(df_d_slice) < 20:
            continue
        try:
            sig = engine.check_on_bar(df_slice, df_d_slice, len(df_slice) - 1, tf)
        except Exception:
            continue
        if not sig or sig.score < bt.MIN_SCORE:
            continue
        pos = bt.calc_position(balance, sig.entry, sig.sl, sig.tp1, sig.tp2)
        if not pos:
            continue
        trade = bt.BtTrade(
            symbol=symbol, direction=sig.direction,
            entry=sig.entry, sl=sig.sl, tp1=sig.tp1, tp2=sig.tp2,
            qty=pos["qty"], qty_tp1=pos["qty_tp1"], qty_tp2=pos["qty_tp2"],
            fib_level=sig.fib_level, pattern=sig.pattern,
            score=sig.score, timeframe=tf,
            open_bar=i, open_time=bar_time,
        )
        df_future = df_tf.iloc[i + 1:].reset_index(drop=True)
        trade = bt.simulate_trade(trade, df_future, max_bars=max_bars)
        if trade.result in ("", "OPEN"):
            continue
        balance += trade.net_pnl
        trades.append(trade)
        if "SL" in trade.result:
            cooldown_until = trade.close_bar + bt.COOLDOWN_BARS
    elapsed = time.time() - t0
    print(f" 找到 {len(trades)} 筆訊號  ({elapsed:.1f}s)")
    return trades, elapsed


def run_new_version(client, symbol, tf, months, max_bars):
    """走 backtest.run_backtest（已是新 vectorized 版本）"""
    t0 = time.time()
    trades = bt.run_backtest(client, symbol, tf, months, max_bars=max_bars)
    elapsed = time.time() - t0
    return trades, elapsed


def main():
    symbol = "BTCUSDT"
    tf = "1h"
    months = 12
    max_bars = 48

    client = Client(os.getenv("BINANCE_API_KEY", ""),
                    os.getenv("BINANCE_SECRET", ""), testnet=False)

    print(f"=== Verify NKF rewrite: {symbol} {tf} {months}m ===\n")

    print("[1/2] OLD version (含 .copy() 反模式)...")
    old_trades, old_t = run_old_version(client, symbol, tf, months, max_bars)
    print()

    print("[2/2] NEW version (vectorized)...")
    new_trades, new_t = run_new_version(client, symbol, tf, months, max_bars)
    print()

    # 比對 trade 數
    if len(old_trades) != len(new_trades):
        print(f"❌ FAIL: trade count differs  old={len(old_trades)}  new={len(new_trades)}")
        sys.exit(1)

    # 逐筆比對 entry_time / entry / exit_price / direction / fib_level / pattern
    fields = ["open_time", "entry", "exit_price", "direction",
              "fib_level", "pattern", "score", "result"]
    for k, (a, b) in enumerate(zip(old_trades, new_trades)):
        for f in fields:
            va = getattr(a, f, None)
            vb = getattr(b, f, None)
            # 允許浮點極小誤差
            if isinstance(va, float) and isinstance(vb, float):
                if abs(va - vb) > 1e-6:
                    print(f"❌ FAIL @ trade #{k}: {f}  old={va}  new={vb}")
                    print(f"  OLD trade: {a}")
                    print(f"  NEW trade: {b}")
                    sys.exit(1)
            elif va != vb:
                print(f"❌ FAIL @ trade #{k}: {f}  old={va!r}  new={vb!r}")
                print(f"  OLD trade: {a}")
                print(f"  NEW trade: {b}")
                sys.exit(1)

    print(f"✅ PASS: {len(old_trades)} trades，每筆 entry_time / entry / exit_price / "
          f"direction / fib_level / pattern / score / result 全部一致")
    print(f"\nTiming:")
    print(f"  OLD:  {old_t:.1f}s")
    print(f"  NEW:  {new_t:.1f}s")
    speedup = old_t / new_t if new_t > 0 else float('inf')
    print(f"  Speedup: {speedup:.1f}×")


if __name__ == "__main__":
    main()
