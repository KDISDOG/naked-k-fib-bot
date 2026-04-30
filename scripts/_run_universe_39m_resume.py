"""
Resume 版：跳過 .cache/wf_results/ 已存在的 <strategy>_<sym>_39m.pkl
（讓我們從上次卡住的地方接著跑）
"""
import os
import sys
import time
import shutil
import pickle
from pathlib import Path
from binance.client import Client
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

from backtest import (
    run_backtest_masr, run_backtest_bd, run_backtest_mr,
    run_backtest_smc, run_backtest, run_backtest_granville,
    run_backtest_masr_short,
)
from wf_runner import run_walk_forward, _segment_metrics

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "1000PEPEUSDT", "SKYAIUSDT", "XAUUSDT", "XAGUSDT", "CLUSDT",
]
MONTHS = 39
RESULTS_DIR = Path(__file__).parent.parent / ".cache" / "wf_results"


def _nkf_wrap(client, symbol, months, debug=False, **kwargs):
    return run_backtest(client, symbol, "1h", months)


_nkf_wrap.__name__ = "run_backtest_nkf_1h"

MAIN_STRATEGIES = {
    "masr": run_backtest_masr,
    "bd":   run_backtest_bd,
    "mr":   run_backtest_mr,
    "smc":  run_backtest_smc,
    "nkf":  _nkf_wrap,
}
APPENDIX_STRATEGIES = {
    "granville":  run_backtest_granville,
    "masr_short": run_backtest_masr_short,
}


def _move_pkl(src_pkl: str, dst_name: str) -> str:
    dst = RESULTS_DIR / dst_name
    src = Path(src_pkl)
    if src.exists():
        shutil.move(str(src), str(dst))
        return str(dst)
    return src_pkl


def run_one(label: str, fn, sym: str, prefix: str = "") -> dict:
    dst_name = f"{prefix}{label}_{sym}_39m.pkl"
    dst_path = RESULTS_DIR / dst_name
    if dst_path.exists():
        # 已完成 — 跳過
        try:
            with open(dst_path, "rb") as fh:
                wf = pickle.load(fh)
            all_trades = []
            for s in wf["segments"]:
                all_trades.extend(s["trades"])
            m = _segment_metrics(all_trades)
            print(f"  [{label}][{sym}] SKIP (cached) n={m['n_trades']} "
                  f"wr={m['win_rate']*100:.1f}% pnl={m['total_pnl']:+.2f}U")
            return wf
        except Exception as e:
            print(f"  [{label}][{sym}] cached pkl 損毀：{e}，重跑")

    t0 = time.time()
    try:
        wf = run_walk_forward(
            fn, client, [sym], MONTHS,
            n_segments=3,
            config_label=f"universe39m_{label}_{sym}",
        )
    except Exception as e:
        print(f"  [{label}][{sym}] FAILED: {e}")
        return None
    elapsed = time.time() - t0

    all_trades = []
    for s in wf["segments"]:
        all_trades.extend(s["trades"])
    overall = _segment_metrics(all_trades)
    n = overall["n_trades"]
    wr = overall["win_rate"] * 100
    pnl = overall["total_pnl"]

    new_path = _move_pkl(wf["_pickle_path"], dst_name)
    wf["_pickle_path"] = new_path

    print(f"  [{label}][{sym}] n={n} wr={wr:.1f}% pnl={pnl:+.2f}U  ({elapsed:.1f}s)")
    return wf


if __name__ == "__main__":
    client = Client(os.getenv("BINANCE_API_KEY", ""),
                    os.getenv("BINANCE_SECRET", ""), testnet=False)

    print("\n" + "=" * 78)
    print(" 主分析：5 策略 × 10 幣 × 39 月（resume mode）")
    print("=" * 78)
    main_results: dict = {}
    for label, fn in MAIN_STRATEGIES.items():
        print(f"\n--- Strategy: {label.upper()} ---")
        main_results[label] = {}
        for sym in SYMBOLS:
            wf = run_one(label, fn, sym, prefix="")
            main_results[label][sym] = wf

    print("\n" + "=" * 78)
    print(" 附錄：Granville / MASR Short")
    print("=" * 78)
    appendix_results: dict = {}
    for label, fn in APPENDIX_STRATEGIES.items():
        print(f"\n--- {label.upper()} (附錄) ---")
        appendix_results[label] = {}
        for sym in SYMBOLS:
            wf = run_one(label, fn, sym, prefix="_appendix_")
            appendix_results[label][sym] = wf

    # 主分析總表
    print("\n" + "=" * 78)
    print(" 主分析總表（n_trades / win_rate / total_pnl）")
    print("=" * 78)
    print(f"\n  {'symbol':<14} " + "  ".join(f"{l.upper():^16}"
                                              for l in MAIN_STRATEGIES))
    for sym in SYMBOLS:
        line = f"  {sym:<14}"
        for label in MAIN_STRATEGIES:
            wf = main_results[label].get(sym)
            if wf is None:
                line += f"  {'—':^16}"
            else:
                all_trades = []
                for s in wf["segments"]:
                    all_trades.extend(s["trades"])
                m = _segment_metrics(all_trades)
                line += f"  {m['n_trades']:>3}/{m['win_rate']*100:.0f}%/{m['total_pnl']:+6.1f}".ljust(18)
        print(line)

    print("\nEXIT=0")
