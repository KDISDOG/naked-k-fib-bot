"""
P12C Task 2：對 sweep top 3 (slow + fast) + 兩個 baseline 跑 stability_audit。

每個 audit 用 mode="config_override"，傳 config_overrides 到 wf_runner，
透過 ConfigPatch 注入。run_backtest_masr_short_v2 沒接 config_overrides
所以走 ConfigPatch path（拿 wrapper fn 固定 variant）。

8 個 audit:
  - slow baseline (mode=config_override, overrides=baseline)
  - slow top1, top2, top3
  - fast baseline
  - fast top1, top2, top3
"""
import os
import sys
import json
import pickle
from pathlib import Path
from datetime import datetime
from binance.client import Client
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

# 進 import 前清 env
for k in ("BACKTEST_USE_FEATURE_FILTERS", "MASR_SHORT_EXCLUDED_SYMBOLS"):
    os.environ.pop(k, None)
os.environ["BACKTEST_USE_FEATURE_FILTERS"] = "false"
os.environ["MASR_SHORT_EXCLUDED_SYMBOLS"] = ""

from backtest import run_backtest_masr_short_v2
from stability_audit import audit_candidate_stability

ROOT = Path(__file__).parent.parent
ACTIVE_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "1000PEPEUSDT", "SKYAIUSDT", "XAUUSDT", "XAGUSDT", "CLUSDT",
]
MONTHS = 39


def _make_v2_wrapper(variant: str):
    def wrapper(client, symbol, months, debug=False, **kw):
        return run_backtest_masr_short_v2(
            client, symbol, months, debug=debug, variant=variant,
        )
    wrapper.__name__ = f"run_backtest_masr_short_v2_{variant}"
    return wrapper


def _format_cfg(cfg: dict) -> str:
    parts = []
    for k, v in cfg.items():
        short = k.replace("MASR_SHORT_", "").replace("_ATR_MULT", "_ATR")
        parts.append(f"{short}={v}")
    return ", ".join(parts)


def _short_label(cfg: dict, variant: str) -> str:
    p = [variant]
    for k, v in cfg.items():
        short = k.replace("MASR_SHORT_", "").replace("_ATR_MULT", "")[:10]
        p.append(f"{short}{v}")
    return "_".join(p).replace(".", "p")[:60]


def main():
    pkls = sorted((ROOT / ".cache").glob("masr_short_sweep_top_*.pkl"))
    if not pkls:
        print("[ERROR] 找不到 masr_short_sweep_top_*.pkl，先跑 run_masr_short_sweep.py")
        sys.exit(1)
    with open(pkls[-1], "rb") as fh:
        sweep = pickle.load(fh)
    print(f"[loaded] {pkls[-1].name}")

    client = Client(os.getenv("BINANCE_API_KEY", ""),
                    os.getenv("BINANCE_SECRET", ""), testnet=False)
    out_dir = ROOT / "reports"

    # 8 audits: 2 baselines + 6 top configs
    audits_to_run = []
    for variant in ("slow", "fast"):
        baseline_entry = sweep["tops_per_variant"][f"{variant}_baseline"]
        audits_to_run.append({
            "variant": variant,
            "rank": "baseline",
            "cfg": baseline_entry["config"],
            "raw_score": baseline_entry["score"],
            "raw_total_pnl": baseline_entry["total_pnl"],
            "raw_n": baseline_entry["n_trades"],
            "raw_wr": baseline_entry["win_rate"],
        })
        for i, e in enumerate(sweep["tops_per_variant"][variant], 1):
            audits_to_run.append({
                "variant": variant,
                "rank": f"top{i}",
                "cfg": e["config"],
                "raw_score": e["score"],
                "raw_total_pnl": e["total_pnl"],
                "raw_n": e["n_trades"],
                "raw_wr": e["win_rate"],
            })

    print(f"\n{'='*78}\n {len(audits_to_run)} audits to run\n{'='*78}")
    audit_results = []
    for i, a in enumerate(audits_to_run, 1):
        print(f"\n[{i}/{len(audits_to_run)}] {a['variant']}.{a['rank']}: "
              f"{_format_cfg(a['cfg'])}")
        cid = f"v2{a['variant']}_{a['rank']}_{_short_label(a['cfg'], a['variant'])}"
        fn = _make_v2_wrapper(a["variant"])
        res = audit_candidate_stability(
            strategy=f"masr_short_v2_{a['variant']}",
            candidate_rules=[],
            rule_logic="AND",
            client=client,
            symbols=ACTIVE_SYMBOLS,
            fn=fn,
            months=MONTHS,
            n_segments=3,
            candidate_id=cid,
            candidate_label=f"{a['variant']} {a['rank']}: {_format_cfg(a['cfg'])}",
            output_dir=out_dir,
            mode="config_override",
            config_overrides=a["cfg"],
        )
        res["sweep_meta"] = a
        audit_results.append(res)
        m = res["metrics"]
        print(f"  → {res['status']}  segs=[{m['seg_pnls'][0]:+.2f}, "
              f"{m['seg_pnls'][1]:+.2f}, {m['seg_pnls'][2]:+.2f}]  "
              f"wr_std={m['wr_std_pp']:.1f}pp  min_n={m['min_n_trades']}  "
              f"adj={res['stability_adjusted_pnl']:+.2f}U")

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_pkl = ROOT / ".cache" / f"p12c_audit_{ts}.pkl"
    with open(out_pkl, "wb") as fh:
        pickle.dump({
            "ts": ts,
            "results": audit_results,
            "sweep_pkl": str(pkls[-1]),
        }, fh)
    print(f"\n[saved] {out_pkl}")
    print("\nEXIT=0")


if __name__ == "__main__":
    main()
