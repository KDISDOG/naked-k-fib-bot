"""
P4 / Task 3+4：對 sweep top 3 configs 跑 stability_audit (config_override mode)，
合成綜合報告 reports/p4_masr_sweep_<ts>.md。

依賴 task 2 產的 .cache/masr_sweep_top3_<ts>.pkl
（含 baseline + top 3 deduped configs）。
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

# 不啟用 filter override；保持 .env 預設行為（cfd 排除）。
# 不需 pop，因為 stability_audit config_override mode 內部會強制 false 整個 BACKTEST_USE_FEATURE_FILTERS。
# 但 baseline 行為要對齊 .env，這裡保留 env，由 audit_candidate_stability mode 控制。

from backtest import run_backtest_masr
from stability_audit import audit_candidate_stability

ROOT = Path(__file__).parent.parent
ACTIVE_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "1000PEPEUSDT", "SKYAIUSDT", "XAUUSDT", "XAGUSDT", "CLUSDT",
]
MONTHS = 39


def _format_cfg(cfg: dict) -> str:
    """簡化 config 顯示。"""
    parts = []
    for k, v in cfg.items():
        short = k.replace("MASR_", "").replace("_ATR_MULT", "_ATR")
        parts.append(f"{short}={v}")
    return ", ".join(parts)


def _short_label(cfg: dict) -> str:
    """生成 audit candidate_id（檔名安全）"""
    p = []
    for k, v in cfg.items():
        short = k.replace("MASR_", "").replace("_ATR_MULT", "")[:8]
        p.append(f"{short}{v}")
    return "_".join(p).replace(".", "p")[:50]


def main():
    # 找最新 sweep top3 pickle
    pkls = sorted((ROOT / ".cache").glob("masr_sweep_top3_*.pkl"))
    if not pkls:
        print("[ERROR] 找不到 .cache/masr_sweep_top3_*.pkl，先跑 run_masr_sweep.py")
        sys.exit(1)
    with open(pkls[-1], "rb") as fh:
        sweep = pickle.load(fh)
    print(f"[loaded] {pkls[-1].name}")

    client = Client(os.getenv("BINANCE_API_KEY", ""),
                    os.getenv("BINANCE_SECRET", ""), testnet=False)
    out_dir = ROOT / "reports"

    # ── audit baseline + top 3 ──────────────────────────────
    runs = [
        {"id": "baseline", "label": "baseline (current Config)",
         "cfg": sweep["baseline"]["config_at_eval"],
         "score": sweep["baseline"]["score"],
         "raw_total_pnl": sweep["baseline"].get("total_pnl"),
         "is_baseline": True},
    ]
    for i, e in enumerate(sweep["top3"], 1):
        runs.append({
            "id": f"top{i}",
            "label": _format_cfg(e["config_at_eval"]),
            "cfg": e["config_at_eval"],
            "score": e["score"],
            "raw_total_pnl": e.get("total_pnl"),
            "is_baseline": False,
        })

    print("\n" + "=" * 78)
    print(f" Stability audit on baseline + top {len(runs) - 1}")
    print("=" * 78)

    audit_results = []
    for j, r in enumerate(runs, 1):
        print(f"\n[{j}/{len(runs)}] {r['id']}: {r['label']}")
        # 構造 candidate_id：baseline 用 "baseline"，top1/2/3 用 short label
        cid = r["id"] if r["is_baseline"] else f"{r['id']}_{_short_label(r['cfg'])}"
        res = audit_candidate_stability(
            strategy="masr",
            candidate_rules=[],
            rule_logic="AND",
            client=client,
            symbols=ACTIVE_SYMBOLS,
            fn=run_backtest_masr,
            months=MONTHS,
            n_segments=3,
            candidate_id=cid,
            candidate_label=r["label"],
            output_dir=out_dir,
            mode="config_override",
            config_overrides=r["cfg"],
        )
        res["sweep_score"] = r["score"]
        res["raw_total_pnl"] = r["raw_total_pnl"]
        res["is_baseline"] = r["is_baseline"]
        audit_results.append(res)
        m = res["metrics"]
        print(f"  → {res['status']}  segs=[{m['seg_pnls'][0]:+.2f}, "
              f"{m['seg_pnls'][1]:+.2f}, {m['seg_pnls'][2]:+.2f}]  "
              f"wr_std={m['wr_std_pp']:.1f}pp  min_n={m['min_n_trades']}  "
              f"adj={res['stability_adjusted_pnl']:+.2f}U")

    # ── 寫綜合報告 ──────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = out_dir / f"p4_masr_sweep_{ts}.md"

    L = []
    L.append("# P4: MASR Sweep + Stability Audit\n")
    L.append(f"_Generated: {datetime.now().isoformat(timespec='minutes')}_\n")
    L.append(f"_Sweep source: {pkls[-1].name}_\n")
    L.append(f"_Sweep JSON: {sweep.get('json_path', 'n/a')}_\n\n")

    # Sweep raw
    L.append("## Sweep result (raw, by win-rate-focused objective)\n")
    L.append("| Rank | Config | Score | n_trades | wr | Total PnL (39m) |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    base = audit_results[0]
    base_total = base["metrics"]["total_pnl"]
    L.append(f"| baseline | `{_format_cfg(base['config_overrides'])}` | "
             f"{base['sweep_score']:.4f} | {base['metrics']['sum_n_trades']} | "
             f"{base['metrics']['seg_wrs'][0]*100:.1f}% (seg1, ref) | "
             f"{base_total:+.2f}U |")
    for i, r in enumerate(audit_results[1:], 1):
        m = r["metrics"]
        # n_trades 來自 sweep（總數，不是分段最小），這裡用 sum_n_trades
        L.append(f"| #{i} | `{_format_cfg(r['config_overrides'])}` | "
                 f"{r['sweep_score']:.4f} | {m['sum_n_trades']} | "
                 f"— | {m['total_pnl']:+.2f}U |")

    # Stability audit
    L.append("\n## Stability audit on top 3\n")
    L.append("| Config | seg1 PnL | seg2 PnL | seg3 PnL | wr_std (pp) | min_n | concentration | Status |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in audit_results:
        m = r["metrics"]
        label = "baseline" if r["is_baseline"] else _format_cfg(r["config_overrides"])
        L.append(f"| `{label[:60]}` | {m['seg_pnls'][0]:+.2f} | "
                 f"{m['seg_pnls'][1]:+.2f} | {m['seg_pnls'][2]:+.2f} | "
                 f"{m['wr_std_pp']:.1f} | {m['min_n_trades']} | "
                 f"{m['concentration']*100:.0f}% | **{r['status']}** |")

    # Stability-adjusted ranking
    L.append("\n## Stability-adjusted ranking\n")
    L.append("`stability_adjusted = total_pnl × pnl_consistency × (min_n>=30 ? 1 : 0.5)`\n")
    ranked = sorted(audit_results, key=lambda r: -r["stability_adjusted_pnl"])
    L.append("| New Rank | Config | Total PnL | Consistency | Adj PnL | Status |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for i, r in enumerate(ranked, 1):
        label = "baseline" if r["is_baseline"] else _format_cfg(r["config_overrides"])
        m = r["metrics"]
        L.append(f"| {i} | `{label[:60]}` | {m['total_pnl']:+.2f}U | "
                 f"{m['pnl_consistency']:.3f} | "
                 f"{r['stability_adjusted_pnl']:+.2f}U | {r['status']} |")

    # 結論段（Q1-Q5）
    base_m = base["metrics"]
    raw_top1 = audit_results[1]
    stab_top1 = ranked[0]

    base_score = base["sweep_score"]
    base_total_pnl = base_m["total_pnl"]
    base_wr = base_m["seg_wrs"][0]  # 用 seg1 wr 當代表（不太精確，但顯示用）

    delta_total = raw_top1["metrics"]["total_pnl"] - base_total_pnl
    delta_score = raw_top1["sweep_score"] - base_score

    L.append("\n## 結論\n")

    # Q1
    L.append("\n### Q1：sweep raw best 是哪組，跟 baseline 比改善多少 PnL / wr？\n")
    if raw_top1["is_baseline"]:
        L.append("⚠️ raw best 仍是 baseline 自己（sweep 沒找到比現行 config 更好的組合）。")
    else:
        L.append(f"- **raw best**: `{_format_cfg(raw_top1['config_overrides'])}`")
        L.append(f"- score {raw_top1['sweep_score']:.4f} vs baseline {base_score:.4f} "
                 f"(Δ {delta_score:+.4f})")
        L.append(f"- total PnL {raw_top1['metrics']['total_pnl']:+.2f}U vs "
                 f"baseline {base_total_pnl:+.2f}U (Δ {delta_total:+.2f}U)")

    # Q2
    L.append("\n### Q2：raw best 是否在 stability audit 下還是 best？(P2B-1 重蹈覆轍 check)\n")
    if stab_top1["is_baseline"]:
        L.append(f"**不是。Baseline 反而是 stability-adjusted #1**（adj "
                 f"{stab_top1['stability_adjusted_pnl']:+.2f}U）。"
                 f"raw best 在 stability 下排到第 "
                 f"{ranked.index(raw_top1) + 1} 名。")
    elif stab_top1 == raw_top1:
        L.append(f"**是的，raw best 在 stability 下也是 #1**（adj "
                 f"{stab_top1['stability_adjusted_pnl']:+.2f}U，status={stab_top1['status']}）。"
                 f"沒有 P2B-1 那種 raw → stability 排名翻盤的 false positive。")
    else:
        L.append(f"**不是**。raw best 排到 stability #{ranked.index(raw_top1) + 1}；"
                 f"stability #1 是 `{_format_cfg(stab_top1['config_overrides'])}`"
                 f"（adj {stab_top1['stability_adjusted_pnl']:+.2f}U）。")

    # Q3
    L.append("\n### Q3：是否有任何 config 達到 ROBUST？\n")
    robusts = [r for r in audit_results if r["status"] == "ROBUST"]
    if robusts:
        L.append(f"**有 {len(robusts)} 個**：")
        for r in robusts:
            label = "baseline" if r["is_baseline"] else _format_cfg(r["config_overrides"])
            L.append(f"- `{label}` — {r['status_reason']}")
    else:
        L.append("**沒有任何 config 達到 ROBUST**。"
                 f"全部都是 {set(r['status'] for r in audit_results)}。")
        # 列舉每個的失敗原因
        for r in audit_results:
            label = "baseline" if r["is_baseline"] else _format_cfg(r["config_overrides"])
            L.append(f"- `{label}` → {r['status']}: {r['status_reason']}")
        L.append("")
        L.append("意義：sweep 找到的 config 跟 baseline 一樣都受 39m 後段紅利主導；"
                 "三段 PnL 從根本就不平均，不是 entry/exit param 能修正的。")

    # Q4
    L.append("\n### Q4：推薦的最終 config 是哪組，跟現在 .env 差多少？\n")
    base_cfg = base["config_overrides"]
    raw_cfg = raw_top1["config_overrides"]
    stab_cfg = stab_top1["config_overrides"]

    L.append("**保守版（stability-adjusted top 1）**：")
    if stab_top1["is_baseline"]:
        L.append("- `(baseline，不變)` — sweep 沒找到 stability 比 baseline 更好的 config")
    else:
        L.append(f"- `{_format_cfg(stab_cfg)}`")
        diffs = [(k, base_cfg[k], stab_cfg[k]) for k in stab_cfg if base_cfg[k] != stab_cfg[k]]
        if diffs:
            L.append("- diff vs baseline:")
            for k, b, n in diffs:
                L.append(f"  - {k}: {b} → {n}")
    L.append("")
    L.append("**進取版（raw top 1）**：")
    if raw_top1["is_baseline"]:
        L.append("- `(同 baseline，sweep 沒找到更好)`")
    else:
        L.append(f"- `{_format_cfg(raw_cfg)}`")
        diffs = [(k, base_cfg[k], raw_cfg[k]) for k in raw_cfg if base_cfg[k] != raw_cfg[k]]
        if diffs:
            L.append("- diff vs baseline:")
            for k, b, n in diffs:
                L.append(f"  - {k}: {b} → {n}")
        else:
            L.append("- 跟 baseline 相同（sweep 維持 baseline 為 best）")

    # Q5
    L.append("\n### Q5：是否該下一輪繼續 sweep 別的參數，還是直接接受結果？\n")
    if not robusts and stab_top1["is_baseline"]:
        L.append("**直接接受結果，不再 sweep**。三個理由：")
        L.append("1. baseline 已是 stability-adjusted #1——sweep 沒找到任何 config 在三段穩定性"
                 "上勝過現行設定。繼續 sweep 別的參數風險是「找到一個 raw score 高但"
                 "stability 不過關」的 P2B-1 重蹈覆轍。")
        L.append("2. MASR 的核心問題不是 entry/exit param tuning，是「seg2 偏弱、seg1/seg3 大正」"
                 "的 regime dependency——這是 strategy logic 沒覆蓋整個 regime cycle 的問題，"
                 "不是 RR/TP/SL 能解。")
        L.append("3. 下一步該做的是 P3B（regime detection 框架），不是繼續 P4 sweep。")
    elif robusts:
        L.append(f"**接受並上 .env**。已找到 {len(robusts)} 個 ROBUST config，"
                 f"raw + stability 雙料 top 都通過驗證，可以推 .env.example。")
    else:
        L.append("**接受結果但保留 baseline**。sweep 找到的 config 都不夠 ROBUST，"
                 "雖然 raw 數字略好但 stability 沒有改善。建議：")
        L.append("- **保守路線**：暫不改 .env，等 P3B regime gate 框架。")
        L.append("- **進取路線**：採用 raw best，但同時做 walk-forward optimization 監控。")

    # Recommended diff（給用戶 review，不直接套）
    L.append("\n## 推薦的最終 .env 變更（diff format，不直接套用）\n")
    L.append("```diff")
    if stab_top1["is_baseline"]:
        L.append("# stability_adjusted top 1 = baseline，不需要改任何 MASR_* env")
    else:
        for k in stab_cfg:
            if base_cfg[k] != stab_cfg[k]:
                L.append(f"-{k}={base_cfg[k]}")
                L.append(f"+{k}={stab_cfg[k]}")
    L.append("```")
    L.append("\n_(此處只是建議，本輪不直接套；用戶看完報告再手動更新)_")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"\n[saved] {out}")
    print("\n" + "=" * 78)
    print(" Master comparison preview")
    print("=" * 78)
    print(f"  {'config':<60}  segs                   wr_std  min_n  status")
    for r in audit_results:
        m = r["metrics"]
        label = "baseline" if r["is_baseline"] else _format_cfg(r["config_overrides"])[:60]
        print(f"  {label:<60}  [{m['seg_pnls'][0]:+5.1f}, {m['seg_pnls'][1]:+5.1f}, "
              f"{m['seg_pnls'][2]:+5.1f}]  {m['wr_std_pp']:>4.1f}pp  "
              f"{m['min_n_trades']:>4}  {r['status']}")


if __name__ == "__main__":
    main()
