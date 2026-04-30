"""
stability_audit.py — IS/OOS walk-forward stability audit for filter candidates

P2B-1 找到的 NKF / MR candidate filter 需要分段驗證：39m total PnL 看起來
變好可能是過擬合（某一段大爆發，其他段平或虧）。本 module 對單一 candidate
做 walk-forward N-segment 拆解 + 5 級狀態分類。

純驗證工具，不會改 .env、不會修改既有 filter rule，不會改策略 logic。

呼叫慣例（見 _run_p2b15_audit.py）：
    audit_candidate_stability(
        strategy="nkf",
        candidate_rules=[{"feature":"whipsaw_idx","op":"<=","threshold":0.121},
                         {"feature":"btc_corr_30d","op":">=","threshold":0.677}],
        rule_logic="AND",
        client=client,
        symbols=[...],
        months=39, n_segments=3,
    )
"""
import os
import sys
import json
import time
import pickle
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from wf_runner import run_walk_forward, _segment_metrics

log = logging.getLogger("stability_audit")

ROOT = Path(__file__).parent.parent


# ── status thresholds（對齊 SKILL.md 樣本 ≥ 30 鐵律）─────────
WR_STD_PP_REJECT = 10.0     # WR std > 10pp → REJECTED
WR_STD_PP_ROBUST = 5.0       # WR std < 5pp + 三段正 → ROBUST
MIN_N_TRADES_PER_SEG = 30    # robust 要求每段 ≥ 30
CONCENTRATION_OVERFIT = 0.70 # 單段 PnL 占正總和 > 70% → 集中
REGIME_MAGNITUDE_RATIO = 1.5 # 兩段正一段負 + 量級差 > 1.5x → regime-dependent


def compute_stability_metrics(segments: list[dict]) -> dict:
    """從 wf segments list 抽 stability metrics。"""
    pnls = [float(s["metrics"]["total_pnl"]) for s in segments]
    wrs = [float(s["metrics"]["win_rate"]) for s in segments]
    ns = [int(s["metrics"]["n_trades"]) for s in segments]

    pnl_std = float(np.std(pnls)) if len(pnls) >= 2 else 0.0
    pnl_mean = float(np.mean(pnls)) if pnls else 0.0
    pnl_consistency = max(0.0, 1.0 - pnl_std / max(abs(pnl_mean), 0.1))
    pnl_consistency = min(1.0, pnl_consistency)

    positive_total = sum(p for p in pnls if p > 0)
    concentration = (max(pnls) / positive_total) if positive_total > 1e-9 else (
        1.0 if any(p > 0 for p in pnls) else 0.0
    )
    sign_flips = sum(1 for i in range(1, len(pnls))
                     if (pnls[i] > 0) != (pnls[i - 1] > 0))

    return {
        "seg_pnls": pnls,
        "seg_wrs": wrs,
        "seg_ns": ns,
        "all_positive": all(p > 0 for p in pnls),
        "n_negative": sum(1 for p in pnls if p <= 0),
        "wr_std_pp": float(np.std(wrs)) * 100,
        "pnl_consistency": float(pnl_consistency),
        "min_n_trades": min(ns) if ns else 0,
        "max_n_trades": max(ns) if ns else 0,
        "sum_n_trades": sum(ns),
        "sign_flip_count": sign_flips,
        "concentration": float(concentration),
        "pnl_mean": pnl_mean,
        "pnl_std": pnl_std,
        "total_pnl": float(sum(pnls)),
    }


def classify_status(m: dict) -> tuple[str, str]:
    """5 級狀態分類，回傳 (status, why)。"""
    n_neg = m["n_negative"]
    wr_std = m["wr_std_pp"]
    min_n = m["min_n_trades"]
    conc = m["concentration"]

    # 1. REJECTED：太多負段或 WR 太抖
    if n_neg >= 2:
        return "REJECTED", f"{n_neg}/3 segments negative"
    if wr_std > WR_STD_PP_REJECT:
        return "REJECTED", f"wr_std={wr_std:.1f}pp > {WR_STD_PP_REJECT}pp"

    # 2. OVERFIT_SUSPECT：1 段大正、其他平或負，正 PnL 集中
    if n_neg == 1 and conc > CONCENTRATION_OVERFIT:
        return "OVERFIT_SUSPECT", (f"1 segment negative + concentration "
                                    f"{conc*100:.0f}% > {CONCENTRATION_OVERFIT*100:.0f}%")
    # 三段全正但其中兩段接近 0、一段大幅
    if n_neg == 0 and conc > CONCENTRATION_OVERFIT:
        return "OVERFIT_SUSPECT", (f"all positive but concentration "
                                    f"{conc*100:.0f}% > {CONCENTRATION_OVERFIT*100:.0f}%")

    # 3. ROBUST：三段全正 + WR std 低 + 樣本足
    if (n_neg == 0 and wr_std < WR_STD_PP_ROBUST
            and min_n >= MIN_N_TRADES_PER_SEG):
        return "ROBUST", (f"3/3 positive, wr_std={wr_std:.1f}pp, "
                           f"min_n={min_n}")

    # 4. STABLE_BUT_THIN：三段全正但樣本薄
    if n_neg == 0 and min_n < MIN_N_TRADES_PER_SEG:
        return "STABLE_BUT_THIN", (f"3/3 positive but min_n_trades={min_n} "
                                    f"< {MIN_N_TRADES_PER_SEG}")
    # 三段全正但 WR std 偏高
    if n_neg == 0 and wr_std >= WR_STD_PP_ROBUST:
        return "STABLE_BUT_THIN", (f"3/3 positive but wr_std={wr_std:.1f}pp "
                                    f">= {WR_STD_PP_ROBUST}pp")

    # 5. REGIME_DEPENDENT：1 段負 + 量級差距 > 1.5x
    if n_neg == 1:
        pos = [p for p in m["seg_pnls"] if p > 0]
        neg = [abs(p) for p in m["seg_pnls"] if p <= 0]
        if pos and neg:
            ratio = max(pos) / max(max(neg), 0.01)
            if ratio > REGIME_MAGNITUDE_RATIO:
                return "REGIME_DEPENDENT", (f"1 neg segment, magnitude ratio "
                                              f"{ratio:.1f}x > {REGIME_MAGNITUDE_RATIO}x")
        return "REGIME_DEPENDENT", "1 segment negative"

    return "REGIME_DEPENDENT", "fallback"


# ── 主函式 ──────────────────────────────────────────────────
def audit_candidate_stability(
    strategy: str,
    candidate_rules: list[dict],
    rule_logic: str,           # "AND" or "OR"
    client,
    symbols: list[str],
    fn,                          # backtest_fn (e.g. _nkf_wrap)
    months: int = 39,
    n_segments: int = 3,
    candidate_id: str = "x",
    candidate_label: str = "",
    output_dir: Optional[Path] = None,
) -> dict:
    """
    對一條 candidate filter 跑 wf × symbols × months × n_segments，
    回傳 metrics + status + 寫 markdown 報告。

    注意：filter 是透過 env vars + feature_filter 的「進場 fast skip」實作；
    wf_runner 的 segment 切法是在 trades 上做 open_time 分段（fn 跑完一次後切），
    所以兩者協作的方式是：fn 在 backtest 進場時被 filter 擋掉的 symbol → 0 trades；
    沒被擋的 symbol → 全期 trades，再被 wf_runner 按時間切 3 段。
    """
    output_dir = output_dir or (ROOT / "reports")
    output_dir.mkdir(parents=True, exist_ok=True)

    # 注入 env
    env_key_rules = f"{strategy.upper()}_RULES_JSON"
    env_key_req = f"{strategy.upper()}_REQUIRE_ALL"

    saved_env = {
        "BACKTEST_USE_FEATURE_FILTERS": os.environ.get("BACKTEST_USE_FEATURE_FILTERS"),
        env_key_rules: os.environ.get(env_key_rules),
        env_key_req: os.environ.get(env_key_req),
    }
    try:
        if not candidate_rules:
            # baseline：強制關 filter
            os.environ["BACKTEST_USE_FEATURE_FILTERS"] = "false"
            os.environ.pop(env_key_rules, None)
            os.environ.pop(env_key_req, None)
        else:
            os.environ["BACKTEST_USE_FEATURE_FILTERS"] = "true"
            os.environ[env_key_rules] = json.dumps(candidate_rules)
            os.environ[env_key_req] = "true" if rule_logic.upper() == "AND" else "false"

        # 對 strategy × all symbols 跑 wf
        wf = run_walk_forward(
            fn, client, symbols, months, n_segments=n_segments,
            config_label=f"audit_{strategy}_{candidate_id}_{int(time.time())}",
        )
    finally:
        # restore env
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # 把 wf pickle 移走（避免污染主 cache 結構）
    try:
        Path(wf["_pickle_path"]).unlink(missing_ok=True)
    except Exception:
        pass

    # 計 stability
    metrics = compute_stability_metrics(wf["segments"])
    status, why = classify_status(metrics)

    # stability-adjusted PnL
    n_factor = 1.0 if metrics["min_n_trades"] >= MIN_N_TRADES_PER_SEG else 0.5
    adjusted_pnl = metrics["total_pnl"] * metrics["pnl_consistency"] * n_factor

    result = {
        "strategy": strategy,
        "candidate_id": candidate_id,
        "candidate_label": candidate_label,
        "candidate_rules": candidate_rules,
        "rule_logic": rule_logic,
        "metrics": metrics,
        "status": status,
        "status_reason": why,
        "stability_adjusted_pnl": float(adjusted_pnl),
        "by_coin": wf.get("by_coin", {}),
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    _render_audit_report(result, output_dir)
    return result


def _render_audit_report(result: dict, output_dir: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"audit_{result['strategy']}_{result['candidate_id']}_{ts}.md"
    out = output_dir / fname
    m = result["metrics"]

    lines = []
    lines.append(f"# Audit: {result['strategy'].upper()} candidate `{result['candidate_id']}`\n")
    lines.append(f"_Generated: {result['ts']}_\n")
    lines.append(f"\n**Candidate**: `{result['candidate_label']}`")
    lines.append(f"\n**Logic**: {result['rule_logic']}")
    lines.append(f"\n**Rules JSON**:")
    lines.append(f"```json\n{json.dumps(result['candidate_rules'], indent=2)}\n```\n")
    lines.append(f"## Status: **{result['status']}**\n")
    lines.append(f"_{result['status_reason']}_\n")

    lines.append("\n## Per-segment metrics\n")
    lines.append("| Segment | n_trades | win_rate | total_pnl |")
    lines.append("| --- | --- | --- | --- |")
    for i, (n, wr, p) in enumerate(zip(m["seg_ns"], m["seg_wrs"], m["seg_pnls"]), 1):
        lines.append(f"| seg{i} | {n} | {wr*100:.1f}% | {p:+.2f}U |")
    lines.append(f"| **total** | **{m['sum_n_trades']}** | "
                 f"**{m['pnl_mean']:+.2f}U avg** | **{m['total_pnl']:+.2f}U** |")

    lines.append("\n## Stability metrics\n")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| all_positive | {m['all_positive']} |")
    lines.append(f"| n_negative_segments | {m['n_negative']} / 3 |")
    lines.append(f"| wr_std (pp) | {m['wr_std_pp']:.2f} |")
    lines.append(f"| pnl_consistency | {m['pnl_consistency']:.3f} |")
    lines.append(f"| min_n_trades | {m['min_n_trades']} |")
    lines.append(f"| sign_flip_count | {m['sign_flip_count']} |")
    lines.append(f"| concentration | {m['concentration']*100:.1f}% |")
    lines.append(f"| stability_adjusted_pnl | {result['stability_adjusted_pnl']:+.2f}U |")

    if result.get("by_coin"):
        lines.append("\n## Per-coin (only kept coins have non-zero rows)\n")
        lines.append("| symbol | n_trades | win_rate | total_pnl |")
        lines.append("| --- | --- | --- | --- |")
        for sym, c in result["by_coin"].items():
            cm = c.get("metrics", {})
            n = cm.get("n_trades", 0)
            if n == 0:
                continue
            lines.append(f"| {sym} | {n} | {cm.get('win_rate', 0)*100:.1f}% | "
                         f"{cm.get('total_pnl', 0):+.2f}U |")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out
