"""
pattern_miner.py — 從 coin_features + wf_results 找「特徵 ↔ 策略表現」配對訊號。

對每個 (strategy, feature) 配對：
  1. 數值特徵：用 33/66 percentile 切 low/mid/high
     類別特徵 (asset_class)：直接 group-by
  2. 計算每 tier 的 median PnL / median WR / Σ n_trades
  3. 標記 signal：
        top tier - bottom tier 的 PnL 中位差 > 1.5×全策略 PnL std (跨幣)，或
        WR 差 > 5pp (0.05)
  4. 推薦閾值：取最好 vs 最差 tier 的分位邊界（建議做 sweep 確認）

輸入：
  features_df：scripts/coin_features.compute_coin_features 的回傳
  wf_results_dir：.cache/wf_results/，含 <strategy>_<sym>_39m.pkl

輸出：
  reports/pattern_mining_<timestamp>.md

樣本門檻：
  per-coin n_trades < MIN_N（預設 10）→ 從該策略統計中剔除
  整個策略剩 < 5 幣有效 → 跳過該策略
"""
import os
import sys
import pickle
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from wf_runner import _segment_metrics

log = logging.getLogger("pattern_miner")

NUMERIC_FEATURES = [
    "atr_pct_med", "adx_med", "range_share", "whipsaw_idx",
    "gap_freq", "volume_quote_med", "btc_corr_30d",
]
CATEGORICAL_FEATURES = ["asset_class"]

MIN_N_PER_COIN = 10           # 單幣 < 10 筆訊號 → 該策略剔除這幣
MIN_VALID_COINS = 5           # 策略剩 < 5 幣 → 整個策略結果不可信
SIGNAL_PNL_K = 1.5            # PnL gap 顯著倍數 (× std)
SIGNAL_WR_DIFF = 0.05         # WR 差 5pp


# ── 載入 wf_results ──────────────────────────────────────────────
def _load_wf_aggregate(pkl_path: Path) -> dict:
    """讀一個 wf pickle，回傳全期合併指標。"""
    with open(pkl_path, "rb") as fh:
        wf = pickle.load(fh)
    all_trades = []
    for s in wf["segments"]:
        all_trades.extend(s["trades"])
    m = _segment_metrics(all_trades)
    return {
        "n_trades": int(m["n_trades"]),
        "win_rate": float(m["win_rate"]),
        "total_pnl": float(m["total_pnl"]),
        "avg_rr": float(m.get("avg_rr") or 0.0),
        "max_dd": float(m.get("max_dd") or 0.0),
    }


def _build_strategy_df(wf_results_dir: Path, features_df: pd.DataFrame,
                       strategy_label: str, prefix: str = "") -> pd.DataFrame:
    """讀 <prefix><strategy>_<sym>_39m.pkl，跟 features_df 做 inner-join。"""
    rows = []
    for sym in features_df["symbol"]:
        pkl = wf_results_dir / f"{prefix}{strategy_label}_{sym}_39m.pkl"
        if not pkl.exists():
            continue
        try:
            metr = _load_wf_aggregate(pkl)
            metr["symbol"] = sym
            rows.append(metr)
        except Exception as e:
            log.warning(f"  load fail {pkl.name}: {e}")
    if not rows:
        return pd.DataFrame()
    df_m = pd.DataFrame(rows)
    return df_m.merge(features_df, on="symbol", how="inner")


# ── 分析 ────────────────────────────────────────────────────────
def _tier_split_numeric(df: pd.DataFrame, feature: str) -> pd.DataFrame:
    """加一欄 _tier ∈ {low,mid,high}。NaN 標 'na'。"""
    out = df.copy()
    vals = out[feature].dropna()
    if len(vals) < 3:
        out["_tier"] = "na"
        return out
    p33, p66 = np.nanpercentile(vals, [33, 66])
    if p33 == p66:
        # 退化：全部相同 → 無法切
        out["_tier"] = "na"
        out._tier_p33 = p33  # type: ignore
        out._tier_p66 = p66  # type: ignore
        return out

    def _tag(v):
        if pd.isna(v):
            return "na"
        if v <= p33:
            return "low"
        if v <= p66:
            return "mid"
        return "high"

    out["_tier"] = out[feature].map(_tag)
    out._tier_p33 = float(p33)  # type: ignore  (掛屬性給後面用)
    out._tier_p66 = float(p66)  # type: ignore
    return out


def _summarize_by_tier(df: pd.DataFrame) -> pd.DataFrame:
    """以 _tier 分群，回傳 median PnL/WR + Σ n_trades + 幣數。"""
    g = df.groupby("_tier")
    return pd.DataFrame({
        "n_coins":       g.size(),
        "trades_total":  g["n_trades"].sum(),
        "median_pnl":    g["total_pnl"].median(),
        "median_wr":     g["win_rate"].median(),
        "median_feat":   g[df.attrs.get("_feature", df.columns[0])].median()
                          if "_feature" in df.attrs else 0.0,
    }).reset_index()


def _is_signal(top_pnl: float, bot_pnl: float, top_wr: float, bot_wr: float,
                strategy_pnl_std: float) -> tuple[bool, str]:
    reasons = []
    if not np.isnan(strategy_pnl_std) and strategy_pnl_std > 0:
        if abs(top_pnl - bot_pnl) > SIGNAL_PNL_K * strategy_pnl_std:
            reasons.append(f"PnL gap {top_pnl - bot_pnl:+.2f}U "
                            f"> 1.5×std ({strategy_pnl_std:.2f})")
    if abs(top_wr - bot_wr) > SIGNAL_WR_DIFF:
        reasons.append(f"WR gap {(top_wr - bot_wr) * 100:+.1f}pp > 5pp")
    return (len(reasons) > 0, "; ".join(reasons))


def _analyze_strategy(strategy_label: str, df_strat: pd.DataFrame) -> dict:
    """回傳 {strategy, n_coins, signals: [...], tier_tables: {feat: df}, summary}"""
    df_valid = df_strat[df_strat["n_trades"] >= MIN_N_PER_COIN].copy()
    n_valid = len(df_valid)

    summary = {
        "n_coins_total": len(df_strat),
        "n_coins_valid": n_valid,
        "median_pnl_valid": float(df_valid["total_pnl"].median()) if n_valid else np.nan,
        "median_wr_valid": float(df_valid["win_rate"].median()) if n_valid else np.nan,
        "pnl_std_valid": float(df_valid["total_pnl"].std()) if n_valid >= 2 else np.nan,
    }

    if n_valid < MIN_VALID_COINS:
        return {
            "strategy": strategy_label,
            "summary": summary,
            "signals": [],
            "tier_tables": {},
            "skipped": True,
            "skip_reason": f"only {n_valid} valid coins (< {MIN_VALID_COINS})",
        }

    pnl_std = summary["pnl_std_valid"]
    signals = []
    tier_tables = {}

    # 數值特徵
    for feat in NUMERIC_FEATURES:
        if feat not in df_valid.columns:
            continue
        df_t = _tier_split_numeric(df_valid, feat)
        df_t.attrs["_feature"] = feat
        tab = df_t.groupby("_tier", dropna=False).agg(
            n_coins=("symbol", "count"),
            trades_total=("n_trades", "sum"),
            median_pnl=("total_pnl", "median"),
            median_wr=("win_rate", "median"),
            median_feat=(feat, "median"),
        ).reset_index()
        tier_tables[feat] = tab

        # 找出 low/high tier 看 signal
        try:
            lo = tab[tab["_tier"] == "low"].iloc[0]
            hi = tab[tab["_tier"] == "high"].iloc[0]
        except IndexError:
            continue  # 缺 tier 就跳
        is_sig, reason = _is_signal(
            top_pnl=hi["median_pnl"], bot_pnl=lo["median_pnl"],
            top_wr=hi["median_wr"], bot_wr=lo["median_wr"],
            strategy_pnl_std=pnl_std,
        )
        if is_sig:
            # 推薦閾值：好 tier 的入口邊界
            better = "high" if hi["median_pnl"] >= lo["median_pnl"] else "low"
            cut = float(getattr(df_t, "_tier_p66", np.nan)) if better == "high" \
                  else float(getattr(df_t, "_tier_p33", np.nan))
            signals.append({
                "feature": feat,
                "better_tier": better,
                "low_pnl": float(lo["median_pnl"]),
                "high_pnl": float(hi["median_pnl"]),
                "low_wr": float(lo["median_wr"]),
                "high_wr": float(hi["median_wr"]),
                "low_n": int(lo["trades_total"]),
                "high_n": int(hi["trades_total"]),
                "p33": float(getattr(df_t, "_tier_p33", np.nan)),
                "p66": float(getattr(df_t, "_tier_p66", np.nan)),
                "suggested_cut": cut,
                "reason": reason,
            })

    # asset_class
    if "asset_class" in df_valid.columns:
        df_c = df_valid.copy()
        df_c["_tier"] = df_c["asset_class"]
        tab_c = df_c.groupby("_tier").agg(
            n_coins=("symbol", "count"),
            trades_total=("n_trades", "sum"),
            median_pnl=("total_pnl", "median"),
            median_wr=("win_rate", "median"),
        ).reset_index()
        tier_tables["asset_class"] = tab_c

        if len(tab_c) >= 2:
            tab_sorted = tab_c.sort_values("median_pnl")
            lo = tab_sorted.iloc[0]
            hi = tab_sorted.iloc[-1]
            is_sig, reason = _is_signal(
                top_pnl=hi["median_pnl"], bot_pnl=lo["median_pnl"],
                top_wr=hi["median_wr"], bot_wr=lo["median_wr"],
                strategy_pnl_std=pnl_std,
            )
            if is_sig:
                signals.append({
                    "feature": "asset_class",
                    "better_class": str(hi["_tier"]),
                    "worse_class": str(lo["_tier"]),
                    "best_pnl": float(hi["median_pnl"]),
                    "worst_pnl": float(lo["median_pnl"]),
                    "best_wr": float(hi["median_wr"]),
                    "worst_wr": float(lo["median_wr"]),
                    "reason": reason,
                })

    return {
        "strategy": strategy_label,
        "summary": summary,
        "signals": signals,
        "tier_tables": tier_tables,
        "skipped": False,
    }


# ── Markdown 產出 ─────────────────────────────────────────────
def _fmt_num(v, d=2, suffix=""):
    if pd.isna(v):
        return "—"
    return f"{v:.{d}f}{suffix}"


def _signal_to_threshold_line(stratl: str, sig: dict) -> str:
    feat = sig["feature"]
    if feat == "asset_class":
        return (f"- **[{stratl}]** asset_class — better={sig['better_class']} "
                 f"(median PnL {sig['best_pnl']:+.2f}U)，"
                 f"worse={sig['worse_class']} ({sig['worst_pnl']:+.2f}U) → "
                 f"考慮在 {stratl.upper()} 分支加白名單；推薦做 sweep 確認")
    cut = sig.get("suggested_cut", np.nan)
    direction = "<=" if sig["better_tier"] == "low" else ">="
    return (f"- **[{stratl}]** {feat} {direction} `{cut:.4f}` "
            f"(better={sig['better_tier']} tier, "
            f"PnL gap {sig['high_pnl'] - sig['low_pnl']:+.2f}U, "
            f"WR diff {(sig['high_wr'] - sig['low_wr']) * 100:+.1f}pp)；推薦做 sweep 確認")


def render_report(features_df: pd.DataFrame,
                   per_strat_results: list[dict],
                   output_path: Path,
                   wf_dir: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.append(f"# Pattern mining report\n")
    lines.append(f"_Generated: {ts}_\n")
    lines.append(f"_Source: features × {len(per_strat_results)} strategies × "
                 f"{len(features_df)} coins (wf_dir=`{wf_dir.name}`)_\n")
    lines.append(f"_Sample thresholds: per-coin n≥{MIN_N_PER_COIN}; "
                 f"strategy needs ≥{MIN_VALID_COINS} valid coins; "
                 f"signal = PnL gap>{SIGNAL_PNL_K}×std OR WR diff>{SIGNAL_WR_DIFF*100:.0f}pp_\n\n")

    # 1. Coin features
    lines.append("## 1. Coin features (固定欄位)\n\n")
    cols = list(features_df.columns)
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for _, row in features_df.iterrows():
        vals = []
        for c in cols:
            v = row[c]
            if pd.isna(v):
                vals.append("—")
            elif isinstance(v, float):
                if c == "volume_quote_med":
                    vals.append(f"{v:,.0f}")
                else:
                    vals.append(f"{v:.4f}".rstrip("0").rstrip("."))
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    lines.append("")

    # 2. Strategy summary
    lines.append("\n## 2. Strategy 全期成果（n≥{} 才入統計）\n".format(MIN_N_PER_COIN))
    lines.append("| strategy | n_coins_total | n_coins_valid | "
                 "median_pnl(U) | median_wr | pnl_std(U) | status |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for r in per_strat_results:
        s = r["summary"]
        status = "skipped" if r.get("skipped") else "OK"
        lines.append(f"| {r['strategy']} | {s['n_coins_total']} | {s['n_coins_valid']} | "
                     f"{_fmt_num(s['median_pnl_valid'], 2)} | "
                     f"{_fmt_num(s['median_wr_valid']*100, 1, '%')} | "
                     f"{_fmt_num(s['pnl_std_valid'], 2)} | {status} |")
    lines.append("")

    # 3. 每策略明細
    lines.append("\n## 3. 各策略 × 特徵 tier 表\n")
    for r in per_strat_results:
        lines.append(f"\n### `{r['strategy']}`")
        if r.get("skipped"):
            lines.append(f"\n_skipped: {r.get('skip_reason')}_\n")
            continue
        for feat, tab in r["tier_tables"].items():
            lines.append(f"\n**{feat}**\n")
            cols = list(tab.columns)
            lines.append("| " + " | ".join(cols) + " |")
            lines.append("| " + " | ".join("---" for _ in cols) + " |")
            for _, row in tab.iterrows():
                vals = []
                for c in cols:
                    v = row[c]
                    if pd.isna(v):
                        vals.append("—")
                    elif isinstance(v, float):
                        if c in ("median_wr",):
                            vals.append(f"{v*100:.1f}%")
                        elif c == "median_pnl":
                            vals.append(f"{v:+.2f}")
                        else:
                            vals.append(f"{v:.4f}".rstrip("0").rstrip("."))
                    else:
                        vals.append(str(v))
                lines.append("| " + " | ".join(vals) + " |")
        # signals 標題
        if r["signals"]:
            lines.append(f"\n**Signals ({len(r['signals'])})**\n")
            for sig in r["signals"]:
                feat = sig["feature"]
                if feat == "asset_class":
                    lines.append(f"- ⚠️ **asset_class**: best=`{sig['better_class']}` "
                                 f"PnL={sig['best_pnl']:+.2f}U/WR={sig['best_wr']*100:.1f}%, "
                                 f"worst=`{sig['worse_class']}` "
                                 f"PnL={sig['worst_pnl']:+.2f}U/WR={sig['worst_wr']*100:.1f}% "
                                 f"— {sig['reason']}")
                else:
                    lines.append(f"- ⚠️ **{feat}** (better tier=`{sig['better_tier']}`): "
                                 f"low PnL={sig['low_pnl']:+.2f}U "
                                 f"(WR {sig['low_wr']*100:.1f}%, n={sig['low_n']}) vs "
                                 f"high PnL={sig['high_pnl']:+.2f}U "
                                 f"(WR {sig['high_wr']*100:.1f}%, n={sig['high_n']}) "
                                 f"— {sig['reason']}")
        else:
            lines.append("\n_no signals_\n")

    # 4. 建議的 .env 新增閾值
    lines.append("\n## 4. 建議的 .env 新增閾值（皆需 sweep 確認）\n")
    any_sig = False
    for r in per_strat_results:
        if r.get("skipped"):
            continue
        for sig in r["signals"]:
            lines.append(_signal_to_threshold_line(r["strategy"], sig))
            any_sig = True
    if not any_sig:
        lines.append("\n_(沒有 signal 超過顯著門檻)_\n")

    # 寫檔
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


# ── 對外主入口 ─────────────────────────────────────────────────
def mine_patterns(features_df: pd.DataFrame,
                   wf_results_dir: Path,
                   strategies: dict,
                   output_path: Optional[Path] = None,
                   prefix: str = "") -> dict:
    """
    strategies: {label: prefix} — e.g. {"masr":"", "bd":"", ..., "granville":"_appendix_"}
    回傳 {report_path, per_strat_results, df_per_strat}
    """
    per_strat_results = []
    df_per_strat = {}
    for label, pfx in strategies.items():
        df_s = _build_strategy_df(wf_results_dir, features_df, label, prefix=pfx)
        if df_s.empty:
            print(f"  [{label}] 找不到 wf pickles, skip")
            continue
        df_per_strat[label] = df_s
        r = _analyze_strategy(label, df_s)
        per_strat_results.append(r)
        n_sig = len(r["signals"])
        print(f"  [{label}] valid={r['summary']['n_coins_valid']}/{r['summary']['n_coins_total']}  "
              f"signals={n_sig}  "
              f"median_pnl={_fmt_num(r['summary']['median_pnl_valid'])}U")

    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        output_path = Path(__file__).parent.parent / "reports" / f"pattern_mining_{ts}.md"
    render_report(features_df, per_strat_results, output_path, wf_results_dir)
    print(f"\n[report] saved → {output_path}")
    return {
        "report_path": str(output_path),
        "per_strat_results": per_strat_results,
        "df_per_strat": df_per_strat,
    }


# ── CLI ────────────────────────────────────────────────────────
if __name__ == "__main__":
    from binance.client import Client
    from dotenv import load_dotenv
    from coin_features import compute_coin_features
    load_dotenv()

    SYMBOLS = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
        "1000PEPEUSDT", "SKYAIUSDT", "XAUUSDT", "XAGUSDT", "CLUSDT",
    ]
    client = Client(os.getenv("BINANCE_API_KEY", ""),
                    os.getenv("BINANCE_SECRET", ""), testnet=False)

    print("[1/3] 載入 coin features...")
    feats = compute_coin_features(client, SYMBOLS, months=39)

    print("\n[2/3] 主分析（masr/bd/mr/smc/nkf）...")
    main_strats = {"masr": "", "bd": "", "mr": "", "smc": "", "nkf": ""}
    wf_dir = Path(__file__).parent.parent / ".cache" / "wf_results"
    main_out = mine_patterns(feats, wf_dir, main_strats)

    print("\n[3/3] 附錄（granville/masr_short）...")
    appx_strats = {"granville": "_appendix_", "masr_short": "_appendix_"}
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    appx_out_path = Path(__file__).parent.parent / "reports" / f"pattern_mining_appendix_{ts}.md"
    appx_out = mine_patterns(feats, wf_dir, appx_strats, output_path=appx_out_path)

    print("\n[完成]")
    print(f"  主分析報告:   {main_out['report_path']}")
    print(f"  附錄報告:     {appx_out['report_path']}")
