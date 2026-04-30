"""
P12D Task 2：MASR Short v2 cooldown 等價驗證

驗證移植到 live 的 cooldown gate（strategies/ma_sr_short.py:on_position_close）
跟 backtest 內建 cooldown_until 完全等價。

跑法（per symbol × variant）：
  1. backtest 跑 → 取得所有 trades（含 close_bar / close_time / result）
  2. 模擬 live 流程：iterate 1H bars
     - 維護 live `_cooldown_until[symbol]: pd.Timestamp`
     - 每 bar 先 cooldown gate；若在 cooldown 內 → cooldown_rejected += 1, continue
     - 否則呼叫 _v2_check_at_bar
     - 若有 signal：比對 backtest trade by entry_time（exact match
       entry/sl/tp/score）；然後查 backtest 的 close_time + result 模擬
       on_position_close 設 cooldown
  3. 期望結果：
     - live signals 數 = backtest signals 數（不再多 30-60%）
     - 全部 entry/sl/tp/score exact match
     - cooldown_rejected 對應 P12B 的 cooldown_acc

config 用 fast.top3：LOOKBACK=150, TOL=0.4, TP1=1.5, SL=2.5。
"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv()

# 在 import 任何 config-dependent module 前注入 fast.top3 config
FAST_TOP3 = {
    "MASR_SHORT_VARIANT":           "fast",
    "MASR_SHORT_RES_LOOKBACK":      "150",
    "MASR_SHORT_RES_TOL_ATR_MULT":  "0.4",
    "MASR_SHORT_TP1_RR":            "1.5",
    "MASR_SHORT_SL_ATR_MULT":       "2.5",
}
for k, v in FAST_TOP3.items():
    os.environ[k] = v
for k in ("BACKTEST_USE_FEATURE_FILTERS", "MASR_SHORT_EXCLUDED_SYMBOLS"):
    os.environ.pop(k, None)
os.environ["BACKTEST_USE_FEATURE_FILTERS"] = "false"
os.environ["MASR_SHORT_EXCLUDED_SYMBOLS"] = ""

import pandas as pd
import numpy as np
from binance.client import Client
from backtest import run_backtest_masr_short_v2, fetch_klines, COOLDOWN_BARS
from strategies.ma_sr_short import _v2_check_at_bar
from config import Config

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
MONTHS = 12
TOL_PCT = 1e-7

# tf 分鐘換算（mirror strategies/ma_sr_short.py:on_position_close）
TF_MIN_MAP = {"1m": 1, "3m": 3, "5m": 5, "15m": 15,
              "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1d": 1440}


def _within_pct(a: float, b: float, pct: float) -> bool:
    if a == b:
        return True
    if b == 0:
        return abs(a) < 1e-9
    return abs(a - b) / abs(b) <= pct


def verify_one(symbol: str, variant: str = "fast") -> dict:
    print(f"\n--- Verifying {symbol} variant={variant} (with cooldown) ---")
    bt_trades = run_backtest_masr_short_v2(
        client, symbol, MONTHS, debug=False, variant=variant,
    )
    print(f"  backtest emitted {len(bt_trades)} trades")
    bt_by_time: dict[pd.Timestamp, object] = {
        pd.Timestamp(t.open_time): t for t in bt_trades
    }

    df_1h = fetch_klines(client, symbol, "1h", MONTHS)
    df_4h = fetch_klines(client, symbol, "4h", MONTHS)
    df_1d = fetch_klines(client, symbol, "1d", MONTHS + 1)
    df_btc_1d = fetch_klines(client, "BTCUSDT", "1d", MONTHS + 1)
    df_btc_4h = fetch_klines(client, "BTCUSDT", "4h", MONTHS)

    # 模擬 live state
    live_cooldown_until: dict[str, pd.Timestamp] = {}

    # tf 從 Config 讀（live behaviour）
    tf = (Config.MASR_SHORT_TIMEFRAME or "1h").lower()
    tf_min = TF_MIN_MAP.get(tf, 60)
    cd_bars = (int(getattr(Config, "MASR_SHORT_COOLDOWN_BARS", 0)) or
                int(getattr(Config, "COOLDOWN_BARS", 6)))

    lookback = int(Config.MASR_SHORT_RES_LOOKBACK)
    warmup = max(60, lookback + 5)
    end = (len(df_1h) - 2) if variant == "slow" else (len(df_1h) - 1)

    bars_processed = 0
    live_signals = 0
    cooldown_rejected = 0
    matched = 0
    real_mismatches: list[dict] = []
    seen_bt_times: set = set()

    for i in range(warmup, end):
        bars_processed += 1
        bar_time = pd.Timestamp(df_1h["time"].iloc[i])

        # Live cooldown gate
        cd = live_cooldown_until.get(symbol)
        if cd is not None and bar_time <= cd:
            cooldown_rejected += 1
            continue

        # 訊號邏輯
        sig = _v2_check_at_bar(
            df_1h, df_4h, df_1d, df_btc_1d, df_btc_4h,
            bar_idx_1h=i, variant=variant,
        )
        if sig is None:
            continue
        live_signals += 1

        sig_entry_time = pd.Timestamp(sig["entry_time"])
        bt_trade = bt_by_time.get(sig_entry_time)
        if bt_trade is None:
            real_mismatches.append({
                "symbol": symbol, "variant": variant,
                "bar_idx": i, "entry_time": str(sig_entry_time),
                "field": "live_only_signal",
                "live": "signal", "backtest": "None",
            })
            continue

        seen_bt_times.add(sig_entry_time)
        diffs = []
        if sig["direction"] != getattr(bt_trade, "direction", "?"):
            diffs.append(("direction", sig["direction"], bt_trade.direction))
        for field, attr in (("entry", "entry"), ("sl", "sl"),
                              ("tp1", "tp1"), ("tp2", "tp2")):
            live_v = float(sig[field])
            bt_v = float(getattr(bt_trade, attr))
            if not _within_pct(live_v, bt_v, TOL_PCT):
                diffs.append((field, live_v, bt_v))
        live_score = int(sig["score"])
        bt_score = int(getattr(bt_trade, "score"))
        if live_score != bt_score:
            diffs.append(("score", live_score, bt_score))

        if diffs:
            for f, lv, bv in diffs:
                real_mismatches.append({
                    "symbol": symbol, "variant": variant,
                    "bar_idx": i, "entry_time": str(sig_entry_time),
                    "field": f, "live": lv, "backtest": bv,
                })
        else:
            matched += 1

        # 模擬 on_position_close：mirror strategies/ma_sr_short.py
        result = getattr(bt_trade, "result", "") or ""
        close_time = getattr(bt_trade, "close_time", None)
        if "SL" in result and "BE" not in result and close_time is not None:
            cooldown_end = pd.Timestamp(close_time) + pd.Timedelta(
                minutes=cd_bars * tf_min,
            )
            live_cooldown_until[symbol] = cooldown_end

    # backtest emitted but live didn't see → real_mismatch
    for bt_time in bt_by_time:
        if bt_time not in seen_bt_times:
            real_mismatches.append({
                "symbol": symbol, "variant": variant,
                "bar_idx": "?", "entry_time": str(bt_time),
                "field": "backtest_only_signal",
                "live": "None", "backtest": "trade",
            })

    return {
        "symbol": symbol, "variant": variant,
        "bars_processed": bars_processed,
        "backtest_signals": len(bt_trades),
        "live_signals": live_signals,
        "exact_matches": matched,
        "cooldown_rejected": cooldown_rejected,
        "real_mismatches": real_mismatches,
    }


def main():
    global client
    client = Client(os.getenv("BINANCE_API_KEY", ""),
                    os.getenv("BINANCE_SECRET", ""), testnet=False)

    print("=== MASR Short v2 fast.top3 — Cooldown Equivalence Verification ===")
    print(f"COOLDOWN_BARS = {COOLDOWN_BARS}")
    print(f"Variant: fast")
    print(f"Config: LOOKBACK=150, TOL=0.4, TP1=1.5, SL=2.5\n")

    results = []
    for sym in SYMBOLS:
        r = verify_one(sym, "fast")
        results.append(r)
        print(f"  → bars={r['bars_processed']}  bt={r['backtest_signals']}  "
              f"live={r['live_signals']}  exact={r['exact_matches']}  "
              f"cooldown_rejected={r['cooldown_rejected']}  "
              f"mismatches={len(r['real_mismatches'])}")

    print(f"\n{'='*78}\n SUMMARY\n{'='*78}")
    print(f"{'symbol':<14} {'bars':>7} {'bt':>5} {'live':>5} "
          f"{'exact':>6} {'cooldwn':>9} {'mismatch':>9}")
    total_mm = 0
    for r in results:
        print(f"{r['symbol']:<14} {r['bars_processed']:>7} {r['backtest_signals']:>5} "
              f"{r['live_signals']:>5} {r['exact_matches']:>6} "
              f"{r['cooldown_rejected']:>9} {len(r['real_mismatches']):>9}")
        total_mm += len(r["real_mismatches"])

    if total_mm > 0:
        print(f"\n❌ COOLDOWN EQUIVALENCE FAILED — {total_mm} real mismatches")
        for r in results:
            for mm in r["real_mismatches"][:5]:
                print(f"  {mm}")
        sys.exit(1)

    print(f"\n✅ COOLDOWN EQUIVALENCE VERIFIED")
    print(f"   live signals exactly match backtest signals (含 cooldown gate)")
    print(f"   live cooldown gate 跟 backtest cooldown_until 等價對齊")
    print("\nEXIT=0")


if __name__ == "__main__":
    main()
