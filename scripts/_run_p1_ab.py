"""
P1 A/B：feature filter OFF (baseline) vs ON。
4 active 策略 × 10 幣 × 39m，wf_runner n_segments=3。

OFF 全跑 → 改 env → ON 全跑（feature_filter.py 每次呼叫重讀 env，
直接 monkey-patch os.environ 即可）。

結果落點：
  .cache/wf_results/p1_filter_off/<strategy>_<sym>.pkl
  .cache/wf_results/p1_filter_on/<strategy>_<sym>.pkl
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

# 在 import backtest 前先 unset env，避免 .env load 進來干擾
for k in ("BACKTEST_USE_FEATURE_FILTERS", "SMC_BTC_CORR_MAX",
          "BD_MIN_ADX_MED", "MASR_EXCLUDE_ASSET_CLASSES"):
    os.environ.pop(k, None)

from backtest import (
    run_backtest_masr, run_backtest_bd, run_backtest_smc, run_backtest,
)
from wf_runner import run_walk_forward, _segment_metrics

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "1000PEPEUSDT", "SKYAIUSDT", "XAUUSDT", "XAGUSDT", "CLUSDT",
]
MONTHS = 39


def _nkf_wrap(client, symbol, months, debug=False, **kwargs):
    return run_backtest(client, symbol, "1h", months)


_nkf_wrap.__name__ = "run_backtest_nkf_1h"

ACTIVE = {
    "masr": run_backtest_masr,
    "bd":   run_backtest_bd,
    "smc":  run_backtest_smc,
    "nkf":  _nkf_wrap,
}

ROOT = Path(__file__).parent.parent
WF_BASE = ROOT / ".cache" / "wf_results"


def _move_pkl(src_pkl: str, target_dir: Path, dst_name: str) -> str:
    target_dir.mkdir(parents=True, exist_ok=True)
    dst = target_dir / dst_name
    src = Path(src_pkl)
    if src.exists():
        shutil.move(str(src), str(dst))
        return str(dst)
    return src_pkl


def run_phase(phase_name: str, env_overrides: dict, client) -> dict:
    """phase_name: 'p1_filter_off' or 'p1_filter_on'。
    env_overrides: 直接 set os.environ。回傳 {(strat, sym): wf}。
    """
    print(f"\n{'='*78}\n PHASE: {phase_name}\n env: {env_overrides}\n{'='*78}")
    for k in ("BACKTEST_USE_FEATURE_FILTERS", "SMC_BTC_CORR_MAX",
              "BD_MIN_ADX_MED", "MASR_EXCLUDE_ASSET_CLASSES"):
        os.environ.pop(k, None)
    for k, v in env_overrides.items():
        os.environ[k] = str(v)

    target_dir = WF_BASE / phase_name
    results = {}
    for label, fn in ACTIVE.items():
        print(f"\n--- Strategy: {label.upper()} ---")
        for sym in SYMBOLS:
            dst_name = f"{label}_{sym}.pkl"
            dst_path = target_dir / dst_name
            if dst_path.exists():
                with open(dst_path, "rb") as fh:
                    wf = pickle.load(fh)
                tt = [t for s in wf["segments"] for t in s["trades"]]
                m = _segment_metrics(tt)
                print(f"  [{label}][{sym}] SKIP cached n={m['n_trades']} "
                      f"wr={m['win_rate']*100:.1f}% pnl={m['total_pnl']:+.2f}U")
                results[(label, sym)] = wf
                continue

            t0 = time.time()
            try:
                wf = run_walk_forward(
                    fn, client, [sym], MONTHS,
                    n_segments=3,
                    config_label=f"{phase_name}_{label}_{sym}",
                )
            except Exception as e:
                print(f"  [{label}][{sym}] FAILED: {e}")
                continue
            elapsed = time.time() - t0

            tt = [t for s in wf["segments"] for t in s["trades"]]
            m = _segment_metrics(tt)
            new_path = _move_pkl(wf["_pickle_path"], target_dir, dst_name)
            wf["_pickle_path"] = new_path
            results[(label, sym)] = wf
            print(f"  [{label}][{sym}] n={m['n_trades']} wr={m['win_rate']*100:.1f}% "
                  f"pnl={m['total_pnl']:+.2f}U  ({elapsed:.1f}s)")
    return results


if __name__ == "__main__":
    client = Client(os.getenv("BINANCE_API_KEY", ""),
                    os.getenv("BINANCE_SECRET", ""), testnet=False)

    # Phase A：filter OFF
    off_res = run_phase("p1_filter_off", {
        "BACKTEST_USE_FEATURE_FILTERS": "false",
    }, client)

    # Phase B：filter ON（用 default 閾值）
    on_res = run_phase("p1_filter_on", {
        "BACKTEST_USE_FEATURE_FILTERS": "true",
        "SMC_BTC_CORR_MAX": "0.74",
        "BD_MIN_ADX_MED": "28",
        "MASR_EXCLUDE_ASSET_CLASSES": "cfd",
    }, client)

    print("\nEXIT=0")
