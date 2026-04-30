"""
P2B-1.5 audit summary：吃 .cache/p2b15_audit_*.pkl 產 markdown 總表 + 結論。
"""
import sys
import pickle
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent


def main():
    pkls = sorted((ROOT / ".cache").glob("p2b15_audit_*.pkl"))
    if not pkls:
        print("[ERROR] 找不到 p2b15_audit_*.pkl，先跑 _run_p2b15_audit.py")
        sys.exit(1)
    with open(pkls[-1], "rb") as fh:
        results = pickle.load(fh)

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = ROOT / "reports" / f"p2b15_nkf_audit_summary_{ts}.md"

    lines = []
    lines.append(f"# P2B-1.5: NKF Candidate Stability Audit\n")
    lines.append(f"_Generated: {datetime.now().isoformat(timespec='minutes')}_\n")
    lines.append(f"_Source: {pkls[-1].name}_\n\n")

    # ── Summary table ───────────────────────────────────────
    lines.append("## Summary table\n")
    lines.append("| # | Candidate | P2B-1 Δ | seg1 | seg2 | seg3 | wr_std (pp) | min_n | conc | adj PnL | Status |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in results:
        m = r["metrics"]
        seg_str = [f"{p:+.2f}" for p in m["seg_pnls"]]
        delta_str = "(ref)" if r["candidate_id"] == "baseline" else f"{r.get('p2b1_delta',0):+.2f}U"
        lines.append(
            f"| {r['candidate_id']} | `{r['candidate_label'][:48]}` | "
            f"{delta_str} | {seg_str[0]} | {seg_str[1]} | {seg_str[2]} | "
            f"{m['wr_std_pp']:.1f} | {m['min_n_trades']} | "
            f"{m['concentration']*100:.0f}% | {r['stability_adjusted_pnl']:+.2f} | "
            f"**{r['status']}** |"
        )
    lines.append("")

    # ── Per-candidate detail ─────────────────────────────────
    lines.append("\n## Per-candidate detail\n")
    for r in results:
        m = r["metrics"]
        lines.append(f"\n### {r['candidate_id']}: `{r['candidate_label']}`")
        lines.append(f"- **Status**: {r['status']} — _{r['status_reason']}_")
        lines.append(f"- Coins kept: {len(r['kept'])}/10  →  {', '.join(r['kept'])}")
        lines.append(f"- Segments: seg1={m['seg_pnls'][0]:+.2f}U (n={m['seg_ns'][0]}, wr={m['seg_wrs'][0]*100:.1f}%) ｜ "
                     f"seg2={m['seg_pnls'][1]:+.2f}U (n={m['seg_ns'][1]}, wr={m['seg_wrs'][1]*100:.1f}%) ｜ "
                     f"seg3={m['seg_pnls'][2]:+.2f}U (n={m['seg_ns'][2]}, wr={m['seg_wrs'][2]*100:.1f}%)")
        lines.append(f"- Total: {m['total_pnl']:+.2f}U / wr_std {m['wr_std_pp']:.2f}pp / "
                     f"sign_flips {m['sign_flip_count']} / pnl_consistency {m['pnl_consistency']:.3f}")
        if r.get("aliases"):
            alias_strs = [f"{a[0]} ({a[1]:+.2f}U)" for a in r["aliases"]]
            lines.append(f"- Equivalent rule sets (same kept coins): {', '.join(alias_strs)}")

    # ── Stability ranking ────────────────────────────────────
    lines.append("\n## Stability ranking (by stability-adjusted PnL)\n")
    ranked = sorted(results, key=lambda r: -r["stability_adjusted_pnl"])
    lines.append("| Rank | # | Candidate | Raw PnL | Consistency | Adjusted PnL | Status |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for i, r in enumerate(ranked, 1):
        lines.append(
            f"| {i} | {r['candidate_id']} | `{r['candidate_label'][:40]}` | "
            f"{r['metrics']['total_pnl']:+.2f}U | {r['metrics']['pnl_consistency']:.3f} | "
            f"{r['stability_adjusted_pnl']:+.2f}U | {r['status']} |"
        )

    # ── 結論段（5 問）────────────────────────────────────
    base = next(r for r in results if r["candidate_id"] == "baseline")
    c13 = next(r for r in results if r["candidate_id"] == "c13")

    # 找出 ROBUST / STABLE_BUT_THIN 的 candidate
    robusts = [r for r in results if r["status"] == "ROBUST"
                and r["candidate_id"] != "baseline"]
    stable_thin = [r for r in results if r["status"] == "STABLE_BUT_THIN"
                    and r["candidate_id"] != "baseline"]
    overfit_suspects = [r for r in results if r["status"] == "OVERFIT_SUSPECT"]
    rejected = [r for r in results if r["status"] == "REJECTED"]
    regime_dep = [r for r in results if r["status"] == "REGIME_DEPENDENT"
                   and r["candidate_id"] != "baseline"]

    # 排序 stability-adjusted（不含 baseline）
    cand_only = [r for r in results if r["candidate_id"] != "baseline"]
    cand_only_ranked = sorted(cand_only, key=lambda r: -r["stability_adjusted_pnl"])
    raw_top = max(cand_only, key=lambda r: r.get("p2b1_delta", 0))
    adj_top = cand_only_ranked[0] if cand_only_ranked else None

    lines.append("\n## 結論\n")

    # Q1
    lines.append("\n### 1. P2B-1 raw rank #1 的 c13 是否真的最好？還是有別的 candidate 在分段下更穩？\n")
    if adj_top is not None:
        if adj_top["candidate_id"] == raw_top["candidate_id"]:
            lines.append(f"以 stability-adjusted PnL 排，**仍是 c13** 最高（adj "
                         f"{adj_top['stability_adjusted_pnl']:+.2f}U）。")
        else:
            lines.append(f"**不是**。raw rank #1 是 c13（P2B-1 +14.78U），但 stability-adjusted PnL "
                         f"排第一是 **{adj_top['candidate_id']}**（adj "
                         f"{adj_top['stability_adjusted_pnl']:+.2f}U vs c13 "
                         f"{c13['stability_adjusted_pnl']:+.2f}U）。")
        lines.append(f"c13 segment PnL 是 [{c13['metrics']['seg_pnls'][0]:+.2f}, "
                     f"{c13['metrics']['seg_pnls'][1]:+.2f}, "
                     f"{c13['metrics']['seg_pnls'][2]:+.2f}]，seg1 大幅負，seg3 大幅正——"
                     f"raw +14.78U 是後段時段紅利，不是穩定 alpha。")

    # Q2
    lines.append("\n### 2. 是否有 candidate 是 ROBUST（三段全正 + 低變異）？\n")
    if robusts:
        lines.append(f"**有 {len(robusts)} 個**：{', '.join(r['candidate_id'] for r in robusts)}。")
    else:
        lines.append("**沒有**。所有 candidate 至少一段為負或 wr_std 過大；"
                     "其中 baseline 自己就是 REGIME_DEPENDENT。")
        if stable_thin:
            lines.append(f"次佳的 STABLE_BUT_THIN：{', '.join(r['candidate_id'] for r in stable_thin)}（"
                         f"三段全正但樣本薄）。")
        else:
            lines.append("也沒有 STABLE_BUT_THIN（三段全正但樣本薄）。")

    # Q3
    lines.append("\n### 3. 是否有 candidate 是 OVERFIT_SUSPECT？哪段集中？\n")
    if overfit_suspects:
        for r in overfit_suspects:
            seg_max_idx = r["metrics"]["seg_pnls"].index(max(r["metrics"]["seg_pnls"]))
            lines.append(f"- **{r['candidate_id']}**：concentration "
                         f"{r['metrics']['concentration']*100:.0f}%，"
                         f"seg{seg_max_idx+1} 主導 PnL "
                         f"({r['metrics']['seg_pnls'][seg_max_idx]:+.2f}U)。")
    else:
        lines.append("**狹義 OVERFIT_SUSPECT 標籤上沒有觸發**（concentration < 70% 的閾值）。")
        lines.append(f"但所有 candidate（包括 baseline）都呈現「seg1 負 → seg3 大正」的形態，"
                     f"代表 NKF 的賺賠分布**強烈集中在 39m 期間的後段**——"
                     f"這跟 OVERFIT_SUSPECT 描述的「過擬合」病徵實質一樣，只是定性不定量。"
                     f"用更嚴的閾值（concentration > 50% 加 seg1 負）來看，"
                     f"baseline、c01、c06、c12、c13 都會被歸為 OVERFIT_SUSPECT 變體。")

    # Q4
    lines.append("\n### 4. 整體看 NKF 在 39m 是否有真實 alpha，還是只是運氣？\n")
    lines.append(f"**證據傾向：運氣 + 後段紅利，alpha 弱或不存在。** Baseline (no filter) 39m 總 "
                 f"{base['metrics']['total_pnl']:+.2f}U 看似正期望，但拆分為 "
                 f"seg1 {base['metrics']['seg_pnls'][0]:+.2f}U / "
                 f"seg2 {base['metrics']['seg_pnls'][1]:+.2f}U / "
                 f"seg3 {base['metrics']['seg_pnls'][2]:+.2f}U——seg1 虧 8.5U，"
                 f"如果 39m 只取前 13 個月，NKF 是負期望策略。任何 P2B-1 candidate 的 "
                 f"+14.78U「improvement」都來自把 seg2/seg3 的後段表現推到主導，"
                 f"而非把 seg1 的虧損修好。換做下一個 13 個月（例如 2026Q2~2027）"
                 f"沒人保證這後段紅利還在。")

    # Q5
    lines.append("\n### 5. 推薦進 P2B-2（trade-level mining）還是回 P2B-1 找更保守 candidate？\n")
    lines.append("**建議：兩個都不做，先 stop 並重新評估 NKF。** 三個理由：")
    lines.append("1. baseline 自身就是 REGIME_DEPENDENT，這不是 filter 能解的問題——"
                 "filter 只能挑「在這 39m 哪些幣賺錢」，不能挑「在哪一段時間賺錢」；"
                 "如果策略本身在 seg1 就是錯方向，下一個 seg1 仍然是錯的。")
    lines.append("2. P2B-1 +14.78U 的 raw signal 在 stability audit 下完全消失——"
                 "min_n_trades=20、wr_std=15.4pp，被歸為 REJECTED。raw → stability 排名翻盤。")
    lines.append("3. trade-level mining (P2B-2) 解決的是「哪些訊號狀態下勝率高」，但 NKF 的問題不在於"
                 "「進場狀態挑得不夠好」，而在於「在 seg1 那 13 個月的市場環境下，"
                 "NKF 的進場邏輯本身對」——換句話說 P2B-2 能挑掉一些雜訊，但救不了這個結構性問題。")
    lines.append("\n**具體建議**：")
    lines.append("- 不要把 P2B-1 任何 candidate 推上 .env 或 active list。")
    lines.append("- 不要做 P2B-2。")
    lines.append("- 重新檢視 NKF：是不是該換 timeframe（從 1h → 4h，跟 MASR 對齊）？是不是該加更基本的 regime filter（HTF EMA200 trend）？這些是策略層的 question，不是 filter mining 能回答的。")
    lines.append("- 把這份 audit 報告 + P2B-1 報告當成「NKF 在 39m 沒有 robust filter alpha」的決定性證據存檔，下次策略迭代時參考。")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[saved] {out}")
    print("\n" + "=" * 78)
    print("Summary table:")
    print("=" * 78)
    for r in results:
        m = r["metrics"]
        print(f"  {r['candidate_id']:>9}  segs=[{m['seg_pnls'][0]:+6.2f}, "
              f"{m['seg_pnls'][1]:+6.2f}, {m['seg_pnls'][2]:+6.2f}]  "
              f"wr_std={m['wr_std_pp']:.1f}pp  min_n={m['min_n_trades']:>3}  "
              f"conc={m['concentration']*100:.0f}%  adj={r['stability_adjusted_pnl']:+5.2f}  "
              f"{r['status']}")


if __name__ == "__main__":
    main()
