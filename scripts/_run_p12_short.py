"""
P12 Task 2：對 MASR_SHORT v1 + v2 (fast / slow) 跑 39m × 10 幣 wf_runner。

純診斷，不改 logic、不啟用 cfd filter（雖然 default 仍會 cfd 排除 — 純測 short
signal 量到底有多少）。

cfd filter 通過 BACKTEST_USE_FEATURE_FILTERS=false 強制關掉，因為這版我們在看
"raw signal 量"。MASR_SHORT_EXCLUDED_SYMBOLS 預設 "PAXGUSDT,XAUUSDT" 是策略
內建排除（與 cfd filter 不同層），會在 v1 內生效；v2 不讀這個 env，所以對 v2
不影響。為了統一，我們把 MASR_SHORT_EXCLUDED_SYMBOLS 也清掉。

結果存 .cache/wf_results/p12_<version>_<sym>.pkl。
"""
import os
import sys
import time
import shutil
import pickle
from pathlib import Path
from datetime import datetime
from binance.client import Client
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

# 進 import 前清 env
for k in ("BACKTEST_USE_FEATURE_FILTERS", "MASR_RULES_JSON",
          "MASR_REQUIRE_ALL", "MASR_EXCLUDE_ASSET_CLASSES",
          "MASR_SHORT_EXCLUDED_SYMBOLS"):
    os.environ.pop(k, None)
os.environ["BACKTEST_USE_FEATURE_FILTERS"] = "false"
os.environ["MASR_SHORT_EXCLUDED_SYMBOLS"] = ""   # 清空，純看 raw 訊號

from backtest import run_backtest_masr_short, run_backtest_masr_short_v2
from wf_runner import run_walk_forward, _segment_metrics

ROOT = Path(__file__).parent.parent
WF_DIR = ROOT / ".cache" / "wf_results"

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "1000PEPEUSDT", "SKYAIUSDT", "XAUUSDT", "XAGUSDT", "CLUSDT",
]
MONTHS = 39


def _v2_fast(client, symbol, months, debug=False, **kw):
    return run_backtest_masr_short_v2(client, symbol, months, debug=debug, variant="fast")


def _v2_slow(client, symbol, months, debug=False, **kw):
    return run_backtest_masr_short_v2(client, symbol, months, debug=debug, variant="slow")


_v2_fast.__name__ = "run_backtest_masr_short_v2_fast"
_v2_slow.__name__ = "run_backtest_masr_short_v2_slow"

VERSIONS = [
    ("masr_short_v1", run_backtest_masr_short),
    ("masr_short_v2_fast", _v2_fast),
    ("masr_short_v2_slow", _v2_slow),
]


def _move_pkl(src_pkl: str, dst_name: str) -> str:
    WF_DIR.mkdir(parents=True, exist_ok=True)
    dst = WF_DIR / dst_name
    src = Path(src_pkl)
    if src.exists():
        shutil.move(str(src), str(dst))
        return str(dst)
    return src_pkl


def main():
    client = Client(os.getenv("BINANCE_API_KEY", ""),
                    os.getenv("BINANCE_SECRET", ""), testnet=False)

    summary = {}  # version -> {sym: metrics, _segments: [...]}
    for label, fn in VERSIONS:
        print(f"\n{'='*78}\n {label}\n{'='*78}")
        sym_metrics = {}
        seg_aggregate = [[], [], []]
        for sym in SYMBOLS:
            dst = WF_DIR / f"p12_{label}_{sym}.pkl"
            if dst.exists():
                with open(dst, "rb") as fh:
                    wf = pickle.load(fh)
                tt = [t for s in wf["segments"] for t in s["trades"]]
                m = _segment_metrics(tt)
                print(f"  [{sym}] SKIP cached n={m['n_trades']}")
            else:
                t0 = time.time()
                try:
                    wf = run_walk_forward(
                        fn, client, [sym], MONTHS, n_segments=3,
                        config_label=f"p12_{label}_{sym}",
                    )
                except Exception as e:
                    print(f"  [{sym}] FAILED: {e}")
                    continue
                # 移到 p12 命名
                _move_pkl(wf["_pickle_path"], f"p12_{label}_{sym}.pkl")
                tt = [t for s in wf["segments"] for t in s["trades"]]
                m = _segment_metrics(tt)
                print(f"  [{sym}] n={m['n_trades']} wr={m['win_rate']*100:.1f}% "
                      f"pnl={m['total_pnl']:+.2f}U  ({time.time()-t0:.1f}s)")

            sym_metrics[sym] = m
            for k_, seg in enumerate(wf["segments"]):
                seg_aggregate[k_].extend(seg["trades"])

        # 跨幣全段聚合
        total_trades = sum(m["n_trades"] for m in sym_metrics.values())
        seg_metrics = [_segment_metrics(s) for s in seg_aggregate]
        all_trades = [t for seg in seg_aggregate for t in seg]
        overall = _segment_metrics(all_trades)
        summary[label] = {
            "by_coin": sym_metrics,
            "segments": seg_metrics,
            "overall": overall,
        }

        print(f"\n  SUMMARY:")
        print(f"    total n_trades = {overall['n_trades']}  "
              f"wr={overall['win_rate']*100:.1f}%  "
              f"pnl={overall['total_pnl']:+.2f}U")
        for k_, sm in enumerate(seg_metrics, 1):
            print(f"    seg{k_}: n={sm['n_trades']:>3}  "
                  f"wr={sm['win_rate']*100:.1f}%  pnl={sm['total_pnl']:+.2f}U")

    # 彙總印一次
    print(f"\n{'='*78}\n MASTER SUMMARY (universe 39m × 10 coins)\n{'='*78}")
    print(f"{'version':<26} {'n_trades':>9} {'wr':>7} {'totalPnL':>10} "
          f"{'seg1':>10} {'seg2':>10} {'seg3':>10}")
    for label, s in summary.items():
        o = s["overall"]
        sg = s["segments"]
        print(f"{label:<26} {o['n_trades']:>9} {o['win_rate']*100:>6.1f}% "
              f"{o['total_pnl']:>+9.2f}U "
              f"n={sg[0]['n_trades']:>3}/p{sg[0]['total_pnl']:>+5.1f} "
              f"n={sg[1]['n_trades']:>3}/p{sg[1]['total_pnl']:>+5.1f} "
              f"n={sg[2]['n_trades']:>3}/p{sg[2]['total_pnl']:>+5.1f}")

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_pkl = ROOT / ".cache" / f"p12_short_summary_{ts}.pkl"
    with open(out_pkl, "wb") as fh:
        pickle.dump(summary, fh)
    print(f"\n[saved] {out_pkl}")
    print("\nEXIT=0")


if __name__ == "__main__":
    main()
