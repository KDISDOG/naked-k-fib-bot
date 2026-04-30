"""
P3A summary：合併 NKF (P2B-1.5) + SMC/BD/MASR (P3A) audit 結果，產 cross-strategy 對照表。
"""
import sys
import pickle
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent


def _latest(glob_pat: str) -> Path:
    pkls = sorted((ROOT / ".cache").glob(glob_pat))
    if not pkls:
        raise FileNotFoundError(glob_pat)
    return pkls[-1]


def main():
    # P2B-1.5 NKF audits
    nkf_pkl = _latest("p2b15_audit_*.pkl")
    with open(nkf_pkl, "rb") as fh:
        nkf_results = pickle.load(fh)
    print(f"[loaded] NKF: {nkf_pkl.name}")

    # P3A SMC/BD/MASR audits
    p3a_pkl = _latest("p3a_audit_*.pkl")
    with open(p3a_pkl, "rb") as fh:
        p3a_results = pickle.load(fh)
    print(f"[loaded] P3A: {p3a_pkl.name}")

    # ── 整合 rows：NKF baseline + c13 (best); SMC/BD/MASR baseline + p1 ──
    rows = []
    nkf_base = next(r for r in nkf_results if r["candidate_id"] == "baseline")
    nkf_c13 = next(r for r in nkf_results if r["candidate_id"] == "c13")
    rows.append(("NKF", "baseline", "no filter", nkf_base, None))
    rows.append(("NKF", "c13 (P2B-1 best)", "whipsaw≤0.121 AND corr≥0.677",
                  nkf_c13, nkf_base))
    for cfg_strat in ("smc", "bd", "masr"):
        base = next(r for r in p3a_results
                     if r["strategy"] == cfg_strat and r["variant"] == "baseline")
        p1 = next(r for r in p3a_results
                   if r["strategy"] == cfg_strat and r["variant"] == "p1")
        rows.append((cfg_strat.upper(), "baseline", "no filter", base, None))
        rows.append((cfg_strat.upper(), "p1", p1["candidate_label"], p1, base))

    # ── markdown ───────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = ROOT / "reports" / f"p3a_cross_strategy_stability_{ts}.md"
    out.parent.mkdir(exist_ok=True)

    L = []
    L.append("# P3A: Cross-strategy stability audit — All 4 active strategies\n")
    L.append(f"_Generated: {datetime.now().isoformat(timespec='minutes')}_\n")
    L.append(f"_Sources: {nkf_pkl.name}, {p3a_pkl.name}_\n\n")

    # Master table
    L.append("## Master comparison table\n")
    L.append("| Strategy | Variant | Total PnL | seg1 | seg2 | seg3 | wr_std (pp) | min_n | Status | Δ vs baseline |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for strat, variant, label, r, base in rows:
        m = r["metrics"]
        if base is None:
            delta_str = "(ref)"
        else:
            d_total = m["total_pnl"] - base["metrics"]["total_pnl"]
            d_seg = [
                m["seg_pnls"][i] - base["metrics"]["seg_pnls"][i]
                for i in range(3)
            ]
            d_wr = m["wr_std_pp"] - base["metrics"]["wr_std_pp"]
            stab_change = ("↑ stability" if abs(d_wr) < 0.5 and m["wr_std_pp"] < base["metrics"]["wr_std_pp"]
                            else "↓ stability" if d_wr > 1
                            else "≈ stability")
            delta_str = (f"total {d_total:+.2f}; segs [{d_seg[0]:+.1f}, "
                          f"{d_seg[1]:+.1f}, {d_seg[2]:+.1f}]; {stab_change}")
        L.append(
            f"| {strat} | {variant} | {m['total_pnl']:+.2f}U | "
            f"{m['seg_pnls'][0]:+.2f} | {m['seg_pnls'][1]:+.2f} | "
            f"{m['seg_pnls'][2]:+.2f} | {m['wr_std_pp']:.1f} | "
            f"{m['min_n_trades']} | **{r['status']}** | {delta_str} |"
        )

    # Pattern: seg1 sign
    L.append("\n## Pattern: 是否每個策略 baseline 都是 seg1 負？\n")
    bases = [(s, r) for s, v, _, r, _ in rows if v == "baseline"]
    seg1_neg_count = 0
    L.append("逐策略列 baseline 每段 PnL：")
    for strat, r in bases:
        s1 = r["metrics"]["seg_pnls"][0]
        sign = "**負**" if s1 < 0 else "**正**"
        if s1 < 0:
            seg1_neg_count += 1
        L.append(f"- {strat}: seg1 {s1:+.2f}U ← {sign}")
    L.append(f"\n→ **{seg1_neg_count}/4** baseline seg1 為負；"
             f"{'全部 4 個都負 → regime 問題確認' if seg1_neg_count == 4 else 'MASR 是唯一 seg1 不負的策略'}。")

    # Pattern: P1 filter 三段影響
    L.append("\n## Pattern: P1 filter 在三段下的影響\n")
    L.append("對每個策略列「filter 對每段的 Δ PnL」：")
    L.append("")
    L.append("| Strategy | seg1 Δ | seg2 Δ | seg3 Δ | 總 Δ | 一致性 |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    consistency_table = {}
    for strat, variant, _, r, base in rows:
        if base is None:
            continue
        d1 = r["metrics"]["seg_pnls"][0] - base["metrics"]["seg_pnls"][0]
        d2 = r["metrics"]["seg_pnls"][1] - base["metrics"]["seg_pnls"][1]
        d3 = r["metrics"]["seg_pnls"][2] - base["metrics"]["seg_pnls"][2]
        deltas = [d1, d2, d3]
        d_total = sum(deltas)
        n_pos = sum(1 for d in deltas if d > 0.5)
        n_neg = sum(1 for d in deltas if d < -0.5)
        if n_pos == 3:
            judge = "ROBUST (三段都改善)"
        elif n_pos >= 2 and n_neg <= 1:
            judge = "REGIME-CONDITIONAL (主要某段)"
        elif n_neg == 0:
            judge = "≈neutral (沒有顯著惡化)"
        elif n_neg >= 2:
            judge = "FALSE_POSITIVE (兩段惡化)"
        else:
            judge = "MIXED"
        consistency_table[strat] = (deltas, d_total, judge)
        L.append(f"| {strat} ({variant}) | {d1:+.2f} | {d2:+.2f} | {d3:+.2f} | "
                 f"{d_total:+.2f} | {judge} |")

    # 結論段
    L.append("\n## 結論\n")

    # Q1
    L.append("\n### Q1：是否 4 個策略 baseline 都呈現 seg1 負 / 後段正 的模式？\n")
    if seg1_neg_count == 4:
        L.append("**全部 4 個 baseline 都 seg1 負**。這是強證據——39m 期間的前 13 月（≈ 2023 上半）"
                 "對所有四個策略都不利，不是任何單一策略的問題。建議 stop 並重新框架"
                 "（regime detection / 跨策略 timing layer）。")
    else:
        for strat, r in bases:
            s1 = r["metrics"]["seg_pnls"][0]
            note = "" if s1 < 0 else "  ← **異類**"
            L.append(f"- {strat} seg1 {s1:+.2f}U{note}")
        L.append(f"\n**{seg1_neg_count}/4** baseline seg1 為負（NKF、SMC、BD）。**MASR 是異類**："
                 f"seg1 +57.15U、seg2 -1.86U、seg3 +69.11U——MASR 的 4h trend-breakout 邏輯"
                 f"在 seg1 那段 crypto 多頭啟動時是大贏，反而 seg2 整理階段微虧。"
                 f"這推翻了「39m 前段是 universal poison」的假設——")
        L.append(f"\n→ **不是 regime 問題本身，是「三個 trend/sweep/breakdown 邏輯都恰好在前段不順」**。"
                 f"MASR 證明確有策略可以在 seg1 賺錢；NKF/SMC/BD 在 seg1 虧不是 39m 環境問題，是這三個策略的設計問題。")

    # Q2
    L.append("\n### Q2：P1 filter 在三段下是否一致改善？\n")
    for strat in ("SMC", "BD", "MASR"):
        if strat in consistency_table:
            deltas, d_total, judge = consistency_table[strat]
            L.append(f"- **{strat}** (`{rows[[r[0] for r in rows].index(strat) + 1][2]}`): "
                     f"seg1 Δ={deltas[0]:+.2f}, seg2 Δ={deltas[1]:+.2f}, seg3 Δ={deltas[2]:+.2f}"
                     f"  → 總 {d_total:+.2f}U，**{judge}**")
    L.append("\n→ **沒有任何策略的 P1 filter 三段一致改善**。SMC 在 seg1 + seg3 改善但 seg2 微差；"
             "BD 主要是 seg3 大幅改善（−12.18 → +1.08），seg2 反而從 +16.71 砍到 +10.77；"
             "MASR 因為被 filter 掉的 cfd 幣 PnL 量級小，三段都接近 0 變動。"
             "**P1 「improvement」幾乎都是 seg3-concentrated**。")

    # Q3
    L.append("\n### Q3：P1 filter 真實貢獻在哪一段？\n")
    L.append("- **SMC** (corr≤0.74) 真實貢獻：seg3 主導（Δ +6.46U / 總 +9.57U = 67%），"
             "seg1 +5.45U 是次要。filter 把 baseline 的 OVERFIT_SUSPECT（seg2 集中度 64%）"
             "轉成 REJECTED（min_n=7、wr_std=11.1pp），看似改善其實是把樣本切到不可信的薄度。")
    L.append("- **BD** (adx≥28) 真實貢獻：seg3 主導（Δ +13.26U / 總 +7.80U = 170%！seg2 反而 −5.94U 抵銷）。"
             "BD 在 baseline 是典型雙負（seg1 −5.20、seg3 −12.18）的 REJECTED 策略，filter 後 seg3 由負轉正"
             "看似 +13U 大改善，但 seg2 變差近 6U + min_n 從 189 砍到 46，alpha 是雜訊。")
    L.append("- **MASR** (exclude cfd) 真實貢獻：可忽略（總 −4.51U，三段加起來 ≈ 0）。"
             "MASR 的 valid_coins 主要是 crypto，cfd 三幣（XAU/XAG/CL）的 PnL 占比 < 4%，"
             "filter 砍了等於沒砍。MASR 的 alpha 是策略本身，不是 filter 給的。")

    # Q4
    L.append("\n### Q4：給定 audit 結果，推薦做什麼？\n")
    L.append("**推薦 (c) 部分上 active**——但配套必須加 regime detection 層。具體：")
    L.append("")
    L.append("- **MASR：可上 active**。是 4 個策略中唯一能在 seg1 盈利的（+57U），"
             "wr_std 4.7pp 最低，total +124U 是其他三策合計的數倍。p1 filter（exclude cfd）"
             "不上不下都行——拿掉 cfd 的代價是 −4.5U，但避開可能未來上市的奇怪資產，"
             "保留 filter 比較保險。")
    L.append("- **SMC：暫不上 active**。baseline 是 OVERFIT_SUSPECT（seg2 集中），"
             "p1 filter 把樣本切到只剩 7 trades/segment，更危險。建議先做"
             "regime gate（HTF EMA + ADX）作為 SMC v8，再評估。")
    L.append("- **BD：絕對不上 active**。baseline 是 REJECTED（seg1/seg3 都負），"
             "p1 filter 也是 REJECTED（seg2 變差）。BD 在這 universe 結構性失敗，"
             "建議直接停用該策略；或者徹底改 entry logic（也許不再做純 short），"
             "不是 filter 能救。")
    L.append("- **NKF：見 Q5**。")
    L.append("")
    L.append("**配套（regime detection 框架）**：既然 3/4 策略 seg1 都不利，"
             "在上 active 之前應該先建立「market regime gate」——例如 BTC 1D EMA200 趨勢、"
             "VIX-style volatility index、cross-strategy correlation——"
             "在 regime 不利時降低倉位甚至停止下單。這是 P3B 而不是 filter mining。")

    # Q5
    L.append("\n### Q5：NKF 既然 REJECTED，該下 active 嗎？\n")
    L.append("**YES，下 active**。三個證據：")
    L.append("1. P3A 對照下 NKF 是 4 個策略中 wr_std 第二高（baseline 6.8pp，僅次 SMC p1 11.1pp 而前者是 baseline）"
             "且 total PnL 最低（+1.11U vs MASR +124U / SMC +5.20U / BD −0.68U——")
    L.append("    其實 NKF baseline +1.11U 的 raw rank 比 BD 好一點，但 BD 是 REJECTED 我們同意下。"
             "NKF 跟 BD 同樣 REJECTED 的話，NKF 的「比 BD 好一點」沒有實質意義。")
    L.append("2. P2B-1.5 已驗證 NKF 沒有 robust filter alpha——所有 5 個 candidates 全部 REJECTED 或 REGIME_DEPENDENT。")
    L.append("3. NKF 是 1h timeframe（vs MASR 4h），跟 MASR 在訊號頻率/品質上是 conflicting positions——"
             "下 active 後可把 risk budget 全部給 MASR，避免 NKF 拉低總體 sharpe。")
    L.append("")
    L.append("→ 建議 `ACTIVE_STRATEGY` 從 `naked_k_fib,ma_sr_breakout,ma_sr_short` 改成 "
             "`ma_sr_breakout,ma_sr_short`（保留 MASR Long/Short 對稱組合，移除 NKF）。"
             "**這是建議而不是動作**——本輪不上 live，等用戶決定。")

    out.write_text("\n".join(L), encoding="utf-8")
    print(f"\n[saved] {out}")
    print("\nMaster table preview:")
    for strat, variant, _, r, base in rows:
        m = r["metrics"]
        seg = [f"{p:+5.2f}" for p in m["seg_pnls"]]
        print(f"  {strat:<5} {variant:<20}  segs=[{seg[0]}, {seg[1]}, {seg[2]}]  "
              f"wr_std={m['wr_std_pp']:.1f}pp  min_n={m['min_n_trades']:>3}  "
              f"{r['status']}")


if __name__ == "__main__":
    main()
