"""
P12B Task 3：MASR Short v2 port 等價驗證 (revised)

對 BTCUSDT / ETHUSDT / SOLUSDT 各 12 個月，對 fast 跟 slow variant 各跑：
  A) backtest path: run_backtest_masr_short_v2 (source of truth)
  B) live path: strategies.ma_sr_short._v2_check_at_bar (移植版)

KNOWN_ACCEPTABLE_DIFFS:
  - "cooldown_blocked_in_bt": backtest 內建 SL 後 COOLDOWN_BARS 跳過；
    live 的 cooldown 由 bot_main + DB 處理（不在 check_signal 內）。
    helper 在 backtest cooldown 期間仍會 emit signal（live 在 bot_main 層
    被擋）。verifier 用 backtest 的 cooldown 窗格忽略這些 bar 的 mismatch。

real mismatches > 0 → STOP，不應 commit。
"""
import os
import sys
import time
from pathlib import Path
from datetime import datetime
from binance.client import Client
from dotenv import load_dotenv
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

for k in ("BACKTEST_USE_FEATURE_FILTERS", "MASR_SHORT_EXCLUDED_SYMBOLS",
          "MASR_SHORT_VARIANT"):
    os.environ.pop(k, None)
os.environ["BACKTEST_USE_FEATURE_FILTERS"] = "false"
os.environ["MASR_SHORT_EXCLUDED_SYMBOLS"] = ""

from backtest import run_backtest_masr_short_v2, fetch_klines, COOLDOWN_BARS
from strategies.ma_sr_short import _v2_check_at_bar
from config import Config

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
MONTHS = 12

TOL_PCT = 1e-7
TOL_SCORE_DELTA = 0


def _within_pct(a: float, b: float, pct: float) -> bool:
    if a == b:
        return True
    if b == 0:
        return abs(a) < 1e-9
    return abs(a - b) / abs(b) <= pct


def _build_cooldown_set(bt_trades, df_1h: pd.DataFrame, variant: str) -> set:
    """精確 mirror backtest 主迴圈的 cooldown 行為。

    backtest 行為：
      - emit trade at iteration i_emit (slow: i_emit = open_bar-1, fast: open_bar)
      - simulate_trade returns close_bar + result
      - 若 SL（非 BE）→ cooldown_until = close_bar + COOLDOWN_BARS
      - 下一次 iteration i_emit+1：if i <= cooldown_until → skip
      - 所以 SL trade 的 cooldown skip 範圍 = [i_emit+1, cooldown_until]
        slow: [open_bar, close_bar+COOLDOWN_BARS]
        fast: [open_bar+1, close_bar+COOLDOWN_BARS]

    非 SL trade（TP / TIMEOUT / BE）：cooldown_until 不更新，backtest CONTINUES
    iterating 並可能再 emit 多筆獨立 trade。所以 helper 在這些 bars 上 emit 與
    backtest 對齊（不需要 block）。
    """
    blocked: set = set()
    sorted_trades = sorted(bt_trades, key=lambda t: getattr(t, "open_bar", 0))
    for t in sorted_trades:
        result = getattr(t, "result", "")
        open_bar = getattr(t, "open_bar", None)
        close_bar = getattr(t, "close_bar", None)
        if open_bar is None or close_bar is None:
            continue
        if "SL" in result and "BE" not in result:
            # i_emit = open_bar - 1 (slow) / open_bar (fast)
            # blocked = [i_emit + 1, close_bar + COOLDOWN_BARS]
            i_emit = (int(open_bar) - 1) if variant == "slow" else int(open_bar)
            block_start = i_emit + 1
            block_end = int(close_bar) + COOLDOWN_BARS
            for i in range(block_start, block_end + 1):
                blocked.add(i)
    return blocked


def verify_one(symbol: str, variant: str) -> dict:
    print(f"\n--- Verifying {symbol} variant={variant} ---")
    bt_trades = run_backtest_masr_short_v2(
        client, symbol, MONTHS, debug=False, variant=variant,
    )
    bt_by_time: dict[pd.Timestamp, object] = {
        pd.Timestamp(t.open_time): t for t in bt_trades
    }
    print(f"  backtest emitted {len(bt_trades)} trades")

    df_1h = fetch_klines(client, symbol, "1h", MONTHS)
    df_4h = fetch_klines(client, symbol, "4h", MONTHS)
    df_1d = fetch_klines(client, symbol, "1d", MONTHS + 1)
    df_btc_1d = fetch_klines(client, "BTCUSDT", "1d", MONTHS + 1)
    df_btc_4h = fetch_klines(client, "BTCUSDT", "4h", MONTHS)

    # 預先計算 backtest 的 cooldown 範圍（用 helper 對 bt trades 做 SL 檢查）
    cooldown_blocked_idx = _build_cooldown_set(bt_trades, df_1h, variant)
    print(f"  backtest cooldown-blocked bars: {len(cooldown_blocked_idx)}")

    lookback = int(Config.MASR_SHORT_RES_LOOKBACK)
    warmup = max(60, lookback + 5)
    if variant == "slow":
        end = len(df_1h) - 2
    else:
        end = len(df_1h) - 1

    bars_processed = 0
    live_signals = 0
    matched = 0
    cooldown_acceptable = 0
    real_mismatches: list[dict] = []
    seen_bt_times: set = set()

    for i in range(warmup, end):
        bars_processed += 1
        sig = _v2_check_at_bar(
            df_1h, df_4h, df_1d, df_btc_1d, df_btc_4h,
            bar_idx_1h=i, variant=variant,
        )
        # backtest open_time 對應 entry_idx，slow 是 i+1，fast 是 i
        # 用 helper 回的 entry_time 對應
        if sig is not None:
            live_signals += 1
            sig_entry_time = pd.Timestamp(sig["entry_time"])
        else:
            sig_entry_time = pd.Timestamp(df_1h["time"].iloc[i if variant == "fast" else i + 1])

        bt_trade = bt_by_time.get(sig_entry_time)

        if sig is None and bt_trade is None:
            continue

        if (sig is None) != (bt_trade is None):
            # 檢查是否為 cooldown 影響
            if sig is not None and bt_trade is None and i in cooldown_blocked_idx:
                cooldown_acceptable += 1
                continue
            real_mismatches.append({
                "symbol": symbol, "variant": variant,
                "bar_idx": i,
                "entry_time": str(sig_entry_time),
                "field": "signal_existence",
                "live": "signal" if sig else "None",
                "backtest": "trade" if bt_trade else "None",
                "in_cooldown": i in cooldown_blocked_idx,
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
        if abs(live_score - bt_score) > TOL_SCORE_DELTA:
            diffs.append(("score", live_score, bt_score))

        if diffs:
            for f, lv, bv in diffs:
                real_mismatches.append({
                    "symbol": symbol, "variant": variant,
                    "bar_idx": i,
                    "entry_time": str(sig_entry_time),
                    "field": f,
                    "live": lv, "backtest": bv,
                })
        else:
            matched += 1

    # backtest emitted but live didn't see (and not cooldown)
    for bt_time in bt_by_time:
        if bt_time not in seen_bt_times:
            real_mismatches.append({
                "symbol": symbol, "variant": variant,
                "bar_idx": "?",
                "entry_time": str(bt_time),
                "field": "backtest_only_signal",
                "live": "None",
                "backtest": "trade",
            })

    return {
        "symbol": symbol,
        "variant": variant,
        "bars_processed": bars_processed,
        "backtest_signals": len(bt_trades),
        "live_signals": live_signals,
        "exact_matches": matched,
        "cooldown_acceptable": cooldown_acceptable,
        "real_mismatches": real_mismatches,
    }


def main():
    global client
    client = Client(os.getenv("BINANCE_API_KEY", ""),
                    os.getenv("BINANCE_SECRET", ""), testnet=False)

    print("=== MASR Short v2 Port Equivalence Test ===")
    print(f"COOLDOWN_BARS = {COOLDOWN_BARS}")
    all_results = []
    for variant in ("slow", "fast"):
        print(f"\n{'#'*78}\n# Variant: {variant}\n{'#'*78}")
        for sym in SYMBOLS:
            r = verify_one(sym, variant)
            all_results.append(r)
            print(f"  → bars={r['bars_processed']}  bt={r['backtest_signals']}  "
                  f"live={r['live_signals']}  exact={r['exact_matches']}  "
                  f"cooldown_acc={r['cooldown_acceptable']}  "
                  f"mismatches={len(r['real_mismatches'])}")

    print(f"\n{'='*78}\n SUMMARY\n{'='*78}")
    print(f"{'variant':<6} {'symbol':<14} {'bars':>7} {'bt':>5} "
          f"{'live':>5} {'exact':>6} {'cooldwn':>8} {'mismatch':>9}")
    total_mm = 0
    for r in all_results:
        print(f"{r['variant']:<6} {r['symbol']:<14} "
              f"{r['bars_processed']:>7} {r['backtest_signals']:>5} "
              f"{r['live_signals']:>5} {r['exact_matches']:>6} "
              f"{r['cooldown_acceptable']:>8} "
              f"{len(r['real_mismatches']):>9}")
        total_mm += len(r["real_mismatches"])

    if total_mm > 0:
        print(f"\n❌ PORT EQUIVALENCE FAILED — {total_mm} real mismatches")
        for r in all_results:
            for mm in r["real_mismatches"][:5]:
                print(f"  {mm}")
        sys.exit(1)

    print(f"\n✅ MASR Short v2 PORT EQUIVALENCE VERIFIED")
    print(f"   Both slow + fast variants: every backtest signal matches live path")
    print(f"   exact entry/sl/tp/score over 3 syms × 12m × 2 variants")
    print(f"   cooldown asymmetry handled (backtest internal, live handled by bot_main)")
    print("\nEXIT=0")


if __name__ == "__main__":
    main()
