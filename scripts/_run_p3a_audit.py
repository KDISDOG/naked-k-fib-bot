"""
P3A：對 SMC / BD / MASR 各跑 baseline + P1 filter stability audit。
總 6 runs。klines 已 disk cached → 應該秒級完成。
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

# 進 import 前清 env，避免 .env 干擾
for k in ("BACKTEST_USE_FEATURE_FILTERS",
          "SMC_RULES_JSON", "SMC_REQUIRE_ALL", "SMC_BTC_CORR_MAX",
          "BD_RULES_JSON", "BD_REQUIRE_ALL", "BD_MIN_ADX_MED",
          "MASR_RULES_JSON", "MASR_REQUIRE_ALL", "MASR_EXCLUDE_ASSET_CLASSES"):
    os.environ.pop(k, None)

from backtest import run_backtest_smc, run_backtest_bd, run_backtest_masr
from stability_audit import audit_candidate_stability

ROOT = Path(__file__).parent.parent
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "1000PEPEUSDT", "SKYAIUSDT", "XAUUSDT", "XAGUSDT", "CLUSDT",
]
MONTHS = 39

STRATEGIES_TO_AUDIT = [
    {
        "strategy": "smc",
        "fn": run_backtest_smc,
        "p1_rules": [{"feature": "btc_corr_30d", "op": "<=", "threshold": 0.74}],
        "p1_logic": "AND",
        "p1_label": "btc_corr_30d <= 0.74",
    },
    {
        "strategy": "bd",
        "fn": run_backtest_bd,
        "p1_rules": [{"feature": "adx_med", "op": ">=", "threshold": 28}],
        "p1_logic": "AND",
        "p1_label": "adx_med >= 28",
    },
    {
        "strategy": "masr",
        "fn": run_backtest_masr,
        "p1_rules": [{"feature": "asset_class", "op": "not_in", "threshold": ["cfd"]}],
        "p1_logic": "AND",
        "p1_label": "asset_class not_in [cfd]",
    },
]


def main():
    client = Client(os.getenv("BINANCE_API_KEY", ""),
                    os.getenv("BINANCE_SECRET", ""), testnet=False)
    out_dir = ROOT / "reports"
    out_dir.mkdir(exist_ok=True)

    all_results = []
    total = len(STRATEGIES_TO_AUDIT) * 2
    i = 0
    for cfg in STRATEGIES_TO_AUDIT:
        for variant in ("baseline", "p1"):
            i += 1
            if variant == "baseline":
                rules, logic, label = [], "AND", "no filter"
                cid = f"{cfg['strategy']}_baseline"
            else:
                rules = cfg["p1_rules"]
                logic = cfg["p1_logic"]
                label = cfg["p1_label"]
                cid = f"{cfg['strategy']}_p1"

            print(f"\n[{i}/{total}] {cfg['strategy'].upper()} / {variant}: {label}")
            res = audit_candidate_stability(
                strategy=cfg["strategy"],
                candidate_rules=rules,
                rule_logic=logic,
                client=client,
                symbols=SYMBOLS,
                fn=cfg["fn"],
                months=MONTHS,
                n_segments=3,
                candidate_id=cid,
                candidate_label=label,
                output_dir=out_dir,
            )
            res["variant"] = variant
            all_results.append(res)
            m = res["metrics"]
            print(f"  → status={res['status']}  segs PnL=[{m['seg_pnls'][0]:+.2f}, "
                  f"{m['seg_pnls'][1]:+.2f}, {m['seg_pnls'][2]:+.2f}]  "
                  f"wr_std={m['wr_std_pp']:.1f}pp  min_n={m['min_n_trades']}  "
                  f"adj={res['stability_adjusted_pnl']:+.2f}U")

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_pkl = ROOT / ".cache" / f"p3a_audit_{ts}.pkl"
    with open(out_pkl, "wb") as fh:
        pickle.dump(all_results, fh)
    print(f"\n[saved] {out_pkl}")
    print("\nEXIT=0")


if __name__ == "__main__":
    main()
