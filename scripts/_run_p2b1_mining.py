"""
P2B-1：對 NKF / MR 各做 Direction A (relaxed single-feature) + Direction B (2-feature combo)。

NKF baseline: .cache/wf_results/p1_filter_off/nkf_<sym>.pkl
MR baseline:  .cache/wf_results/mr_<sym>_39m.pkl  (universe39m run)

四個產出：
  reports/pattern_relaxed_nkf_<ts>.md
  reports/pattern_relaxed_mr_<ts>.md
  reports/pattern_2feature_nkf_<ts>.md
  reports/pattern_2feature_mr_<ts>.md
"""
import sys
from pathlib import Path
from datetime import datetime
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from pattern_miner import mine_patterns_relaxed, mine_patterns_2feature

ROOT = Path(__file__).parent.parent
FEAT_PKL = ROOT / ".cache" / "coin_features_39m.pkl"
WF_DIR = ROOT / ".cache" / "wf_results"
NKF_BASELINE = WF_DIR / "p1_filter_off"   # nkf_<sym>.pkl
MR_BASELINE = WF_DIR                       # mr_<sym>_39m.pkl


def main():
    feats = pd.read_pickle(FEAT_PKL)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)

    out = {}

    # ── NKF ──────────────────────────────────────────────────
    print("=" * 78)
    print("Direction A: NKF relaxed single-feature mining")
    print("=" * 78)
    cands_nkf_a = mine_patterns_relaxed(
        feats, NKF_BASELINE, "nkf",
        output_path=reports / f"pattern_relaxed_nkf_{ts}.md",
        pnl_sigma_threshold=1.0, wr_pp_threshold=5.0, use_or_logic=True,
        filename_pattern="{strategy}_{symbol}.pkl",
    )
    out["pattern_relaxed_nkf"] = (reports / f"pattern_relaxed_nkf_{ts}.md", cands_nkf_a)
    print(f"  → {len(cands_nkf_a)} candidates")
    for c in cands_nkf_a:
        if c["rule_type"] == "exclude":
            print(f"    - {c['feature']} not_in {c['threshold']}  ({c['trigger_reason']})")
        else:
            print(f"    - {c['feature']} {c['op']} {c['threshold']:.4f}  ({c['trigger_reason']})")

    print("\n" + "=" * 78)
    print("Direction B: NKF 2-feature combo mining")
    print("=" * 78)
    cands_nkf_b = mine_patterns_2feature(
        feats, NKF_BASELINE, "nkf",
        output_path=reports / f"pattern_2feature_nkf_{ts}.md",
        min_cell_coins=3, min_cell_trades=30,
        filename_pattern="{strategy}_{symbol}.pkl",
    )
    out["pattern_2feature_nkf"] = (reports / f"pattern_2feature_nkf_{ts}.md", cands_nkf_b)
    print(f"  → {len(cands_nkf_b)} 2-feature candidates")
    for c in cands_nkf_b:
        print(f"    - {c['feature_i']}×{c['feature_j']}  best={c['best_quadrant']}  "
              f"n={c['n_trades']}  wr={c['wr']*100:.1f}%  pnl={c['pnl_med']:+.2f}U")

    # ── MR ──────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("Direction A: MR relaxed single-feature mining")
    print("=" * 78)
    cands_mr_a = mine_patterns_relaxed(
        feats, MR_BASELINE, "mr",
        output_path=reports / f"pattern_relaxed_mr_{ts}.md",
        pnl_sigma_threshold=1.0, wr_pp_threshold=5.0, use_or_logic=True,
        filename_pattern="{strategy}_{symbol}_39m.pkl",
    )
    out["pattern_relaxed_mr"] = (reports / f"pattern_relaxed_mr_{ts}.md", cands_mr_a)
    print(f"  → {len(cands_mr_a)} candidates")
    for c in cands_mr_a:
        if c["rule_type"] == "exclude":
            print(f"    - {c['feature']} not_in {c['threshold']}  ({c['trigger_reason']})")
        else:
            print(f"    - {c['feature']} {c['op']} {c['threshold']:.4f}  ({c['trigger_reason']})")

    print("\n" + "=" * 78)
    print("Direction B: MR 2-feature combo mining")
    print("=" * 78)
    cands_mr_b = mine_patterns_2feature(
        feats, MR_BASELINE, "mr",
        output_path=reports / f"pattern_2feature_mr_{ts}.md",
        min_cell_coins=3, min_cell_trades=30,
        filename_pattern="{strategy}_{symbol}_39m.pkl",
    )
    out["pattern_2feature_mr"] = (reports / f"pattern_2feature_mr_{ts}.md", cands_mr_b)
    print(f"  → {len(cands_mr_b)} 2-feature candidates")
    for c in cands_mr_b:
        print(f"    - {c['feature_i']}×{c['feature_j']}  best={c['best_quadrant']}  "
              f"n={c['n_trades']}  wr={c['wr']*100:.1f}%  pnl={c['pnl_med']:+.2f}U")

    # 把 candidates 落成 pickle 給後續 validation 用
    import pickle
    cand_pkl = ROOT / ".cache" / f"p2b1_candidates_{ts}.pkl"
    with open(cand_pkl, "wb") as fh:
        pickle.dump({
            "nkf_relaxed": cands_nkf_a,
            "nkf_2feature": cands_nkf_b,
            "mr_relaxed": cands_mr_a,
            "mr_2feature": cands_mr_b,
            "ts": ts,
        }, fh)
    print(f"\n[saved candidates] {cand_pkl}")
    print(f"\nReports:")
    for k, (p, _) in out.items():
        print(f"  {k}:  {p}")


if __name__ == "__main__":
    main()
