"""
任務 2：5 策略 × 10 幣 × 39m 重跑（用 wf_runner 預設 / n_segments=3）。

主分析：MASR / BD / MR / SMC / NKF
附錄：Granville / MASR Short
結果按 .pkl 存到 .cache/wf_results/<strategy>_<sym>_39m.pkl
（附錄：_appendix_<strategy>_<sym>_39m.pkl）
"""
import os
import sys
import time
import shutil
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
from wf_runner import run_walk_forward

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "1000PEPEUSDT", "SKYAIUSDT", "XAUUSDT", "XAGUSDT", "CLUSDT",
]
MONTHS = 39

# NKF 簽名與其他不同（含 timeframe），包成 lambda 對齊 wf_runner 介面
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
    """把 wf_runner 自動產的 pickle 檔重命名"""
    dst = Path(__file__).parent.parent / ".cache" / "wf_results" / dst_name
    src = Path(src_pkl)
    if src.exists():
        shutil.move(str(src), str(dst))
        return str(dst)
    return src_pkl


def run_one(label: str, fn, sym: str, prefix: str = "") -> dict:
    """跑一個 (strategy, symbol)；回傳 wf result。"""
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

    # 算總 metrics（合併 3 段）
    from wf_runner import _segment_metrics
    all_trades = []
    for s in wf["segments"]:
        all_trades.extend(s["trades"])
    overall = _segment_metrics(all_trades)
    n = overall["n_trades"]
    wr = overall["win_rate"] * 100
    pnl = overall["total_pnl"]

    # 重命名檔案
    dst_name = f"{prefix}{label}_{sym}_39m.pkl"
    new_path = _move_pkl(wf["_pickle_path"], dst_name)
    wf["_pickle_path"] = new_path

    print(f"  [{label}][{sym}] n={n} wr={wr:.1f}% pnl={pnl:+.2f}U  ({elapsed:.1f}s)")
    return wf


if __name__ == "__main__":
    client = Client(os.getenv("BINANCE_API_KEY", ""),
                    os.getenv("BINANCE_SECRET", ""), testnet=False)

    # 主分析
    print("\n" + "=" * 78)
    print(" 主分析：5 策略 × 10 幣 × 39 月")
    print("=" * 78)
    main_results: dict = {}
    for label, fn in MAIN_STRATEGIES.items():
        print(f"\n--- Strategy: {label.upper()} ---")
        main_results[label] = {}
        for sym in SYMBOLS:
            wf = run_one(label, fn, sym, prefix="")
            main_results[label][sym] = wf

    # 附錄
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
    from wf_runner import _segment_metrics
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

    print(f"\n[完成] main pickles 在 .cache/wf_results/<strat>_<sym>_39m.pkl")
    print(f"      appendix pickles 在 .cache/wf_results/_appendix_<strat>_<sym>_39m.pkl")
