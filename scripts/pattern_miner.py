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
                       strategy_label: str, prefix: str = "",
                       filename_pattern: Optional[str] = None) -> pd.DataFrame:
    """讀 wf pickle 跟 features_df 做 inner-join。
    filename_pattern：可包含 {strategy} {symbol} 占位符；
       預設 "{prefix}{strategy}_{symbol}_39m.pkl"（保持向後相容）。
    """
    rows = []
    for sym in features_df["symbol"]:
        if filename_pattern:
            fname = filename_pattern.format(strategy=strategy_label, symbol=sym)
        else:
            fname = f"{prefix}{strategy_label}_{sym}_39m.pkl"
        pkl = wf_results_dir / fname
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


def _build_strategy_trades_df(wf_results_dir: Path, features_df: pd.DataFrame,
                               strategy_label: str,
                               filename_pattern: Optional[str] = None,
                               prefix: str = "") -> tuple[pd.DataFrame, dict[str, list]]:
    """進階版：除了 per-coin 聚合，也回傳每幣的原始 trades list（給 2-feature mining 用）。
    回傳 (per_coin_df, {sym: [trades...]})
    """
    rows = []
    sym2trades: dict[str, list] = {}
    for sym in features_df["symbol"]:
        if filename_pattern:
            fname = filename_pattern.format(strategy=strategy_label, symbol=sym)
        else:
            fname = f"{prefix}{strategy_label}_{sym}_39m.pkl"
        pkl = wf_results_dir / fname
        if not pkl.exists():
            continue
        try:
            with open(pkl, "rb") as fh:
                wf = pickle.load(fh)
            tt = [t for s in wf["segments"] for t in s["trades"]]
            sym2trades[sym] = tt
            metr = _segment_metrics(tt)
            rows.append({
                "symbol": sym,
                "n_trades": int(metr["n_trades"]),
                "win_rate": float(metr["win_rate"]),
                "total_pnl": float(metr["total_pnl"]),
            })
        except Exception as e:
            log.warning(f"  load fail {pkl.name}: {e}")
    if not rows:
        return pd.DataFrame(), {}
    df_m = pd.DataFrame(rows).merge(features_df, on="symbol", how="inner")
    return df_m, sym2trades


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


# ═══════════════════════════════════════════════════════════════
# 方向 A：放寬門檻的 single-feature mining（per strategy）
# ═══════════════════════════════════════════════════════════════
NUMERIC_FEATS = NUMERIC_FEATURES  # alias
CATEGORICAL_FEATS = CATEGORICAL_FEATURES


def _tier_summary_for_feature(df: pd.DataFrame, feature: str) -> tuple[pd.DataFrame, dict]:
    """切 tier 並算每 tier (n_coins, trades_total, median_pnl, median_wr)。
    回傳 (tab_df, attrs={p33, p66})。
    """
    df_t = _tier_split_numeric(df, feature)
    p33 = float(getattr(df_t, "_tier_p33", float("nan")))
    p66 = float(getattr(df_t, "_tier_p66", float("nan")))
    tab = df_t.groupby("_tier", dropna=False).agg(
        n_coins=("symbol", "count"),
        trades_total=("n_trades", "sum"),
        median_pnl=("total_pnl", "median"),
        median_wr=("win_rate", "median"),
    ).reset_index()
    return tab, {"p33": p33, "p66": p66}


def _is_signal_relaxed(top_pnl: float, bot_pnl: float,
                       top_wr: float, bot_wr: float,
                       pnl_std: float,
                       pnl_sigma_threshold: float,
                       wr_pp_threshold: float,
                       use_or_logic: bool) -> tuple[bool, str, dict]:
    """放寬版 signal 判定。回傳 (is_sig, reason, metrics)。"""
    pnl_gap = top_pnl - bot_pnl
    wr_diff_pp = (top_wr - bot_wr) * 100
    pnl_sig_ok = (not (pnl_std is None or np.isnan(pnl_std) or pnl_std <= 0)
                   and abs(pnl_gap) > pnl_sigma_threshold * pnl_std)
    wr_sig_ok = abs(wr_diff_pp) > wr_pp_threshold

    pnl_sigma = (abs(pnl_gap) / pnl_std) if (pnl_std and pnl_std > 0) else float("inf")
    metrics = {
        "pnl_gap": float(pnl_gap),
        "wr_diff_pp": float(wr_diff_pp),
        "pnl_gap_sigma": float(pnl_sigma),
    }
    if use_or_logic:
        if pnl_sig_ok and wr_sig_ok:
            return True, "both", metrics
        if pnl_sig_ok:
            return True, "PnL_gap_only", metrics
        if wr_sig_ok:
            return True, "WR_gap_only", metrics
        return False, "below_threshold", metrics
    else:
        if pnl_sig_ok and wr_sig_ok:
            return True, "both", metrics
        return False, "below_threshold", metrics


def mine_patterns_relaxed(
    features_df: pd.DataFrame,
    wf_results_dir: str | Path,
    target_strategy: str,
    output_path: str | Path,
    pnl_sigma_threshold: float = 1.0,
    wr_pp_threshold: float = 5.0,
    use_or_logic: bool = True,
    filename_pattern: Optional[str] = None,
    prefix: str = "",
    min_n_per_coin: int = MIN_N_PER_COIN,
) -> list[dict]:
    """
    對 single strategy 做 single-feature mining，放寬門檻。
    寫 markdown 報告 + 回傳候選 filter list（dict）。
    """
    wf_dir = Path(wf_results_dir)
    df_strat = _build_strategy_df(wf_dir, features_df, target_strategy,
                                    prefix=prefix, filename_pattern=filename_pattern)
    if df_strat.empty:
        raise FileNotFoundError(
            f"No wf pickles for {target_strategy} in {wf_dir} "
            f"(pattern={filename_pattern or 'default'})"
        )

    df_valid = df_strat[df_strat["n_trades"] >= min_n_per_coin].copy()
    n_valid = len(df_valid)
    pnl_std = float(df_valid["total_pnl"].std()) if n_valid >= 2 else float("nan")
    overall_pnl = float(df_valid["total_pnl"].sum()) if n_valid else 0.0
    overall_wr = float(df_valid["win_rate"].median()) if n_valid else 0.0

    candidates: list[dict] = []
    feature_tables: dict[str, dict] = {}

    # 數值特徵
    for feat in NUMERIC_FEATS:
        if feat not in df_valid.columns:
            continue
        tab, attrs = _tier_summary_for_feature(df_valid, feat)
        feature_tables[feat] = {"tab": tab, **attrs}
        try:
            lo = tab[tab["_tier"] == "low"].iloc[0]
            hi = tab[tab["_tier"] == "high"].iloc[0]
        except IndexError:
            continue
        is_sig, reason, mets = _is_signal_relaxed(
            top_pnl=hi["median_pnl"], bot_pnl=lo["median_pnl"],
            top_wr=hi["median_wr"], bot_wr=lo["median_wr"],
            pnl_std=pnl_std,
            pnl_sigma_threshold=pnl_sigma_threshold,
            wr_pp_threshold=wr_pp_threshold,
            use_or_logic=use_or_logic,
        )
        if not is_sig:
            continue
        better_tier = "high" if hi["median_pnl"] >= lo["median_pnl"] else "low"
        # 推薦 threshold：好 tier 的入口邊界
        threshold = attrs["p66"] if better_tier == "high" else attrs["p33"]
        rule_type = "min" if better_tier == "high" else "max"
        candidates.append({
            "strategy": target_strategy,
            "feature": feat,
            "rule_type": rule_type,
            "op": ">=" if rule_type == "min" else "<=",
            "threshold": float(threshold),
            "tier_low_pnl": float(lo["median_pnl"]),
            "tier_high_pnl": float(hi["median_pnl"]),
            "tier_low_wr": float(lo["median_wr"]),
            "tier_high_wr": float(hi["median_wr"]),
            "wr_diff_pp": mets["wr_diff_pp"] if better_tier == "high"
                          else -mets["wr_diff_pp"],
            "pnl_gap_sigma": mets["pnl_gap_sigma"],
            "trigger_reason": reason,
            "better_tier": better_tier,
            "p33": attrs["p33"],
            "p66": attrs["p66"],
        })

    # 類別特徵
    for cat in CATEGORICAL_FEATS:
        if cat not in df_valid.columns:
            continue
        df_c = df_valid.copy()
        df_c["_tier"] = df_c[cat]
        tab_c = df_c.groupby("_tier").agg(
            n_coins=("symbol", "count"),
            trades_total=("n_trades", "sum"),
            median_pnl=("total_pnl", "median"),
            median_wr=("win_rate", "median"),
        ).reset_index()
        feature_tables[cat] = {"tab": tab_c}
        if len(tab_c) < 2:
            continue
        tab_sorted = tab_c.sort_values("median_pnl")
        lo = tab_sorted.iloc[0]
        hi = tab_sorted.iloc[-1]
        is_sig, reason, mets = _is_signal_relaxed(
            top_pnl=hi["median_pnl"], bot_pnl=lo["median_pnl"],
            top_wr=hi["median_wr"], bot_wr=lo["median_wr"],
            pnl_std=pnl_std,
            pnl_sigma_threshold=pnl_sigma_threshold,
            wr_pp_threshold=wr_pp_threshold,
            use_or_logic=use_or_logic,
        )
        if not is_sig:
            continue
        # exclude worst class
        candidates.append({
            "strategy": target_strategy,
            "feature": cat,
            "rule_type": "exclude",
            "op": "not_in",
            "threshold": [str(lo["_tier"])],  # exclude this class
            "include_class": str(hi["_tier"]),
            "tier_low_pnl": float(lo["median_pnl"]),
            "tier_high_pnl": float(hi["median_pnl"]),
            "tier_low_wr": float(lo["median_wr"]),
            "tier_high_wr": float(hi["median_wr"]),
            "wr_diff_pp": mets["wr_diff_pp"],
            "pnl_gap_sigma": mets["pnl_gap_sigma"],
            "trigger_reason": reason,
            "better_tier": str(hi["_tier"]),
        })

    # 寫 markdown 報告
    _render_relaxed_report(
        target_strategy, df_strat, df_valid,
        feature_tables, candidates, output_path,
        pnl_sigma_threshold, wr_pp_threshold, use_or_logic,
    )
    return candidates


def _render_relaxed_report(strategy: str,
                            df_strat: pd.DataFrame,
                            df_valid: pd.DataFrame,
                            feature_tables: dict,
                            candidates: list[dict],
                            output_path: str | Path,
                            pnl_sigma_threshold: float,
                            wr_pp_threshold: float,
                            use_or_logic: bool) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pnl_std = df_valid["total_pnl"].std() if len(df_valid) >= 2 else float("nan")

    lines = []
    lines.append(f"# {strategy.upper()} Relaxed Mining (Direction A)\n")
    lines.append(f"_Generated: {ts}_\n")
    lines.append(f"_Source: {len(df_strat)} coins; {len(df_valid)} valid (n≥{MIN_N_PER_COIN})_\n")
    lines.append(f"_Threshold: PnL_gap > {pnl_sigma_threshold:.1f}σ "
                 f"({'OR' if use_or_logic else 'AND'}) "
                 f"WR diff > {wr_pp_threshold:.1f}pp; pnl_std={pnl_std:.2f}_\n\n")

    lines.append("## Per-coin baseline\n")
    cols = ["symbol", "n_trades", "win_rate", "total_pnl",
            "atr_pct_med", "adx_med", "btc_corr_30d", "asset_class"]
    have = [c for c in cols if c in df_valid.columns]
    lines.append("| " + " | ".join(have) + " |")
    lines.append("| " + " | ".join("---" for _ in have) + " |")
    for _, row in df_valid.iterrows():
        vals = []
        for c in have:
            v = row[c]
            if pd.isna(v):
                vals.append("—")
            elif c == "win_rate":
                vals.append(f"{v*100:.1f}%")
            elif c == "total_pnl":
                vals.append(f"{v:+.2f}")
            elif isinstance(v, float):
                vals.append(f"{v:.4f}".rstrip("0").rstrip("."))
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    lines.append("")

    lines.append("\n## Single-feature candidates (Direction A)\n")
    for feat, info in feature_tables.items():
        tab = info["tab"]
        lines.append(f"\n### Feature: `{feat}`\n")
        cols = list(tab.columns)
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("| " + " | ".join("---" for _ in cols) + " |")
        for _, row in tab.iterrows():
            vals = []
            for c in cols:
                v = row[c]
                if pd.isna(v):
                    vals.append("—")
                elif c == "median_wr":
                    vals.append(f"{v*100:.1f}%")
                elif c == "median_pnl":
                    vals.append(f"{v:+.2f}")
                elif isinstance(v, float):
                    vals.append(f"{v:.4f}".rstrip("0").rstrip("."))
                else:
                    vals.append(str(v))
            lines.append("| " + " | ".join(vals) + " |")

        # 找這個 feature 的 candidate
        match = [c for c in candidates if c["feature"] == feat]
        if match:
            cand = match[0]
            if cand["rule_type"] == "exclude":
                lines.append(f"\n🎯 CANDIDATE: exclude `{cand['threshold'][0]}` "
                             f"(better=`{cand['include_class']}`，trigger={cand['trigger_reason']})")
            else:
                t_str = f"{cand['threshold']:.4f}".rstrip("0").rstrip(".")
                lines.append(f"\n🎯 CANDIDATE: `{feat} {cand['op']} {t_str}` "
                             f"(trigger={cand['trigger_reason']}, "
                             f"WR Δ={cand['wr_diff_pp']:+.1f}pp, "
                             f"PnL gap σ={cand['pnl_gap_sigma']:.2f})")

    lines.append("\n## All candidates summary\n")
    if not candidates:
        lines.append("\n_(沒有任何 candidate 觸發放寬門檻)_\n")
    else:
        lines.append("| # | Feature | Rule | Threshold | Trigger | WR Δ | PnL σ |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for i, c in enumerate(candidates, 1):
            if c["rule_type"] == "exclude":
                rule = f"not_in"
                t = ",".join(c["threshold"])
            else:
                rule = c["op"]
                t = f"{c['threshold']:.4f}".rstrip("0").rstrip(".")
            lines.append(
                f"| {i} | {c['feature']} | {rule} | {t} | "
                f"{c['trigger_reason']} | {c['wr_diff_pp']:+.1f}pp | "
                f"{c['pnl_gap_sigma']:.2f} |"
            )

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


# ═══════════════════════════════════════════════════════════════
# 方向 B：2-feature combo mining
# ═══════════════════════════════════════════════════════════════
def _binary_split(df: pd.DataFrame, feature: str) -> tuple[pd.Series, float]:
    """中位數切 binary。回傳 (label_series ∈ {"low","high","na"}, threshold)。"""
    vals = df[feature].dropna()
    if len(vals) < 2:
        return pd.Series(["na"] * len(df), index=df.index), float("nan")
    med = float(vals.median())

    def tag(v):
        if pd.isna(v):
            return "na"
        return "low" if v <= med else "high"

    return df[feature].map(tag), med


def mine_patterns_2feature(
    features_df: pd.DataFrame,
    wf_results_dir: str | Path,
    target_strategy: str,
    output_path: str | Path,
    min_cell_coins: int = 3,
    min_cell_trades: int = 30,
    wr_diff_pp_threshold: float = 8.0,
    pnl_sigma_threshold: float = 1.0,
    filename_pattern: Optional[str] = None,
    prefix: str = "",
) -> list[dict]:
    """
    對所有 (feat_i, feat_j) 組合做 2D mining（binary × binary 4-cell + asset_class × binary）。
    """
    wf_dir = Path(wf_results_dir)
    df_strat, _trades = _build_strategy_trades_df(
        wf_dir, features_df, target_strategy,
        filename_pattern=filename_pattern, prefix=prefix,
    )
    if df_strat.empty:
        raise FileNotFoundError(
            f"No wf pickles for {target_strategy} in {wf_dir}"
        )
    # 不再過濾 n_trades ≥ MIN_N_PER_COIN（cell 用自己的 trades_total 約束）
    df = df_strat.copy()

    candidates: list[dict] = []
    pair_tables: list[dict] = []

    feats = [f for f in NUMERIC_FEATS if f in df.columns]

    # numeric × numeric pairs
    from itertools import combinations
    for f_i, f_j in combinations(feats, 2):
        tag_i, med_i = _binary_split(df, f_i)
        tag_j, med_j = _binary_split(df, f_j)
        if math_isnan(med_i) or math_isnan(med_j):
            continue
        df_pair = df.assign(_ti=tag_i, _tj=tag_j)
        cells = df_pair.groupby(["_ti", "_tj"]).agg(
            n_coins=("symbol", "count"),
            trades_total=("n_trades", "sum"),
            median_pnl=("total_pnl", "median"),
            median_wr=("win_rate", "median"),
            sym_list=("symbol", lambda s: ",".join(sorted(s))),
        ).reset_index()

        cand, summary = _eval_2d_cells(
            cells, target_strategy, f_i, f_j,
            f_i_thresh=med_i, f_j_thresh=med_j,
            min_cell_coins=min_cell_coins,
            min_cell_trades=min_cell_trades,
            wr_diff_pp_threshold=wr_diff_pp_threshold,
            pnl_sigma_threshold=pnl_sigma_threshold,
            cat_i=False, cat_j=False,
        )
        pair_tables.append({
            "feat_i": f_i, "feat_j": f_j, "cells": cells, "summary": summary,
            "med_i": med_i, "med_j": med_j,
        })
        if cand:
            candidates.append(cand)

    # asset_class × numeric pairs
    if "asset_class" in df.columns:
        for f_j in feats:
            tag_j, med_j = _binary_split(df, f_j)
            if math_isnan(med_j):
                continue
            df_pair = df.assign(_ti=df["asset_class"], _tj=tag_j)
            cells = df_pair.groupby(["_ti", "_tj"]).agg(
                n_coins=("symbol", "count"),
                trades_total=("n_trades", "sum"),
                median_pnl=("total_pnl", "median"),
                median_wr=("win_rate", "median"),
                sym_list=("symbol", lambda s: ",".join(sorted(s))),
            ).reset_index()
            cand, summary = _eval_2d_cells(
                cells, target_strategy, "asset_class", f_j,
                f_i_thresh=None, f_j_thresh=med_j,
                min_cell_coins=min_cell_coins,
                min_cell_trades=min_cell_trades,
                wr_diff_pp_threshold=wr_diff_pp_threshold,
                pnl_sigma_threshold=pnl_sigma_threshold,
                cat_i=True, cat_j=False,
            )
            pair_tables.append({
                "feat_i": "asset_class", "feat_j": f_j,
                "cells": cells, "summary": summary,
                "med_i": None, "med_j": med_j,
            })
            if cand:
                candidates.append(cand)

    _render_2feature_report(
        target_strategy, df_strat, pair_tables, candidates, output_path,
        min_cell_coins, min_cell_trades,
        wr_diff_pp_threshold, pnl_sigma_threshold,
    )
    return candidates


def math_isnan(v) -> bool:
    try:
        import math
        return isinstance(v, float) and math.isnan(v)
    except Exception:
        return False


def _eval_2d_cells(cells: pd.DataFrame,
                    strategy: str, f_i: str, f_j: str,
                    f_i_thresh, f_j_thresh,
                    min_cell_coins: int, min_cell_trades: int,
                    wr_diff_pp_threshold: float, pnl_sigma_threshold: float,
                    cat_i: bool, cat_j: bool) -> tuple[Optional[dict], dict]:
    """找最佳 cell。"""
    valid = cells[
        (cells["n_coins"] >= min_cell_coins) &
        (cells["trades_total"] >= min_cell_trades)
    ].copy()
    if valid.empty:
        return None, {"reason": "no_valid_cell"}

    # 對所有有效 cell 算 PnL std（給 sigma 比較用）
    pnl_vals = valid["median_pnl"].values
    pnl_std = float(np.std(pnl_vals)) if len(pnl_vals) >= 2 else float("nan")

    # 找最高 PnL 且 PnL > 0 的 cell
    best_idx = valid["median_pnl"].idxmax()
    best = valid.loc[best_idx]
    if best["median_pnl"] <= 0:
        return None, {"reason": "best_cell_not_positive"}

    # 與其他 cells 的中位 WR 比
    others = valid.drop(best_idx)
    if others.empty:
        return None, {"reason": "only_one_valid_cell"}

    other_wr_med = float(others["median_wr"].median())
    other_pnl_med = float(others["median_pnl"].median())
    wr_diff_pp = (best["median_wr"] - other_wr_med) * 100
    pnl_excess = best["median_pnl"] - other_pnl_med
    pnl_excess_sigma = (pnl_excess / pnl_std) if pnl_std and pnl_std > 0 else float("inf")

    summary = {
        "best_cell": (best["_ti"], best["_tj"]),
        "best_wr": float(best["median_wr"]),
        "best_pnl": float(best["median_pnl"]),
        "best_n_trades": int(best["trades_total"]),
        "best_n_coins": int(best["n_coins"]),
        "best_syms": str(best["sym_list"]),
        "wr_diff_pp": float(wr_diff_pp),
        "pnl_excess_sigma": float(pnl_excess_sigma),
        "other_wr_med": float(other_wr_med),
        "other_pnl_med": float(other_pnl_med),
    }

    is_sig = (wr_diff_pp > wr_diff_pp_threshold and
              pnl_excess_sigma > pnl_sigma_threshold)
    if not is_sig:
        summary["reason"] = (f"below_threshold(wr_diff={wr_diff_pp:.1f}pp, "
                              f"pnl_excess_sigma={pnl_excess_sigma:.2f})")
        return None, summary

    cand = {
        "strategy": strategy,
        "feature_i": f_i,
        "feature_j": f_j,
        "best_quadrant": f"{best['_ti']}_{best['_tj']}",
        "rule_i": _make_rule_part(f_i, best["_ti"], f_i_thresh, cat_i),
        "rule_j": _make_rule_part(f_j, best["_tj"], f_j_thresh, cat_j),
        "coins_in_quadrant": str(best["sym_list"]).split(","),
        "n_trades": int(best["trades_total"]),
        "wr": float(best["median_wr"]),
        "pnl_med": float(best["median_pnl"]),
        "wr_diff_vs_other_quadrants": float(wr_diff_pp),
        "pnl_excess_sigma": float(pnl_excess_sigma),
        "trigger_reason": "both",
    }
    return cand, summary


def _make_rule_part(feat: str, tag: str, threshold,
                    is_categorical: bool) -> dict:
    """從 (tag, threshold) 推算對應 rule。"""
    if is_categorical:
        return {"feature": feat, "op": "==", "threshold": str(tag)}
    op = ">=" if tag == "high" else "<="
    return {"feature": feat, "op": op, "threshold": float(threshold)}


def _render_2feature_report(strategy: str,
                             df_strat: pd.DataFrame,
                             pair_tables: list[dict],
                             candidates: list[dict],
                             output_path: str | Path,
                             min_cell_coins: int, min_cell_trades: int,
                             wr_diff_pp_threshold: float,
                             pnl_sigma_threshold: float) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = []
    lines.append(f"# {strategy.upper()} 2-Feature Combo Mining (Direction B)\n")
    lines.append(f"_Generated: {ts}_\n")
    lines.append(f"_Source: {len(df_strat)} coins_\n")
    lines.append(f"_Cell threshold: ≥{min_cell_coins} coins AND ≥{min_cell_trades} trades; "
                 f"signal: WR diff > {wr_diff_pp_threshold:.0f}pp AND PnL excess > "
                 f"{pnl_sigma_threshold:.1f}σ_\n\n")

    # 只展示有 candidate 的配對 + 「值得看的」configs
    sig_pairs = [p for p in pair_tables
                  if any(c["feature_i"] == p["feat_i"] and c["feature_j"] == p["feat_j"]
                          for c in candidates)]
    lines.append(f"## Top combinations ({len(sig_pairs)} 顯著 / "
                 f"{len(pair_tables)} pair total)\n")

    if sig_pairs:
        for p in sig_pairs:
            lines.append(f"\n### `{p['feat_i']}` × `{p['feat_j']}`\n")
            cells = p["cells"]
            cols = ["_ti", "_tj", "n_coins", "trades_total",
                    "median_wr", "median_pnl", "sym_list"]
            lines.append("| " + " | ".join(cols) + " |")
            lines.append("| " + " | ".join("---" for _ in cols) + " |")
            for _, row in cells.iterrows():
                vals = []
                for c in cols:
                    v = row[c]
                    if pd.isna(v):
                        vals.append("—")
                    elif c == "median_wr":
                        vals.append(f"{v*100:.1f}%")
                    elif c == "median_pnl":
                        vals.append(f"{v:+.2f}")
                    elif isinstance(v, float):
                        vals.append(f"{v:.4f}".rstrip("0").rstrip("."))
                    else:
                        vals.append(str(v))
                lines.append("| " + " | ".join(vals) + " |")
            cand_match = [c for c in candidates
                           if c["feature_i"] == p["feat_i"] and c["feature_j"] == p["feat_j"]]
            if cand_match:
                cd = cand_match[0]
                lines.append(f"\n🎯 CANDIDATE: `{strategy.upper()}` requires "
                             f"({cd['rule_i']['feature']} {cd['rule_i']['op']} "
                             f"{cd['rule_i']['threshold']} AND "
                             f"{cd['rule_j']['feature']} {cd['rule_j']['op']} "
                             f"{cd['rule_j']['threshold']})\n"
                             f"   target quadrant coins: {', '.join(cd['coins_in_quadrant'])} "
                             f"(n={cd['n_trades']} trades, wr={cd['wr']*100:.1f}%, "
                             f"PnL/coin med={cd['pnl_med']:+.2f}U)")
    else:
        lines.append("\n_(沒有 2-feature combo 觸發顯著門檻)_\n")

    lines.append("\n## All candidates summary\n")
    if not candidates:
        lines.append("\n_(空)_")
    else:
        lines.append("| # | Pair | Quadrant | n_trades | WR | PnL/coin | WR Δ | PnL σ |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for i, c in enumerate(candidates, 1):
            lines.append(
                f"| {i} | {c['feature_i']} × {c['feature_j']} | "
                f"{c['best_quadrant']} | {c['n_trades']} | "
                f"{c['wr']*100:.1f}% | {c['pnl_med']:+.2f}U | "
                f"{c['wr_diff_vs_other_quadrants']:+.1f}pp | "
                f"{c['pnl_excess_sigma']:.2f} |"
            )

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


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
