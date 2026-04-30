"""
Demo：跑 wf_runner + per_coin_report + sweep_runner 三個工具串連測試。
- symbols = BTC/ETH/SOL
- months = 6
- backtest_fn = run_backtest_granville（已接 config_overrides）
- 額外驗證 ConfigPatch 路徑：用 run_backtest_masr（沒有 config_overrides）跑一次 WF
"""
import os
import sys
from pathlib import Path
from binance.client import Client
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

from backtest import run_backtest_granville, run_backtest_masr
from wf_runner import run_walk_forward
from per_coin_report import generate_per_coin_report
from sweep_runner import coordinate_descent_sweep

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
MONTHS = 6


def main():
    client = Client(os.getenv("BINANCE_API_KEY", ""),
                    os.getenv("BINANCE_SECRET", ""), testnet=False)

    # ── 1. wf_runner（Granville，走 config_overrides 路徑）────────
    print("=" * 78)
    print("DEMO 1: wf_runner — Granville (config_overrides path)")
    print("=" * 78)
    wf = run_walk_forward(
        run_backtest_granville, client, SYMBOLS, MONTHS,
        n_segments=3, config_label="demo_default",
    )
    print(f"\n[Aggregate segments]")
    for s in wf["segments"]:
        m = s["metrics"]
        print(f"  {s['label']}: n={m['n_trades']:>3}  WR={m['win_rate']*100:>5.1f}%  "
              f"PnL={m['total_pnl']:>+8.2f}  MDD={m['max_dd']:>6.2f}  "
              f"avg_rr={m['avg_rr']}  gb_p50={m['median_give_back']}")

    # ── 2. per_coin_report ────────────────────────────────────────
    print("\n" + "=" * 78)
    print("DEMO 2: per_coin_report")
    print("=" * 78)
    csv_path = str(Path(__file__).parent.parent / ".cache" / "wf_results" / "demo.csv")
    df = generate_per_coin_report(wf["_pickle_path"], output_path=csv_path)

    # ── 3. wf_runner（MASR，走 ConfigPatch 路徑）─────────────────
    print("\n" + "=" * 78)
    print("DEMO 3: wf_runner — MASR (ConfigPatch path, fn 沒有 config_overrides)")
    print("=" * 78)
    wf_masr = run_walk_forward(
        run_backtest_masr, client, ["BTCUSDT"], MONTHS,
        n_segments=3, config_label="masr_demo",
        config_overrides={"MASR_TP1_RR": 2.5},   # ConfigPatch 改一個值試試
    )
    for s in wf_masr["segments"]:
        m = s["metrics"]
        print(f"  {s['label']}: n={m['n_trades']:>3}  WR={m['win_rate']*100:>5.1f}%  "
              f"PnL={m['total_pnl']:>+8.2f}")

    # ── 4. sweep_runner（小 grid，3 個值）──────────────────────────
    print("\n" + "=" * 78)
    print("DEMO 4: sweep_runner — Granville TP1 grid")
    print("=" * 78)
    result = coordinate_descent_sweep(
        run_backtest_granville, client, SYMBOLS, MONTHS,
        param_grid={"GRANVILLE_TP1_ATR_MULT": [0.8, 1.0, 1.2]},
        max_iters=2,
        n_segments=3,
    )
    print(f"\nbest_config: {result['best_config']}")
    print(f"saved JSON:  {result['_json_path']}")

    print("\n" + "=" * 78)
    print("DEMO 完成")
    print("=" * 78)


if __name__ == "__main__":
    main()
