"""
P12C：MASR Short v2 — coordinate descent sweep over 4 entry/exit params。

variants slow + fast 各跑一次（共 2 個 sweep）。

PARAM_GRID:
  MASR_SHORT_RES_LOOKBACK     ∈ {50, 75, 100, 125, 150}
  MASR_SHORT_RES_TOL_ATR_MULT ∈ {0.2, 0.3, 0.4, 0.5}
  MASR_SHORT_TP1_RR           ∈ {1.5, 2.0, 2.5, 3.0}
  MASR_SHORT_SL_ATR_MULT      ∈ {1.5, 2.0, 2.5, 3.0}

baseline 取 .env.example 預設值。

run_backtest_masr_short_v2 沒接 config_overrides → wrapper + sweep_runner 走
ConfigPatch 路徑（monkey-patch Config class）。每 variant 用 wrapper 固定
variant 不變，只 sweep 4 個 entry/exit param。

cfd 排除關閉、MASR_SHORT_EXCLUDED_SYMBOLS 清空（純看 raw alpha）。
"""
import os
import sys
import time
import json
import pickle
from pathlib import Path
from datetime import datetime
from binance.client import Client
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

# 進 import 前清 env，cfd 不啟用 + 不排除避險資產
for k in ("BACKTEST_USE_FEATURE_FILTERS",
          "MASR_RULES_JSON", "MASR_REQUIRE_ALL", "MASR_EXCLUDE_ASSET_CLASSES",
          "MASR_SHORT_EXCLUDED_SYMBOLS"):
    os.environ.pop(k, None)
os.environ["BACKTEST_USE_FEATURE_FILTERS"] = "false"
os.environ["MASR_SHORT_EXCLUDED_SYMBOLS"] = ""

from backtest import run_backtest_masr_short_v2, fetch_klines
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

PARAM_GRID = {
    "MASR_SHORT_RES_LOOKBACK":     [50, 75, 100, 125, 150],
    "MASR_SHORT_RES_TOL_ATR_MULT": [0.20, 0.30, 0.40, 0.50],
    "MASR_SHORT_TP1_RR":           [1.5, 2.0, 2.5, 3.0],
    "MASR_SHORT_SL_ATR_MULT":      [1.5, 2.0, 2.5, 3.0],
}


def load_baseline_from_config() -> dict:
    return {key: getattr(Config, key) for key in PARAM_GRID.keys()}


def warm_kline_cache(client) -> None:
    print("=" * 78)
    print(" Warming kline cache (1H + 4H + 1D × 10 coins + BTC 4H/1D)")
    print("=" * 78)
    for sym in ACTIVE_SYMBOLS:
        for tf, m in (("1h", MONTHS), ("4h", MONTHS), ("1d", MONTHS + 1)):
            try:
                df = fetch_klines(client, sym, tf, m)
                print(f"  [{sym}] {tf}: {len(df)} bars")
            except Exception as e:
                print(f"  [{sym}] {tf} 失敗：{e}")
    for tf in ("4h", "1d"):
        try:
            fetch_klines(client, "BTCUSDT", tf, MONTHS + (1 if tf == "1d" else 0))
        except Exception:
            pass


def _make_v2_wrapper(variant: str):
    """Wrapper that fixes variant kwarg so sweep can swap config params."""
    def wrapper(client, symbol, months, debug=False, **kw):
        return run_backtest_masr_short_v2(
            client, symbol, months, debug=debug, variant=variant,
        )
    wrapper.__name__ = f"run_backtest_masr_short_v2_{variant}"
    return wrapper


def main():
    client = Client(os.getenv("BINANCE_API_KEY", ""),
                    os.getenv("BINANCE_SECRET", ""), testnet=False)
    warm_kline_cache(client)

    baseline = load_baseline_from_config()
    print(f"\nBaseline (current Config): {baseline}")
    print(f"\nParam grid:")
    for k, v in PARAM_GRID.items():
        print(f"  {k}: {v}")

    summary = {}
    for variant in ("slow", "fast"):
        print("\n" + "=" * 78)
        print(f" Coordinate descent sweep: variant={variant}")
        print("=" * 78)
        fn = _make_v2_wrapper(variant)
        result = coordinate_descent_sweep(
            backtest_fn=fn,
            client=client,
            symbols=ACTIVE_SYMBOLS,
            months=MONTHS,
            param_grid=PARAM_GRID,
            baseline_config=baseline,
            objective=default_objective_winrate_focused,
            max_iters=3,
            n_segments=3,
        )
        summary[variant] = {
            "best_config": result["best_config"],
            "json_path": result["_json_path"],
            "history": result["history"],
        }

    # Top-3 (by score) per variant from history
    from wf_runner import _segment_metrics
    from wf_runner import run_walk_forward
    print("\n" + "=" * 78)
    print(" Re-eval baseline + top-3 unique configs per variant for ranking")
    print("=" * 78)

    tops_per_variant: dict = {}
    for variant in ("slow", "fast"):
        all_evals = []
        for h in summary[variant]["history"]:
            for entry in h["param_log"]:
                param = entry["param"]
                for c in entry["candidates"]:
                    if c["score"] is None:
                        continue
                    cfg = dict(h["config"])
                    cfg[param] = c["value"]
                    all_evals.append({
                        "iter": h["iter"],
                        "param_swept": param,
                        "value": c["value"],
                        "score": c["score"],
                        "n_trades": c["n_trades"],
                        "win_rate": c["win_rate"],
                        "config": cfg,
                    })

        # Dedupe by config tuple
        seen: dict[tuple, dict] = {}
        for e in all_evals:
            key = tuple(sorted(e["config"].items()))
            if key not in seen or e["score"] > seen[key]["score"]:
                seen[key] = e
        deduped = sorted(seen.values(), key=lambda x: -x["score"])
        tops_per_variant[variant] = deduped[:3]

        # Reeval top 3 to get total_pnl + best_score
        fn = _make_v2_wrapper(variant)
        for e in tops_per_variant[variant]:
            wf = run_walk_forward(
                fn, client, ACTIVE_SYMBOLS, MONTHS,
                n_segments=3, config_overrides=e["config"],
                config_label=f"reeval_short_{variant}_{int(time.time())}",
            )
            tt = [t for s in wf["segments"] for t in s["trades"]]
            m = _segment_metrics(tt)
            e["total_pnl"] = m["total_pnl"]
            try:
                Path(wf["_pickle_path"]).unlink(missing_ok=True)
            except Exception:
                pass
            print(f"  [{variant} top] cfg={e['config']}  score={e['score']:.4f}  "
                  f"n={e['n_trades']}  wr={e['win_rate']*100:.1f}%  "
                  f"total={m['total_pnl']:+.2f}U")

        # Reeval baseline for ranking comparison
        wf_base = run_walk_forward(
            fn, client, ACTIVE_SYMBOLS, MONTHS,
            n_segments=3, config_overrides=baseline,
            config_label=f"baseline_short_{variant}_{int(time.time())}",
        )
        base_trades = [t for s in wf_base["segments"] for t in s["trades"]]
        base_metrics = _segment_metrics(base_trades)
        base_score = default_objective_winrate_focused(base_metrics)
        try:
            Path(wf_base["_pickle_path"]).unlink(missing_ok=True)
        except Exception:
            pass
        tops_per_variant[f"{variant}_baseline"] = {
            "config": dict(baseline),
            "score": base_score,
            "n_trades": base_metrics["n_trades"],
            "win_rate": base_metrics["win_rate"],
            "total_pnl": base_metrics["total_pnl"],
        }
        print(f"  [{variant} baseline]  score={base_score:.4f}  "
              f"n={base_metrics['n_trades']}  wr={base_metrics['win_rate']*100:.1f}%  "
              f"total={base_metrics['total_pnl']:+.2f}U")

    # Save pickle for task 2 audit consumer
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_pkl = ROOT / ".cache" / f"masr_short_sweep_top_{ts}.pkl"
    payload = {
        "ts": ts,
        "baseline_config": baseline,
        "param_grid": PARAM_GRID,
        "tops_per_variant": tops_per_variant,
        "json_paths": {v: summary[v]["json_path"] for v in ("slow", "fast")},
        "best_per_variant": {v: summary[v]["best_config"] for v in ("slow", "fast")},
    }
    with open(out_pkl, "wb") as fh:
        pickle.dump(payload, fh)

    print("\n" + "=" * 78)
    print(" Sweep summary")
    print("=" * 78)
    for variant in ("slow", "fast"):
        print(f"\n  variant={variant}")
        b = tops_per_variant[f"{variant}_baseline"]
        print(f"    baseline:  score={b['score']:.4f}  n={b['n_trades']}  "
              f"wr={b['win_rate']*100:.1f}%  total={b['total_pnl']:+.2f}U")
        for i, e in enumerate(tops_per_variant[variant], 1):
            print(f"    #{i}:  score={e['score']:.4f}  n={e['n_trades']}  "
                  f"wr={e['win_rate']*100:.1f}%  total={e['total_pnl']:+.2f}U")
            print(f"        cfg: {e['config']}")
        print(f"    JSON: {summary[variant]['json_path']}")

    print(f"\n[saved] {out_pkl}")
    print("\nEXIT=0")


if __name__ == "__main__":
    main()
