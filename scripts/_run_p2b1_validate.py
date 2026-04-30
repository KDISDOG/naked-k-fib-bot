"""
P2B-1 Task 4：candidate validation

對 P2B-1 mining 找到的 NKF / MR 所有候選，逐個套上去重跑 39m，比對 baseline。

baseline:
  NKF: .cache/wf_results/p1_filter_off/nkf_<sym>.pkl  (P1 baseline, filter off)
  MR:  .cache/wf_results/mr_<sym>_39m.pkl            (universe39m run)

每個候選獨立跑 39m × 10 幣 × 3 segments，輸出對 baseline 的 delta：
  - total_pnl Δ
  - median_pnl_per_coin Δ
  - win_rate Δ
  - n_trades Δ
  - status: improved | quality_only | rejected

執行需要 BINANCE_API_KEY / BINANCE_SECRET（讀 K 線；都已 disk-cached 所以
應該秒完）。
"""
import os
import sys
import time
import json
import pickle
import shutil
from pathlib import Path
from datetime import datetime
from binance.client import Client
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

# 進 import 前清 env，避免本地 .env 干擾
_FORCE_CLEAN = [
    "BACKTEST_USE_FEATURE_FILTERS",
    "SMC_RULES_JSON", "SMC_REQUIRE_ALL", "SMC_BTC_CORR_MAX",
    "BD_RULES_JSON", "BD_REQUIRE_ALL", "BD_MIN_ADX_MED",
    "MASR_RULES_JSON", "MASR_REQUIRE_ALL", "MASR_EXCLUDE_ASSET_CLASSES",
    "NKF_RULES_JSON", "NKF_REQUIRE_ALL",
    "MR_RULES_JSON", "MR_REQUIRE_ALL",
]
for k in _FORCE_CLEAN:
    os.environ.pop(k, None)

from backtest import run_backtest_mr, run_backtest
from wf_runner import run_walk_forward, _segment_metrics

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "1000PEPEUSDT", "SKYAIUSDT", "XAUUSDT", "XAGUSDT", "CLUSDT",
]
MONTHS = 39
ROOT = Path(__file__).parent.parent
WF_DIR = ROOT / ".cache" / "wf_results"
NKF_BASELINE_DIR = WF_DIR / "p1_filter_off"
MR_BASELINE_DIR = WF_DIR  # mr_<sym>_39m.pkl


def _nkf_wrap(client, symbol, months, debug=False, **kwargs):
    return run_backtest(client, symbol, "1h", months)


_nkf_wrap.__name__ = "run_backtest_nkf_1h"


def _aggregate_metrics(trades_or_wf) -> dict:
    """支援 wf dict 或 trade list。"""
    if isinstance(trades_or_wf, dict) and "segments" in trades_or_wf:
        tt = [t for s in trades_or_wf["segments"] for t in s["trades"]]
    else:
        tt = trades_or_wf
    return _segment_metrics(tt)


def _load_baseline(strategy: str) -> dict:
    """load baseline aggregate (per-coin n/wr/pnl)，回傳 {sym: metrics}。"""
    out = {}
    for sym in SYMBOLS:
        if strategy == "nkf":
            pkl = NKF_BASELINE_DIR / f"nkf_{sym}.pkl"
        elif strategy == "mr":
            pkl = MR_BASELINE_DIR / f"mr_{sym}_39m.pkl"
        else:
            raise ValueError(strategy)
        if not pkl.exists():
            continue
        with open(pkl, "rb") as fh:
            wf = pickle.load(fh)
        out[sym] = _aggregate_metrics(wf)
    return out


def _summary_from_per_coin(per_coin: dict) -> dict:
    """輸入 {sym: metrics}，回傳整體聚合。"""
    n_total = 0
    pnl_total = 0.0
    wins = 0.0
    pnl_per_coin = []
    valid_coins = 0
    for sym, m in per_coin.items():
        n = m["n_trades"]
        n_total += n
        pnl_total += m["total_pnl"]
        wins += n * m["win_rate"]
        if n > 0:
            pnl_per_coin.append(m["total_pnl"])
            valid_coins += 1
    wr = wins / n_total if n_total else 0.0
    median_pnl = (sorted(pnl_per_coin)[len(pnl_per_coin) // 2]
                   if pnl_per_coin else 0.0)
    return {
        "n_trades": n_total,
        "win_rate": wr,
        "total_pnl": pnl_total,
        "valid_coins": valid_coins,
        "median_pnl_per_coin": median_pnl,
    }


def _candidate_to_rules(cand: dict) -> tuple[list[dict], bool]:
    """單 candidate → (rules, require_all)。
    direction A 單條 rule；direction B 兩條 AND。
    """
    if "feature" in cand:  # direction A
        if cand["rule_type"] == "exclude":
            return ([{
                "feature": cand["feature"],
                "op": "not_in",
                "threshold": cand["threshold"],
            }], True)
        return ([{
            "feature": cand["feature"],
            "op": cand["op"],
            "threshold": cand["threshold"],
        }], True)
    # direction B
    return ([cand["rule_i"], cand["rule_j"]], True)


def _candidate_label(cand: dict) -> str:
    if "feature" in cand:
        if cand["rule_type"] == "exclude":
            return f"{cand['feature']} not_in {cand['threshold']}"
        thr = cand["threshold"]
        thr_str = f"{thr:.4f}".rstrip("0").rstrip(".") if isinstance(thr, float) else str(thr)
        return f"{cand['feature']} {cand['op']} {thr_str}"
    # 2-feat
    a, b = cand["rule_i"], cand["rule_j"]
    return (f"{a['feature']} {a['op']} {a['threshold']} AND "
            f"{b['feature']} {b['op']} {b['threshold']}")


def _validate_one(strategy: str, cand: dict, fn, client) -> dict:
    """套上 cand 的 rules 跑 39m × 10 幣，回傳 per-coin + summary。"""
    rules, require_all = _candidate_to_rules(cand)
    env_key_rules = f"{strategy.upper()}_RULES_JSON"
    env_key_req = f"{strategy.upper()}_REQUIRE_ALL"

    # 設環境並請使用者每次重讀（feature_filter 每次呼叫重讀 env）
    os.environ["BACKTEST_USE_FEATURE_FILTERS"] = "true"
    os.environ[env_key_rules] = json.dumps(rules)
    os.environ[env_key_req] = "true" if require_all else "false"

    per_coin = {}
    for sym in SYMBOLS:
        try:
            wf = run_walk_forward(
                fn, client, [sym], MONTHS, n_segments=3,
                config_label=f"validate_{strategy}_{sym}_{int(time.time())}",
            )
        except Exception as e:
            print(f"  [{sym}] FAILED: {e}")
            continue
        # 把 pickle 移到一個 throwaway 路徑（避免污染 cache）
        try:
            Path(wf["_pickle_path"]).unlink(missing_ok=True)
        except Exception:
            pass
        per_coin[sym] = _aggregate_metrics(wf)

    # cleanup env
    for k in (env_key_rules, env_key_req):
        os.environ.pop(k, None)

    return per_coin


def _classify(delta_total, delta_n, delta_med):
    """status 判定：
      improved:    total_pnl Δ > 0 且 n_trades 維持有意義 (delta_n >= -某閾值)
      quality_only: median per-coin Δ > 0 但 total Δ <= 0
      rejected:    其他
    """
    if delta_total > 0:
        return "improved"
    if delta_med > 0 and delta_total <= 0:
        return "quality_only"
    return "rejected"


def validate_candidates(strategy: str, candidates: list[dict],
                         fn, client) -> list[dict]:
    if not candidates:
        return []

    print(f"\n--- Loading baseline for {strategy.upper()} ---")
    base_per_coin = _load_baseline(strategy)
    base_summary = _summary_from_per_coin(base_per_coin)
    print(f"  baseline: n={base_summary['n_trades']} "
          f"wr={base_summary['win_rate']*100:.1f}% "
          f"total={base_summary['total_pnl']:+.2f}U "
          f"median/coin={base_summary['median_pnl_per_coin']:+.2f}U "
          f"valid_coins={base_summary['valid_coins']}")

    out = []
    for i, cand in enumerate(candidates, 1):
        label = _candidate_label(cand)
        print(f"\n[{i}/{len(candidates)}] {strategy.upper()} candidate: {label}")
        per_coin = _validate_one(strategy, cand, fn, client)
        summary = _summary_from_per_coin(per_coin)

        delta_total = summary["total_pnl"] - base_summary["total_pnl"]
        delta_n = summary["n_trades"] - base_summary["n_trades"]
        delta_wr_pp = (summary["win_rate"] - base_summary["win_rate"]) * 100
        delta_med = summary["median_pnl_per_coin"] - base_summary["median_pnl_per_coin"]
        status = _classify(delta_total, delta_n, delta_med)

        print(f"  → total {summary['total_pnl']:+.2f}U (Δ{delta_total:+.2f})  "
              f"med/coin {summary['median_pnl_per_coin']:+.2f}U (Δ{delta_med:+.2f})  "
              f"wr {summary['win_rate']*100:.1f}% (Δ{delta_wr_pp:+.1f}pp)  "
              f"n {summary['n_trades']} (Δ{delta_n:+d})  {status}")

        out.append({
            "candidate": cand,
            "label": label,
            "summary": summary,
            "delta_total": delta_total,
            "delta_n": delta_n,
            "delta_wr_pp": delta_wr_pp,
            "delta_med": delta_med,
            "status": status,
        })

    return out


def render_validation_report(strategy: str,
                              base_summary: dict,
                              results: list[dict],
                              output_path: Path) -> Path:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    lines = []
    lines.append(f"# {strategy.upper()} Candidate Validation\n")
    lines.append(f"_Generated: {ts}_\n")
    lines.append(f"_Baseline (filter OFF): n={base_summary['n_trades']}  "
                 f"WR={base_summary['win_rate']*100:.1f}%  "
                 f"total={base_summary['total_pnl']:+.2f}U  "
                 f"median/coin={base_summary['median_pnl_per_coin']:+.2f}U  "
                 f"valid_coins={base_summary['valid_coins']}_\n\n")

    if not results:
        lines.append("\n_(無 candidate)_\n")
    else:
        lines.append("| # | Candidate | Total Δ | Median Δ | WR Δ | n Δ | Status |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for i, r in enumerate(results, 1):
            badge = {
                "improved": "✅ improved",
                "quality_only": "⚠️ quality_only",
                "rejected": "❌ rejected",
            }[r["status"]]
            lines.append(
                f"| {i} | `{r['label']}` | {r['delta_total']:+.2f}U | "
                f"{r['delta_med']:+.2f}U | {r['delta_wr_pp']:+.1f}pp | "
                f"{r['delta_n']:+d} | {badge} |"
            )

        # 推薦規則
        improved = [r for r in results if r["status"] == "improved"]
        improved.sort(key=lambda r: -r["delta_total"])
        lines.append("\n## Recommended rules\n")
        if improved:
            top = improved[0]
            cand = top["candidate"]
            rules, _ = _candidate_to_rules(cand)
            lines.append(f"\n基於上表，最佳 candidate（total Δ {top['delta_total']:+.2f}U）：\n")
            lines.append(f"```")
            lines.append(f"{strategy.upper()}_RULES_JSON='{json.dumps(rules)}'")
            lines.append(f"{strategy.upper()}_REQUIRE_ALL=true")
            lines.append(f"```")
            if len(improved) > 1:
                lines.append(f"\n其他 improved candidates ({len(improved) - 1})：")
                for r in improved[1:]:
                    lines.append(f"- `{r['label']}`  total Δ {r['delta_total']:+.2f}U")
        else:
            lines.append("\n_(沒有 candidate 改善 total PnL — 推薦不動 .env，繼續探索 P2B-2 trade-level)_\n")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def main():
    # 載入最新 candidates
    cand_pkls = sorted((ROOT / ".cache").glob("p2b1_candidates_*.pkl"))
    if not cand_pkls:
        print("[ERROR] 找不到 p2b1_candidates_*.pkl，請先跑 _run_p2b1_mining.py")
        sys.exit(1)
    with open(cand_pkls[-1], "rb") as fh:
        cands = pickle.load(fh)
    print(f"[loaded] {cand_pkls[-1]}")

    client = Client(os.getenv("BINANCE_API_KEY", ""),
                    os.getenv("BINANCE_SECRET", ""), testnet=False)

    # NKF：合併 relaxed + 2feature
    print("\n" + "=" * 78)
    print(" NKF candidate validation")
    print("=" * 78)
    nkf_all = (cands["nkf_relaxed"] or []) + (cands["nkf_2feature"] or [])
    nkf_results = validate_candidates("nkf", nkf_all, _nkf_wrap, client)
    nkf_base = _summary_from_per_coin(_load_baseline("nkf"))

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    nkf_report = ROOT / "reports" / f"validate_nkf_{ts}.md"
    render_validation_report("nkf", nkf_base, nkf_results, nkf_report)
    print(f"\n[saved] {nkf_report}")

    # MR：合併 relaxed + 2feature
    print("\n" + "=" * 78)
    print(" MR candidate validation")
    print("=" * 78)
    mr_all = (cands["mr_relaxed"] or []) + (cands["mr_2feature"] or [])
    mr_results = validate_candidates("mr", mr_all, run_backtest_mr, client)
    mr_base = _summary_from_per_coin(_load_baseline("mr"))

    mr_report = ROOT / "reports" / f"validate_mr_{ts}.md"
    render_validation_report("mr", mr_base, mr_results, mr_report)
    print(f"\n[saved] {mr_report}")

    # 把 results 落 pickle 給 final summary 用
    out_pkl = ROOT / ".cache" / f"p2b1_validation_{ts}.pkl"
    with open(out_pkl, "wb") as fh:
        pickle.dump({
            "nkf_results": nkf_results, "nkf_base": nkf_base,
            "mr_results": mr_results, "mr_base": mr_base,
            "ts": ts,
        }, fh)
    print(f"\n[saved] {out_pkl}")
    print("\nEXIT=0")


if __name__ == "__main__":
    main()
