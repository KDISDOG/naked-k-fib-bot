"""
analyze_granville.py — Granville 三輪回測根因分析

執行：
  c:/python312/python.exe scripts/analyze_granville.py --top-n 20 --months 12

流程：
  1. 跑 3 個配置（original / A / C）對同一批幣
  2. 把每筆 trade 的診斷欄位匯出 granville_trades.csv
  3. 4 切片分析：exit_reason / ADX 區間 / BTC regime / 進場後 4h 行為
  4. 印根因報告

Config 對照：
  original: ADX_MIN=20, BREAKOUT=0.3, SCREEN_MIN=7
  A:        ADX_MIN=25, BREAKOUT=0.5, SCREEN_MIN=7
  C:        ADX_MIN=20, BREAKOUT=0.3, SCREEN_MIN=8
"""
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np
from binance.client import Client
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from backtest import (
    run_backtest_granville, fetch_klines, btc_60d_vol_series,
    _resolve_symbol_list, MARGIN_USDT, LEVERAGE,
)
from config import Config

load_dotenv()


CONFIGS = {
    "original": {
        "GRANVILLE_ADX_MIN": 20.0,
        "GRANVILLE_BREAKOUT_ATR_MULT": 0.3,
        "GRANVILLE_SCREEN_MIN_SCORE": 7,
    },
    "A": {
        "GRANVILLE_ADX_MIN": 25.0,
        "GRANVILLE_BREAKOUT_ATR_MULT": 0.5,
        "GRANVILLE_SCREEN_MIN_SCORE": 7,
    },
    "C": {
        "GRANVILLE_ADX_MIN": 20.0,
        "GRANVILLE_BREAKOUT_ATR_MULT": 0.3,
        "GRANVILLE_SCREEN_MIN_SCORE": 8,
    },
}


def collect_trades(client: Client, symbols: list, months: int) -> pd.DataFrame:
    """跑 3 配置 × symbols，把所有 trade 收進 DataFrame。"""
    all_rows = []
    for label, ovr in CONFIGS.items():
        print(f"\n{'='*70}")
        print(f"  跑配置 {label}: {ovr}")
        print(f"{'='*70}")
        for sym in symbols:
            try:
                trades = run_backtest_granville(
                    client, sym, months, debug=False,
                    config_overrides=ovr, config_label=label,
                )
            except Exception as e:
                print(f"  [{sym}] 失敗：{e}")
                continue
            for t in trades:
                if t.result in ("", "OPEN"):
                    continue
                # 計算 entry-to-next-4h 行為
                next_close = getattr(t, "next_bar_close", float("nan"))
                if not np.isnan(next_close) and t.entry > 0:
                    if t.direction == "LONG":
                        sl_dist = t.entry - t.sl
                        adverse = t.entry - next_close   # 正值代表反向
                    else:
                        sl_dist = t.sl - t.entry
                        adverse = next_close - t.entry
                    early_adverse_pct = adverse / sl_dist if sl_dist > 0 else 0
                else:
                    early_adverse_pct = float("nan")

                all_rows.append({
                    "config": getattr(t, "config_label", "?"),
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "rule_triggered": getattr(t, "rule_triggered", ""),
                    "entry_time": t.open_time,
                    "entry_price": t.entry,
                    "exit_time": t.close_time,
                    "exit_price": t.exit_price,
                    "result": t.result,
                    "exit_reason": _categorize_exit(t.result),
                    "pnl_usdt": t.net_pnl,
                    "pnl_pct": ((t.exit_price - t.entry) / t.entry * 100
                                if t.direction == "LONG"
                                else (t.entry - t.exit_price) / t.entry * 100)
                                if (t.entry > 0 and t.exit_price > 0) else 0,
                    "adx_at_entry": getattr(t, "adx_at_entry", float("nan")),
                    "atr_pct_at_entry": getattr(t, "atr_pct_at_entry", float("nan")),
                    "ema60_slope_at_entry": getattr(t, "ema60_slope_at_entry", float("nan")),
                    "screen_score": getattr(t, "screen_score", float("nan")),
                    "next_bar_close": next_close,
                    "early_adverse_pct_of_sl": early_adverse_pct,
                    "bars_held": (t.close_bar - t.open_bar) if t.close_bar else 0,
                })
    return pd.DataFrame(all_rows)


def _categorize_exit(result: str) -> str:
    """把 result 字串歸類為 stop_loss / take_profit / max_hold / break_even"""
    if "BE" in result:
        return "break_even"
    if "TP1+TP2" in result:
        return "take_profit_full"
    if "TP1" in result:
        return "take_profit_partial"
    if "SL" in result:
        return "stop_loss"
    if "TIMEOUT" in result:
        return "max_hold"
    return "other"


def attach_btc_regime(df: pd.DataFrame, vol_df: pd.DataFrame) -> pd.DataFrame:
    """每筆 trade 依 entry_time 取最近的 BTC 60d vol，分 low/mid/high。"""
    if df.empty or vol_df.empty:
        df["btc_60d_vol"] = float("nan")
        df["btc_regime"] = "unknown"
        return df

    vol_df = vol_df.sort_values("time").reset_index(drop=True)
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    times = vol_df["time"].values
    entry_times = df["entry_time"].values

    # searchsorted 找最近已知 vol
    idx = np.searchsorted(times, entry_times, side="right") - 1
    vols = np.full(len(df), np.nan)
    valid = idx >= 0
    vols[valid] = vol_df["btc_60d_vol_pct"].values[idx[valid]]
    df["btc_60d_vol"] = vols

    # 用 quantile 切 low/mid/high
    q33 = vol_df["btc_60d_vol_pct"].quantile(0.33)
    q67 = vol_df["btc_60d_vol_pct"].quantile(0.67)
    df["btc_regime"] = pd.cut(
        df["btc_60d_vol"], bins=[-np.inf, q33, q67, np.inf],
        labels=["low_vol", "mid_vol", "high_vol"],
    )
    print(f"\nBTC vol 分位：q33={q33:.1f}%, q67={q67:.1f}%")
    return df


# ────────────────────── 4 切片分析 ──────────────────────

def slice_1_exit_reason(df: pd.DataFrame) -> str:
    """切片 1：exit_reason 比例分析"""
    out = ["切片 1：按 exit_reason 分組", "=" * 70]
    for cfg in df["config"].unique():
        sub = df[df["config"] == cfg]
        n = len(sub)
        if n == 0:
            continue
        cnt = sub["exit_reason"].value_counts()
        out.append(f"\n[{cfg}] 總 {n} 筆")
        for reason in ["stop_loss", "take_profit_full", "take_profit_partial",
                       "break_even", "max_hold"]:
            c = cnt.get(reason, 0)
            pct = c / n * 100
            mean_pnl = sub[sub["exit_reason"] == reason]["pnl_usdt"].mean() \
                if c > 0 else 0
            out.append(f"  {reason:<22} {c:>3} ({pct:>5.1f}%)  "
                       f"avg PnL = {mean_pnl:+.2f}")

        # 規則式診斷
        sl_pct = cnt.get("stop_loss", 0) / n * 100
        mh_pct = cnt.get("max_hold", 0) / n * 100
        tp_full_pct = cnt.get("take_profit_full", 0) / n * 100
        diag = []
        if sl_pct > 60:
            diag.append("⚠ SL > 60% → 進場太早或止損太緊")
        if mh_pct > 30:
            diag.append("⚠ TIMEOUT > 30% → 訊號失效但拖太久")
        if tp_full_pct < 20:
            diag.append("⚠ 完整 TP < 20% → 移動止盈擰太緊或 trailing 邏輯失效")
        if not diag:
            diag.append("✓ 出場分布健康")
        out.append("  診斷：" + " | ".join(diag))
    return "\n".join(out)


def slice_2_adx_buckets(df: pd.DataFrame) -> str:
    """切片 2：ADX 區間 vs 勝率"""
    out = ["\n\n切片 2：按 ADX 區間分組", "=" * 70]
    bins = [(20, 25), (25, 30), (30, 40), (40, 100)]
    for cfg in df["config"].unique():
        sub = df[df["config"] == cfg]
        out.append(f"\n[{cfg}] 總 {len(sub)} 筆")
        out.append(f"  {'ADX 區間':<10} {'樣本':>6} {'勝率':>8} {'AvgPnL':>10}")
        wr_curve = []
        for lo, hi in bins:
            bucket = sub[(sub["adx_at_entry"] >= lo) & (sub["adx_at_entry"] < hi)]
            n = len(bucket)
            wr = (bucket["pnl_usdt"] > 0).mean() * 100 if n > 0 else 0
            avg = bucket["pnl_usdt"].mean() if n > 0 else 0
            out.append(f"  {lo}-{hi:<6}: {n:>6} {wr:>7.1f}% {avg:>+10.3f}")
            if n >= 3:
                wr_curve.append((lo, wr))
        if len(wr_curve) >= 3:
            wrs = [w for _, w in wr_curve]
            monotonic = all(wrs[i] <= wrs[i+1] for i in range(len(wrs)-1))
            if monotonic:
                out.append("  ✓ 勝率單調隨 ADX 上升 → 可考慮提高 ADX 門檻")
            else:
                out.append("  ✗ 勝率非單調 → ADX 不是有效特徵，找別的")
    return "\n".join(out)


def slice_3_btc_regime(df: pd.DataFrame) -> str:
    """切片 3：BTC 60d vol regime 拆分"""
    out = ["\n\n切片 3：按 BTC 60 日波動率 regime 分組", "=" * 70]
    for cfg in df["config"].unique():
        sub = df[df["config"] == cfg]
        out.append(f"\n[{cfg}] 總 {len(sub)} 筆")
        out.append(f"  {'regime':<12} {'樣本':>6} {'勝率':>8} "
                   f"{'TotalPnL':>10} {'AvgPnL':>10}")
        for regime in ["low_vol", "mid_vol", "high_vol"]:
            bucket = sub[sub["btc_regime"] == regime]
            n = len(bucket)
            wr = (bucket["pnl_usdt"] > 0).mean() * 100 if n > 0 else 0
            tot = bucket["pnl_usdt"].sum() if n > 0 else 0
            avg = bucket["pnl_usdt"].mean() if n > 0 else 0
            mark = "✗" if (n >= 5 and tot < 0 and wr < 35) else " "
            out.append(f"  {regime:<12} {n:>6} {wr:>7.1f}% "
                       f"{tot:>+10.2f} {avg:>+10.3f}  {mark}")
    return "\n".join(out)


def slice_4_early_adverse(df: pd.DataFrame) -> str:
    """切片 4：進場後第 1 根（4h 內）反向幅度 / SL 距離"""
    out = ["\n\n切片 4：進場後 4 小時行為分組", "=" * 70]
    out.append("  early_adverse_pct = (進場後第 1 根反向距離 / SL 距離)")
    out.append("  > 50% 代表「進場 4h 內已往 SL 走半路」")
    for cfg in df["config"].unique():
        sub = df[df["config"] == cfg].copy()
        out.append(f"\n[{cfg}] 總 {len(sub)} 筆")
        valid = sub.dropna(subset=["early_adverse_pct_of_sl"])
        bins = [(-np.inf, 0), (0, 0.25), (0.25, 0.5), (0.5, 1.0), (1.0, np.inf)]
        labels = ["順向", "微逆≤25%", "逆25-50%", "逆50-100%", "逆破SL"]
        out.append(f"  {'區間':<14} {'樣本':>6} {'勝率':>8} {'最終 PnL':>10}")
        for (lo, hi), lab in zip(bins, labels):
            bucket = valid[(valid["early_adverse_pct_of_sl"] >= lo)
                            & (valid["early_adverse_pct_of_sl"] < hi)]
            n = len(bucket)
            wr = (bucket["pnl_usdt"] > 0).mean() * 100 if n > 0 else 0
            tot = bucket["pnl_usdt"].sum() if n > 0 else 0
            out.append(f"  {lab:<14} {n:>6} {wr:>7.1f}% {tot:>+10.2f}")
        # 高比例早期反向
        early_bad = valid[valid["early_adverse_pct_of_sl"] > 0.5]
        pct_bad = len(early_bad) / len(valid) * 100 if len(valid) > 0 else 0
        if pct_bad > 30:
            out.append(f"  ⚠ {pct_bad:.0f}% 訊號 4h 內就走超過半距 → 進場時機差")
    return "\n".join(out)


def root_cause_report(df: pd.DataFrame) -> str:
    """匯總四切片結論寫根因報告"""
    out = ["\n\n根因報告", "=" * 70]
    if df.empty:
        out.append("無交易資料")
        return "\n".join(out)

    # 用 original config 為基準（最寬鬆，看真正的訊號分布）
    base = df[df["config"] == "original"]
    n = len(base)
    if n == 0:
        out.append("original config 無交易")
        return "\n".join(out)

    total_pnl = base["pnl_usdt"].sum()
    win_rate = (base["pnl_usdt"] > 0).mean() * 100
    sl_pct = (base["exit_reason"] == "stop_loss").mean() * 100
    tp_full_pct = (base["exit_reason"] == "take_profit_full").mean() * 100
    early_bad = base[base["early_adverse_pct_of_sl"] > 0.5]
    pct_early_bad = len(early_bad) / len(base.dropna(subset=["early_adverse_pct_of_sl"])) * 100 \
        if len(base.dropna(subset=["early_adverse_pct_of_sl"])) > 0 else 0

    # ADX 區間是否單調
    adx_buckets = []
    for lo, hi in [(20, 25), (25, 30), (30, 40), (40, 100)]:
        bucket = base[(base["adx_at_entry"] >= lo) & (base["adx_at_entry"] < hi)]
        if len(bucket) >= 3:
            wr = (bucket["pnl_usdt"] > 0).mean() * 100
            adx_buckets.append(wr)
    adx_monotonic = (
        len(adx_buckets) >= 3
        and all(adx_buckets[i] <= adx_buckets[i+1]
                for i in range(len(adx_buckets)-1))
    )

    # BTC regime：哪個 regime 最虧
    regime_pnl = base.groupby("btc_regime", observed=False)["pnl_usdt"].sum().to_dict()
    worst_regime = min(regime_pnl, key=regime_pnl.get) if regime_pnl else None

    out.append(f"\n基準資料（original config，{n} 筆）：")
    out.append(f"  總 PnL: {total_pnl:+.2f} USDT")
    out.append(f"  勝率: {win_rate:.1f}%")
    out.append(f"  SL 比例: {sl_pct:.1f}%")
    out.append(f"  完整 TP 比例: {tp_full_pct:.1f}%")
    out.append(f"  進場 4h 內反向 > 50% SL 距離: {pct_early_bad:.1f}%")
    out.append(f"  ADX 勝率單調? {'是' if adx_monotonic else '否'}")
    out.append(f"  最虧 regime: {worst_regime} (PnL = {regime_pnl.get(worst_regime, 0):+.2f})")

    # 主要原因判斷
    out.append("\n主要虧損原因：")
    causes = []
    if sl_pct > 60:
        causes.append("止損頻率過高（SL > 60%）")
    if pct_early_bad > 35:
        causes.append("進場時機差（4h 內常反向）")
    if not adx_monotonic and len(adx_buckets) >= 3:
        causes.append("ADX 不是有效特徵（勝率非單調）")
    if regime_pnl and worst_regime and abs(regime_pnl[worst_regime]) > 5:
        causes.append(f"特定 BTC regime 損失集中（{worst_regime}）")
    if tp_full_pct < 20:
        causes.append("移動止盈無法兌現完整目標")

    if not causes:
        out.append("  ✓ 無單一明顯根因（可能策略本質期望值就微負）")
    else:
        for i, c in enumerate(causes, 1):
            out.append(f"  {i}. {c}")

    # 建議
    out.append("\n建議的修正方向：")
    if "進場時機差" in " ".join(causes):
        out.append("  → 重新設計進場：突破後等回測不破再進（slow variant 的概念）")
    if "ADX 不是有效特徵" in " ".join(causes):
        out.append("  → 拋棄 ADX 過濾，找別的特徵（成交量趨勢、market structure）")
    if "特定 BTC regime" in " ".join(causes):
        out.append(f"  → 加 regime filter：{worst_regime} 期間暫停 Granville")
    if "止損頻率過高" in " ".join(causes):
        out.append("  → 進場條件本身需要重新設計（不是調參）")
    if not causes:
        out.append("  → 接受現狀（本來就是負期望策略）或放棄")

    # 預期改善
    if causes:
        improved_sl = max(35, sl_pct - 15)
        improved_wr = min(55, win_rate + 8)
        out.append("\n預期改善後（若修正方向有效）：")
        out.append(f"  勝率: {win_rate:.1f}% → ~{improved_wr:.0f}%")
        out.append(f"  SL%: {sl_pct:.1f}% → ~{improved_sl:.0f}%")
        out.append("  賺賠比: 1.73 → 預期 2.0+（樣本 < 30 不可信）")
    else:
        out.append("\n結論：filter tweak 救不了，要嘛重新設計、要嘛放棄。")
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=None,
                        help="逗號分隔幣列表（覆蓋 --top-n）")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--csv", default="granville_trades.csv",
                        help="輸出 CSV 檔名")
    parser.add_argument("--exclude-stable", action="store_true", default=True)
    args = parser.parse_args()

    use_testnet = False
    client = Client(
        os.getenv("BINANCE_API_KEY", ""),
        os.getenv("BINANCE_SECRET", ""),
        testnet=use_testnet,
    )

    symbols = _resolve_symbol_list(args, client)
    print(f"分析 {len(symbols)} 幣 × 3 配置 × {args.months} 月")

    df = collect_trades(client, symbols, args.months)
    if df.empty:
        print("\n⚠ 無任何 trade，無法分析")
        return

    # BTC regime
    print("\n下載 BTC 60d vol series...")
    vol_df = btc_60d_vol_series(client, args.months)
    df = attach_btc_regime(df, vol_df)

    # 匯出 CSV
    df.to_csv(args.csv, index=False, encoding="utf-8-sig")
    print(f"\n已匯出 {len(df)} 筆 trade 到 {args.csv}")

    # 4 切片
    print(slice_1_exit_reason(df))
    print(slice_2_adx_buckets(df))
    print(slice_3_btc_regime(df))
    print(slice_4_early_adverse(df))
    print(root_cause_report(df))


if __name__ == "__main__":
    main()
