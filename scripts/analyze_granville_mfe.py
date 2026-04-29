"""
analyze_granville_mfe.py — Granville 擴大樣本回測 + MFE 分析

只做兩件事，不下結論：
  1. 39 個月（2023-01 ~ 2026-04）× 10 大盤幣回測，原版參數不變
  2. 對「順向但虧損」trade 列出最大正向 PnL vs 最終 PnL，並算回吐比例中位數

執行：
  c:/python312/python.exe scripts/analyze_granville_mfe.py
"""
import os
import sys
from pathlib import Path

import pandas as pd
import numpy as np
from binance.client import Client
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from backtest import run_backtest_granville, MARGIN_USDT, LEVERAGE

load_dotenv()


SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT",
    "AVAXUSDT", "DOTUSDT", "LINKUSDT", "ATOMUSDT", "APTUSDT",
]
MONTHS = 39


def main():
    client = Client(
        os.getenv("BINANCE_API_KEY", ""),
        os.getenv("BINANCE_SECRET", ""),
        testnet=False,
    )

    all_trades = []
    print(f"執行 Granville 擴大回測：{len(SYMBOLS)} 幣 × {MONTHS} 個月（原版參數）")
    for sym in SYMBOLS:
        try:
            trades = run_backtest_granville(
                client, sym, MONTHS, debug=False,
                config_overrides=None, config_label="original",
            )
        except Exception as e:
            print(f"  [{sym}] 失敗：{e}")
            continue
        for t in trades:
            if t.result in ("", "OPEN"):
                continue
            mfp = getattr(t, "max_favorable_price", t.entry)
            mavp = getattr(t, "max_adverse_price", t.entry)
            # MFE/MAE 用「全倉若按峰值出場」的概念計（不扣手續費，給判斷給回吐用）
            if t.direction == "LONG":
                mfe_pnl = (mfp - t.entry) * t.qty
                mae_pnl = (mavp - t.entry) * t.qty
            else:
                mfe_pnl = (t.entry - mfp) * t.qty
                mae_pnl = (t.entry - mavp) * t.qty
            all_trades.append({
                "symbol": t.symbol,
                "direction": t.direction,
                "entry_time": t.open_time,
                "entry_price": t.entry,
                "exit_time": t.close_time,
                "exit_price": t.exit_price,
                "result": t.result,
                "max_fav_price": round(mfp, 6),
                "max_adv_price": round(mavp, 6),
                "max_favorable_pnl": round(mfe_pnl, 4),
                "max_adverse_pnl": round(mae_pnl, 4),
                "final_pnl": round(t.net_pnl, 4),
                "bars_held": (t.close_bar - t.open_bar) if t.close_bar else 0,
            })

    df = pd.DataFrame(all_trades)
    if df.empty:
        print("\n⚠ 無 trade 收集到")
        return

    # ── 數據組 1：擴大回測總計 ────────────────────────────────
    print(f"\n{'='*78}")
    print(f"數據組 1：擴大回測總計（10 幣 × {MONTHS} 個月，原版參數）")
    print(f"{'='*78}")
    print(f"  總 trade 數：           {len(df)}")
    wins = df[df["final_pnl"] > 0]
    print(f"  勝率：                  {len(wins) / len(df) * 100:.1f}% ({len(wins)}/{len(df)})")
    print(f"  總 PnL：                {df['final_pnl'].sum():+.2f} USDT")
    print(f"  平均 PnL：              {df['final_pnl'].mean():+.4f} USDT/單")
    print(f"  exit reason 分布：")
    cnt = df["result"].value_counts()
    for r in ["SL", "TP1+TP2", "TP1+SL", "TP1+BE", "TIMEOUT"]:
        c = cnt.get(r, 0)
        if c == 0:
            continue
        sub = df[df["result"] == r]
        avg = sub["final_pnl"].mean()
        tot = sub["final_pnl"].sum()
        print(f"    {r:<10} {c:>4} ({c/len(df)*100:>5.1f}%)  "
              f"sum={tot:+.2f}  avg={avg:+.3f}")
    print(f"\n  per-coin 分布：")
    print(f"    {'symbol':<12} {'trades':>7} {'win%':>7} {'pnl':>10}")
    for sym in SYMBOLS:
        sub = df[df["symbol"] == sym]
        n = len(sub)
        if n == 0:
            print(f"    {sym:<12} {0:>7} {'—':>7} {'—':>10}")
            continue
        wr = (sub["final_pnl"] > 0).mean() * 100
        pnl = sub["final_pnl"].sum()
        print(f"    {sym:<12} {n:>7} {wr:>6.1f}% {pnl:>+10.2f}")

    # CSV 全量輸出
    full_csv = "granville_mfe_all_trades.csv"
    df.to_csv(full_csv, index=False, encoding="utf-8-sig")
    print(f"\n  完整 trade 表：{full_csv}（{len(df)} 筆）")

    # ── 數據組 2：「順向但虧損」trade 路徑分析 ────────────────
    print(f"\n{'='*78}")
    print(f"數據組 2：「順向但虧損」trade 價格路徑分析")
    print(f"{'='*78}")
    print(f"  定義：max_favorable_pnl > 0（曾走順向）且 final_pnl ≤ 0（最終虧損）")

    # 篩選
    losers_with_mfe = df[(df["max_favorable_pnl"] > 0) & (df["final_pnl"] <= 0)].copy()
    losers_with_mfe = losers_with_mfe.sort_values("entry_time").reset_index(drop=True)

    if losers_with_mfe.empty:
        print(f"  無符合條件的 trade")
    else:
        print(f"  符合條件 trade 數：{len(losers_with_mfe)}\n")

        # 每筆列表
        print(f"  {'#':>3}  {'symbol':<12} {'side':<6} "
              f"{'entry_time':<19} {'max_fav_pnl':>12} {'final_pnl':>10} "
              f"{'give_back':>10}")
        print(f"  {'-'*78}")
        for idx, row in losers_with_mfe.iterrows():
            mfe = row["max_favorable_pnl"]
            final = row["final_pnl"]
            give_back = (mfe - final) / mfe if mfe > 0 else 0
            t = row["entry_time"]
            t_str = t.strftime("%Y-%m-%d %H:%M") if hasattr(t, "strftime") else str(t)[:16]
            print(f"  {idx+1:>3}  {row['symbol']:<12} {row['direction']:<6} "
                  f"{t_str:<19} {mfe:>+12.4f} {final:>+10.4f} "
                  f"{give_back*100:>9.1f}%")

        # 統計
        give_back_ratios = (
            (losers_with_mfe["max_favorable_pnl"] - losers_with_mfe["final_pnl"])
            / losers_with_mfe["max_favorable_pnl"]
        )
        print(f"\n  ── give_back ratio 統計（順向但虧損 trade）──")
        print(f"    n           = {len(give_back_ratios)}")
        print(f"    min         = {give_back_ratios.min()*100:.1f}%")
        print(f"    25th pct    = {give_back_ratios.quantile(0.25)*100:.1f}%")
        print(f"    median      = {give_back_ratios.median()*100:.1f}%")
        print(f"    75th pct    = {give_back_ratios.quantile(0.75)*100:.1f}%")
        print(f"    max         = {give_back_ratios.max()*100:.1f}%")
        print(f"    mean        = {give_back_ratios.mean()*100:.1f}%")

        loser_csv = "granville_mfe_losers.csv"
        out = losers_with_mfe.copy()
        out["give_back_ratio"] = give_back_ratios.values
        out.to_csv(loser_csv, index=False, encoding="utf-8-sig")
        print(f"\n    輸出：{loser_csv}")

    # ── 補充參考：所有 trade 的 give_back 中位數（含贏家）────────
    all_with_mfe = df[df["max_favorable_pnl"] > 0].copy()
    if not all_with_mfe.empty:
        gb_all = (all_with_mfe["max_favorable_pnl"] - all_with_mfe["final_pnl"]) \
                  / all_with_mfe["max_favorable_pnl"]
        print(f"\n  ── 補充：所有曾走順向 trade 的 give_back ──")
        print(f"    n           = {len(gb_all)}")
        print(f"    median      = {gb_all.median()*100:.1f}%")
        print(f"    mean        = {gb_all.mean()*100:.1f}%")


if __name__ == "__main__":
    main()
