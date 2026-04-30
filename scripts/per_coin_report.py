"""
per_coin_report.py — Per-coin × per-segment 穩定性報表

吃 wf_runner.run_walk_forward 輸出的 pickle，產：
  - 每幣 × 每段 metrics 表
  - 自動 stability tag：consistent_winner / consistent_loser / unstable
  - Universe 建議（移除 / 重點關注）
  - 寫 CSV 到 output_path（可選）
"""
import os
import sys
import pickle
import argparse
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))


def _classify_symbol(seg_metrics: list[dict]) -> tuple[str, str]:
    """
    回傳 (tag, reason)。
      consistent_winner : 全段 PnL 都 > 0
      consistent_loser  : 全段 PnL 都 < 0（且每段樣本 ≥ 5）
      unstable          : PnL 正負交替 / 變動 > 50% range
      neutral           : 不滿足以上（樣本太少 / 平均接近 0）
    """
    pnls = [m.get("total_pnl", 0.0) for m in seg_metrics]
    n_trades = [m.get("n_trades", 0) for m in seg_metrics]

    if all(p > 0 for p in pnls) and all(n >= 1 for n in n_trades):
        return "consistent_winner", "全段 PnL > 0"
    if all(p < 0 for p in pnls) and all(n >= 5 for n in n_trades):
        return "consistent_loser", f"全段 PnL < 0, n=" + "+".join(str(n) for n in n_trades)
    # unstable：正負交替（有正有負）
    has_pos = any(p > 0 for p in pnls)
    has_neg = any(p < 0 for p in pnls)
    if has_pos and has_neg:
        return "unstable", "正負交替"
    # 否則：樣本太少或全段近 0
    return "neutral", "樣本不足或邊際"


def _print_table(df: pd.DataFrame, max_col_width: int = 16) -> None:
    """簡易 markdown table（無 tabulate dep）"""
    cols = list(df.columns)
    # widths
    widths = []
    for c in cols:
        w = max(len(str(c)),
                *(len(str(v)) for v in df[c].astype(str).tolist()))
        widths.append(min(w, max_col_width))

    def fmt_row(vals):
        return "  ".join(str(v)[:widths[i]].ljust(widths[i])
                          for i, v in enumerate(vals))

    print(fmt_row(cols))
    print("  ".join("-" * w for w in widths))
    for _, row in df.iterrows():
        print(fmt_row(row.tolist()))


def generate_per_coin_report(
    wf_result_path: str,
    output_path: Optional[str] = None,
) -> pd.DataFrame:
    """
    讀 pickle，輸出 DataFrame：
      symbol / segment / n_trades / win_rate / avg_rr /
      total_pnl / give_back_p50
    + 各幣 stability tag + universe 建議。
    """
    with open(wf_result_path, "rb") as fp:
        wf = pickle.load(fp)

    fn_name = wf.get("fn_name", "?")
    config_label = wf.get("config_label", "?")
    by_coin: dict[str, list[dict]] = wf["by_coin"]
    n_segments = wf["n_segments"]
    seg_labels = ["IS"] + [f"OOS{i}" for i in range(1, n_segments)]

    # 展平表
    rows = []
    for sym, segs in by_coin.items():
        for k, m in enumerate(segs):
            rows.append({
                "symbol": sym,
                "segment": seg_labels[k],
                "n_trades": m.get("n_trades", 0),
                "win_rate": m.get("win_rate", 0.0),
                "avg_rr": m.get("avg_rr"),
                "total_pnl": round(m.get("total_pnl", 0.0), 2),
                "give_back_p50": m.get("median_give_back"),
            })
    df = pd.DataFrame(rows)

    # 分類每幣
    classification = {}
    for sym, segs in by_coin.items():
        tag, reason = _classify_symbol(segs)
        classification[sym] = {"tag": tag, "reason": reason}

    df["stability"] = df["symbol"].map(lambda s: classification[s]["tag"])

    # 印報表
    print(f"\n=== Per-coin × Per-segment Report ===")
    print(f"fn: {fn_name}  config: {config_label}  segments: {n_segments}")
    print(f"symbols: {len(by_coin)}\n")
    _print_table(df)

    # Universe 建議
    losers = [(s, classification[s]["reason"])
               for s in by_coin if classification[s]["tag"] == "consistent_loser"]
    winners = [(s, classification[s]["reason"])
                for s in by_coin if classification[s]["tag"] == "consistent_winner"]
    unstable = [s for s in by_coin if classification[s]["tag"] == "unstable"]

    print("\n建議從該策略 universe 移除（consistent_loser 且樣本夠）：")
    if losers:
        for s, r in losers:
            print(f"  - {s} ({r})")
    else:
        print("  （無）")

    print("\n建議重點關注（consistent_winner）：")
    if winners:
        for s, _ in winners:
            print(f"  - {s}")
    else:
        print("  （無）")

    if unstable:
        print(f"\nUnstable（{len(unstable)} 支）：{', '.join(unstable)}")

    # 寫 CSV
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False, encoding="utf-8-sig")
        print(f"\nCSV 寫入：{output_path}")

    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pickle_path", help="wf_runner 輸出的 .pkl")
    parser.add_argument("--csv", default=None,
                        help="輸出 CSV 路徑（預設不寫）")
    args = parser.parse_args()
    generate_per_coin_report(args.pickle_path, args.csv)


if __name__ == "__main__":
    main()
