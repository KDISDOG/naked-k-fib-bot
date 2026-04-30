"""
P4 / Task 2：MASR coordinate-descent sweep over 4 entry/exit params。

Param 對應（用戶腳本指定的別名 → 實際 config key）：
  MASR_SR_LOOKBACK        → MASR_RES_LOOKBACK   (找阻力位回看根數，default 100)
  MASR_BREAKOUT_ATR_MULT  → MASR_RES_TOL_ATR_MULT (價格貼近阻力位的 ATR 容忍倍數，default 0.3)
  MASR_TP1_RR             → MASR_TP1_RR (TP1 reward:risk，default 2.0)
  MASR_SL_ATR_MULT        → MASR_SL_ATR_MULT (SL 距離 entry 的 ATR 倍數，default 1.5)

Grid 設計圍繞 baseline（保持向上向下對稱），避免 sweep 完全偏離既有調校。

跑前先 warm fetch_klines（39m × 4h × 10 幣，所有 cached pkl 已在 P1 期建立 →
應 instant hit；做一次預檢避免 sweep 中段才打 API）。

結果存 .cache/sweep_results/run_backtest_masr_<ts>.json （sweep_runner 自動寫）+
.cache/sweep_top3_<ts>.pkl 給 task 3 audit 用。
"""
import os
import sys
import json
import time
import pickle
from pathlib import Path
from datetime import datetime
from binance.client import Client
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

# 進 import 前清 filter env，sweep 期間不啟用任何 filter（純測 MASR config）
for k in ("BACKTEST_USE_FEATURE_FILTERS",
          "MASR_RULES_JSON", "MASR_REQUIRE_ALL", "MASR_EXCLUDE_ASSET_CLASSES"):
    os.environ.pop(k, None)

from backtest import run_backtest_masr, fetch_klines
from sweep_runner import (
    coordinate_descent_sweep,
    default_objective_winrate_focused,
)
from config import Config

ROOT = Path(__file__).parent.parent

ACTIVE_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "1000PEPEUSDT", "SKYAIUSDT", "XAUUSDT", "XAGUSDT", "CLUSDT",
]
MONTHS = 39

# 別名 → 實際 config key
PARAM_ALIAS = {
    "MASR_SR_LOOKBACK":       "MASR_RES_LOOKBACK",
    "MASR_BREAKOUT_ATR_MULT": "MASR_RES_TOL_ATR_MULT",
    "MASR_TP1_RR":            "MASR_TP1_RR",
    "MASR_SL_ATR_MULT":       "MASR_SL_ATR_MULT",
}

# Grid（圍繞 baseline 對稱）
PARAM_GRID = {
    "MASR_RES_LOOKBACK":       [50, 75, 100, 125, 150],
    "MASR_RES_TOL_ATR_MULT":   [0.20, 0.30, 0.40, 0.50, 0.70],
    "MASR_TP1_RR":             [1.5, 2.0, 2.5, 3.0, 3.5],
    "MASR_SL_ATR_MULT":        [1.0, 1.5, 2.0, 2.5, 3.0],
}


def load_baseline_from_config() -> dict:
    """讀目前 Config 值當 baseline（背後可能來自 .env 或 default）。"""
    return {
        key: getattr(Config, key) for key in PARAM_GRID.keys()
    }


def warm_kline_cache(client) -> None:
    """確保 4h × 10 幣 + 1d 都已 disk cached。"""
    print("=" * 78)
    print(" Warming kline cache (4h + 1d × 10 coins)")
    print("=" * 78)
    tf = Config.MASR_TIMEFRAME
    for sym in ACTIVE_SYMBOLS:
        try:
            df = fetch_klines(client, sym, tf, MONTHS)
            print(f"  [{sym}] {tf}: {len(df)} bars")
        except Exception as e:
            print(f"  [{sym}] {tf} 失敗：{e}")
    # MASR 可能也讀 1d（screen / 規則需要）— 不確定但保險
    try:
        df = fetch_klines(client, "BTCUSDT", "1d", MONTHS + 1)
        print(f"  [BTCUSDT] 1d: {len(df)} bars")
    except Exception:
        pass


def main():
    client = Client(os.getenv("BINANCE_API_KEY", ""),
                    os.getenv("BINANCE_SECRET", ""), testnet=False)
    warm_kline_cache(client)

    baseline = load_baseline_from_config()
    print(f"\nBaseline (current Config): {baseline}")
    print(f"\nParam grid:")
    for k, v in PARAM_GRID.items():
        print(f"  {k}: {v}")

    print("\n" + "=" * 78)
    print(" Coordinate descent sweep start")
    print("=" * 78)
    result = coordinate_descent_sweep(
        backtest_fn=run_backtest_masr,
        client=client,
        symbols=ACTIVE_SYMBOLS,
        months=MONTHS,
        param_grid=PARAM_GRID,
        baseline_config=baseline,
        objective=default_objective_winrate_focused,
        max_iters=3,
        n_segments=3,
    )

    # Top-3 raw 收集（從 history 抽全部試過的 config + score）
    all_evaluated = []
    for h in result["history"]:
        for entry in h["param_log"]:
            param = entry["param"]
            for c in entry["candidates"]:
                if c["score"] is None:
                    continue
                # 構造 trial config（替換該 param 為候選值，其他用 iter 開頭的 config）
                # 為簡化，記錄 score + 該 candidate 的 (param, value)，rebuild config 由我們做
                all_evaluated.append({
                    "iter": h["iter"],
                    "param_swept": param,
                    "value": c["value"],
                    "score": c["score"],
                    "n_trades": c["n_trades"],
                    "win_rate": c["win_rate"],
                    "config_at_eval": _config_at_eval(h, param, c["value"]),
                })

    # Dedupe by config tuple，取每個 unique config 的最高 score
    seen: dict[tuple, dict] = {}
    for e in all_evaluated:
        key = tuple(sorted(e["config_at_eval"].items()))
        if key not in seen or e["score"] > seen[key]["score"]:
            seen[key] = e
    deduped = list(seen.values())
    deduped.sort(key=lambda x: -x["score"])

    # 加 baseline 跑一次拿 score（為了排名比較）
    print("\n" + "=" * 78)
    print(" Re-eval baseline (for ranking)")
    print("=" * 78)
    from wf_runner import run_walk_forward, _segment_metrics
    wf_base = run_walk_forward(
        run_backtest_masr, client, ACTIVE_SYMBOLS, MONTHS,
        n_segments=3, config_overrides=baseline, config_label="baseline_eval",
    )
    base_trades = [t for s in wf_base["segments"] for t in s["trades"]]
    base_metrics = _segment_metrics(base_trades)
    base_score = default_objective_winrate_focused(base_metrics)
    base_total = base_metrics["total_pnl"]

    # 把 baseline 也加進 deduped（特殊 marker）
    baseline_entry = {
        "iter": 0,
        "param_swept": "baseline",
        "value": "(baseline)",
        "score": base_score,
        "n_trades": base_metrics["n_trades"],
        "win_rate": base_metrics["win_rate"],
        "total_pnl": base_total,
        "config_at_eval": dict(baseline),
        "is_baseline": True,
    }
    # 對 deduped 補 total_pnl（剛才沒記）
    print("\n[補算 top configs 的 total_pnl]")
    for e in deduped[:5]:  # 前 5 重跑算 total_pnl
        wf = run_walk_forward(
            run_backtest_masr, client, ACTIVE_SYMBOLS, MONTHS,
            n_segments=3, config_overrides=e["config_at_eval"],
            config_label=f"reeval_{int(time.time())}",
        )
        tt = [t for s in wf["segments"] for t in s["trades"]]
        m = _segment_metrics(tt)
        e["total_pnl"] = m["total_pnl"]
        e["wf"] = wf  # 保留給 audit
        # cleanup
        try:
            Path(wf["_pickle_path"]).unlink(missing_ok=True)
        except Exception:
            pass
        print(f"  cfg={e['config_at_eval']}  total={m['total_pnl']:+.2f}U  "
              f"score={e['score']:.4f}")

    # 打印 baseline + top 5
    print("\n" + "=" * 78)
    print(" Top 5 (raw, by win-rate-focused score) + baseline")
    print("=" * 78)
    print(f"  baseline  score={base_score:.4f}  n={base_metrics['n_trades']}  "
          f"wr={base_metrics['win_rate']*100:.1f}%  total={base_total:+.2f}U")
    for i, e in enumerate(deduped[:5], 1):
        print(f"  #{i}  score={e['score']:.4f}  n={e['n_trades']}  "
              f"wr={e['win_rate']*100:.1f}%  total={e.get('total_pnl', 0):+.2f}U")
        print(f"      cfg: {e['config_at_eval']}")

    # 落 pickle 給 task 3 audit 用
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_pkl = ROOT / ".cache" / f"masr_sweep_top3_{ts}.pkl"
    payload = {
        "baseline": baseline_entry,
        "top3": deduped[:3],   # 前三 unique config
        "best_config": result["best_config"],
        "json_path": result["_json_path"],
        "ts": ts,
    }
    # 保存前刪 wf object（pickle 不需要）
    for e in [payload["baseline"]] + payload["top3"]:
        e.pop("wf", None)
    with open(out_pkl, "wb") as fh:
        pickle.dump(payload, fh)
    print(f"\n[saved] {out_pkl}")
    print(f"[json] {result['_json_path']}")
    print("\nEXIT=0")


def _config_at_eval(history_iter: dict, swept_param: str, value) -> dict:
    """還原這個 candidate 評估時的完整 config。
    history_iter: history[i] = {"iter":, "config":(end-of-iter snapshot), "param_log": [...]}
    在 iter 內 sweep 是順序進行的，但簡化：用 iter 結束的 config 把 swept_param 換成試的 value。
    這對 coordinate descent 是準確的（每個 candidate eval 時，其他參數都 == 該 iter 結束 config）。
    """
    cfg = dict(history_iter["config"])
    cfg[swept_param] = value
    return cfg


if __name__ == "__main__":
    main()
