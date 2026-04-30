"""
P2B-1.5：對 P2B-1 NKF ✅ improved candidates + baseline 跑 wf-segment stability audit。

Dedupe 邏輯：兩個 candidate 若在 39m universe 留下完全相同的 coin set，
trade list 必然完全等價（filter 是 entry-time fast skip） → 跑一次即可。
"""
import os
import sys
import json
import pickle
from pathlib import Path
from datetime import datetime
from binance.client import Client
from dotenv import load_dotenv
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

# 進 import 前清 env
for k in ("BACKTEST_USE_FEATURE_FILTERS",
          "NKF_RULES_JSON", "NKF_REQUIRE_ALL",
          "MR_RULES_JSON", "MR_REQUIRE_ALL"):
    os.environ.pop(k, None)

from backtest import run_backtest
from feature_filter import should_skip_for_strategy
from stability_audit import audit_candidate_stability

ROOT = Path(__file__).parent.parent
FEAT_PKL = ROOT / ".cache" / "coin_features_39m.pkl"
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "1000PEPEUSDT", "SKYAIUSDT", "XAUUSDT", "XAGUSDT", "CLUSDT",
]
MONTHS = 39


def _nkf_wrap(client, symbol, months, debug=False, **kwargs):
    return run_backtest(client, symbol, "1h", months)


_nkf_wrap.__name__ = "run_backtest_nkf_1h"


def kept_coins(strategy: str, rules: list[dict], rule_logic: str,
                features_df: pd.DataFrame) -> tuple[str, ...]:
    """模擬 feature_filter 對每個 symbol 的判定，回傳留下的 coin tuple（排序）。
    用 env 注入 → should_skip_for_strategy。
    """
    if not rules:
        return tuple(features_df["symbol"])
    saved = {
        "BACKTEST_USE_FEATURE_FILTERS": os.environ.get("BACKTEST_USE_FEATURE_FILTERS"),
        f"{strategy.upper()}_RULES_JSON": os.environ.get(f"{strategy.upper()}_RULES_JSON"),
        f"{strategy.upper()}_REQUIRE_ALL": os.environ.get(f"{strategy.upper()}_REQUIRE_ALL"),
    }
    try:
        os.environ["BACKTEST_USE_FEATURE_FILTERS"] = "true"
        os.environ[f"{strategy.upper()}_RULES_JSON"] = json.dumps(rules)
        os.environ[f"{strategy.upper()}_REQUIRE_ALL"] = "true" if rule_logic == "AND" else "false"
        kept = []
        for _, row in features_df.iterrows():
            feat = row.to_dict()
            skip, _ = should_skip_for_strategy(strategy, row["symbol"], feat)
            if not skip:
                kept.append(row["symbol"])
        return tuple(sorted(kept))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── P2B-1 NKF ✅ improved candidates（從 reports/validate_nkf_*.md 抓，這裡硬編碼）
NKF_IMPROVED = [
    # (id, total_delta_U, label, rules, logic)
    ("c01", 6.05,
     "adx_med >= 26.468",
     [{"feature": "adx_med", "op": ">=", "threshold": 26.468}], "AND"),
    ("c02", 6.05,
     "range_share <= 0.2265",
     [{"feature": "range_share", "op": "<=", "threshold": 0.2265}], "AND"),
    ("c05", 6.05,
     "adx_med >= 26.48 AND range_share <= 0.225",
     [{"feature": "adx_med", "op": ">=", "threshold": 26.48},
      {"feature": "range_share", "op": "<=", "threshold": 0.225}], "AND"),
    ("c06", 6.46,
     "adx_med >= 26.48 AND whipsaw_idx <= 0.121",
     [{"feature": "adx_med", "op": ">=", "threshold": 26.48},
      {"feature": "whipsaw_idx", "op": "<=", "threshold": 0.121}], "AND"),
    ("c07", 6.46,
     "adx_med >= 26.48 AND gap_freq <= 0.0",
     [{"feature": "adx_med", "op": ">=", "threshold": 26.48},
      {"feature": "gap_freq", "op": "<=", "threshold": 0.0}], "AND"),
    ("c09", 6.46,
     "range_share <= 0.225 AND whipsaw_idx <= 0.121",
     [{"feature": "range_share", "op": "<=", "threshold": 0.225},
      {"feature": "whipsaw_idx", "op": "<=", "threshold": 0.121}], "AND"),
    ("c10", 6.46,
     "range_share <= 0.225 AND gap_freq <= 0.0",
     [{"feature": "range_share", "op": "<=", "threshold": 0.225},
      {"feature": "gap_freq", "op": "<=", "threshold": 0.0}], "AND"),
    ("c12", 6.46,
     "whipsaw_idx <= 0.121 AND volume_quote_med >= 795472542.5",
     [{"feature": "whipsaw_idx", "op": "<=", "threshold": 0.121},
      {"feature": "volume_quote_med", "op": ">=", "threshold": 795472542.5}], "AND"),
    ("c13", 14.78,
     "whipsaw_idx <= 0.121 AND btc_corr_30d >= 0.677",
     [{"feature": "whipsaw_idx", "op": "<=", "threshold": 0.121},
      {"feature": "btc_corr_30d", "op": ">=", "threshold": 0.677}], "AND"),
    ("c15", 7.31,
     "gap_freq <= 0.0 AND btc_corr_30d >= 0.677",
     [{"feature": "gap_freq", "op": "<=", "threshold": 0.0},
      {"feature": "btc_corr_30d", "op": ">=", "threshold": 0.677}], "AND"),
]


def main():
    feats = pd.read_pickle(FEAT_PKL)
    client = Client(os.getenv("BINANCE_API_KEY", ""),
                    os.getenv("BINANCE_SECRET", ""), testnet=False)
    out_dir = ROOT / "reports"
    out_dir.mkdir(exist_ok=True)

    # ── Dedupe by coin-set ─────────────────────────────────
    print("=" * 78)
    print(" Step 1: dedupe candidates by kept coin-set")
    print("=" * 78)
    seen: dict[tuple, str] = {}
    runs: list[dict] = []  # 要實際跑的 (id, label, rules, logic, kept, p2b1_delta, aliases)
    for cid, delta, label, rules, logic in NKF_IMPROVED:
        kept = kept_coins("nkf", rules, logic, feats)
        if kept in seen:
            # alias to existing
            for r in runs:
                if r["kept"] == kept:
                    r["aliases"].append((cid, delta, label))
                    break
            print(f"  {cid} ({delta:+.2f}U) → SAME coins as {seen[kept]}: {len(kept)} kept")
        else:
            seen[kept] = cid
            runs.append({
                "id": cid, "label": label, "rules": rules, "logic": logic,
                "kept": kept, "p2b1_delta": delta,
                "aliases": [],
            })
            print(f"  {cid} ({delta:+.2f}U) → NEW set: {kept}")

    # baseline
    runs.insert(0, {
        "id": "baseline", "label": "no filter (P1 baseline)",
        "rules": [], "logic": "AND",
        "kept": tuple(feats["symbol"]),
        "p2b1_delta": 0.0,
        "aliases": [],
    })

    print(f"\n→ {len(runs) - 1} unique candidate coin-sets + 1 baseline = {len(runs)} runs")

    # ── 跑 audit ────────────────────────────────────────────
    print("\n" + "=" * 78)
    print(f" Step 2: run audit ({len(runs)} runs)")
    print("=" * 78)
    results = []
    for i, r in enumerate(runs, 1):
        print(f"\n[{i}/{len(runs)}] {r['id']}: {r['label']} (kept {len(r['kept'])} coins)")
        res = audit_candidate_stability(
            strategy="nkf",
            candidate_rules=r["rules"],
            rule_logic=r["logic"],
            client=client,
            symbols=SYMBOLS,
            fn=_nkf_wrap,
            months=MONTHS,
            n_segments=3,
            candidate_id=r["id"],
            candidate_label=r["label"],
            output_dir=out_dir,
        )
        res["p2b1_delta"] = r["p2b1_delta"]
        res["aliases"] = r["aliases"]
        res["kept"] = r["kept"]
        results.append(res)
        m = res["metrics"]
        print(f"  → status={res['status']}  segs PnL=[{m['seg_pnls'][0]:+.2f}, "
              f"{m['seg_pnls'][1]:+.2f}, {m['seg_pnls'][2]:+.2f}]  "
              f"wr_std={m['wr_std_pp']:.1f}pp  min_n={m['min_n_trades']}  "
              f"adj={res['stability_adjusted_pnl']:+.2f}U")

    # ── 落 pickle 給 summary 用 ────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_pkl = ROOT / ".cache" / f"p2b15_audit_{ts}.pkl"
    with open(out_pkl, "wb") as fh:
        pickle.dump(results, fh)
    print(f"\n[saved] {out_pkl}")
    print("\nEXIT=0")


if __name__ == "__main__":
    main()
