"""
sweep_runner.py — Coordinate descent 參數 sweep

從 baseline 開始，每 iteration 對每個參數獨立掃所有候選值，
選最佳值固定下來。共 max_iters 輪收斂。

預設 objective 偏向 win_rate 但用樣本量 sqrt 加權，避免 < 30 筆瞎拼。
"""
import os
import sys
import json
import time
import logging
import inspect
from pathlib import Path
from datetime import datetime
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).parent))
from config import Config
# Re-export ConfigPatch from wf_runner（同一個 implementation 共用）
from wf_runner import ConfigPatch, run_walk_forward

log = logging.getLogger("sweep_runner")


def default_objective_winrate_focused(metrics: dict) -> float:
    """
    SKILL.md 第三鐵律：樣本 < 30 不下結論。
    score = win_rate × sqrt(min(n, 60) / 30)
    n 從 30 開始給滿分權重，60 封頂避免單純訊號量灌水。
    """
    n = int(metrics.get("n_trades", 0))
    if n < 30:
        return float("-inf")
    win_rate = float(metrics.get("win_rate", 0.0))
    weight = (min(n, 60) / 30.0) ** 0.5
    return win_rate * weight


def _flatten_segments(wf_result: dict) -> dict:
    """合併 walk-forward 結果的全段 trades 算 overall metrics（給 objective 吃）"""
    from wf_runner import _segment_metrics
    all_trades = []
    for seg in wf_result["segments"]:
        all_trades.extend(seg["trades"])
    return _segment_metrics(all_trades)


def _is_better(a: float, b: float, eps: float = 1e-9) -> bool:
    """a > b 嚴格優於"""
    if a == float("-inf") and b == float("-inf"):
        return False
    return a > b + eps


def coordinate_descent_sweep(
    backtest_fn: Callable,
    client,
    symbols: list[str],
    months: int,
    param_grid: dict[str, list],
    baseline_config: Optional[dict] = None,
    objective: Optional[Callable] = None,
    max_iters: int = 3,
    n_segments: int = 3,
) -> dict:
    """
    Coordinate descent over param_grid keys，每輪固定其他參數依序掃單一參數。

    每個候選值跑一次 walk-forward → 取整體 metrics 算 objective。
    記錄 IS（segment 0）vs OOS（segments 1..n-1）排名差異 → flag overfit risk。
    """
    objective = objective or default_objective_winrate_focused
    baseline_config = dict(baseline_config or {})
    fn_name = backtest_fn.__name__

    # 收斂歷史
    history: list[dict] = []
    current_config = dict(baseline_config)

    print(f"=== Sweep Result: {fn_name} ===")
    print(f"baseline_config: {current_config}")
    print(f"param_grid: {param_grid}")

    for it in range(1, max_iters + 1):
        print(f"\nIter {it}:")
        iter_changed = False
        iter_log: list[dict] = []

        for param, candidates in param_grid.items():
            best_val = current_config.get(param)
            best_score = float("-inf")
            best_metrics = None
            best_wf = None
            cand_results = []

            for v in candidates:
                trial_config = dict(current_config)
                trial_config[param] = v
                wf = run_walk_forward(
                    backtest_fn, client, symbols, months,
                    n_segments=n_segments,
                    config_overrides=trial_config,
                    config_label=f"sweep_iter{it}_{param}_{v}",
                )
                metrics = _flatten_segments(wf)
                score = objective(metrics)
                cand_results.append({
                    "value": v, "score": round(score, 4) if score != float("-inf") else None,
                    "n_trades": metrics["n_trades"],
                    "win_rate": metrics["win_rate"],
                    "wf": wf,
                })
                marker = ""
                if _is_better(score, best_score):
                    best_score = score
                    best_val = v
                    best_metrics = metrics
                    best_wf = wf

            # 列印該參數的所有候選值結果
            for c in cand_results:
                marker = " ← BEST" if c["value"] == best_val else ""
                score_str = (f"{c['score']:.4f}" if c["score"] is not None
                              else "n<30")
                print(f"  {param}: {c['value']} -> "
                      f"score {score_str} (n={c['n_trades']}, "
                      f"wr={c['win_rate']*100:.1f}%){marker}")

            # 偵測 overfit risk：IS 排名 vs OOS（取 n_segments-1 段平均）
            overfit_flag = ""
            if best_wf and len(best_wf["segments"]) >= 2:
                # 對所有候選值算 IS 跟 OOS 排名
                is_scores = []
                oos_scores = []
                for c in cand_results:
                    is_seg = c["wf"]["segments"][0]["metrics"]
                    oos_segs = [s["metrics"] for s in c["wf"]["segments"][1:]]
                    is_score = objective(is_seg)
                    # OOS 平均（合併 trades 算 metric，更穩健）
                    from wf_runner import _segment_metrics
                    oos_trades = []
                    for s in c["wf"]["segments"][1:]:
                        oos_trades.extend(s["trades"])
                    oos_score = objective(_segment_metrics(oos_trades))
                    is_scores.append((c["value"], is_score))
                    oos_scores.append((c["value"], oos_score))
                is_rank = sorted(is_scores, key=lambda x: -x[1])
                oos_rank = sorted(oos_scores, key=lambda x: -x[1])
                is_top = is_rank[0][0] if is_rank else None
                oos_top = oos_rank[0][0] if oos_rank else None
                if is_top != oos_top and is_top is not None:
                    is_pos = next((i for i, (v, _) in enumerate(oos_rank) if v == is_top), None)
                    if is_pos is not None and is_pos > 0:
                        overfit_flag = f"  IS#1({is_top}) -> OOS#{is_pos+1} ⚠️ overfit risk"
                        print(overfit_flag)

            # 套用最佳值
            if best_val != current_config.get(param):
                current_config[param] = best_val
                iter_changed = True
            iter_log.append({
                "param": param,
                "best_value": best_val,
                "best_score": best_score if best_score != float("-inf") else None,
                "candidates": [{k: v for k, v in c.items() if k != "wf"}
                               for c in cand_results],
                "overfit_flag": overfit_flag.strip() if overfit_flag else None,
            })

        history.append({"iter": it, "config": dict(current_config),
                        "param_log": iter_log})

        if not iter_changed:
            print(f"\n收斂於 iter {it}（無參數變動）")
            break

    print(f"\n最佳組合: {current_config}")

    # 寫入 JSON 報表
    out_dir = Path(__file__).parent.parent / ".cache" / "sweep_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{fn_name}_{int(time.time())}.json"
    payload = {
        "fn_name": fn_name,
        "symbols": symbols,
        "months": months,
        "param_grid": param_grid,
        "baseline_config": baseline_config,
        "best_config": current_config,
        "n_iters_run": len(history),
        "history": history,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2, ensure_ascii=False, default=str)
    print(f"[Sweep] saved → {out_path}")

    return {
        "best_config": current_config,
        "history": history,
        "_json_path": str(out_path),
    }


__all__ = [
    "coordinate_descent_sweep",
    "default_objective_winrate_focused",
    "ConfigPatch",  # 從 wf_runner re-export
]
