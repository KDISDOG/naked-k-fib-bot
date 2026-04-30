"""
P12C.5 Task 2：在 fast.top3 config 下重跑等價驗證

目的不是「移植正確性」(P12B 已 PASS)，而是「config 改動不破壞 live ↔ backtest
等價性」。

做法：在 import config / backtest / strategies 之前 set os.environ 為 fast.top3
參數值，然後呼叫 _verify_masr_short_v2_port.verify_one(..., variant="fast")
對 BTC/ETH/SOL × 12m 各跑一次。real_mismatches 必須 = 0。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv()

# 在 import 任何 config-dependent module 前注入 fast.top3 config
# （Config class 讀 os.environ，這個 override 會直接生效）
FAST_TOP3 = {
    "MASR_SHORT_VARIANT":           "fast",
    "MASR_SHORT_RES_LOOKBACK":      "150",
    "MASR_SHORT_RES_TOL_ATR_MULT":  "0.4",
    "MASR_SHORT_TP1_RR":            "1.5",
    "MASR_SHORT_SL_ATR_MULT":       "2.5",
}
for k, v in FAST_TOP3.items():
    os.environ[k] = v

# 跟 P12B 一致：清 filter env、清避險排除、純看 raw equivalence
for k in ("BACKTEST_USE_FEATURE_FILTERS", "MASR_SHORT_EXCLUDED_SYMBOLS"):
    os.environ.pop(k, None)
os.environ["BACKTEST_USE_FEATURE_FILTERS"] = "false"
os.environ["MASR_SHORT_EXCLUDED_SYMBOLS"] = ""

# 確認 Config 讀進去新值
from config import Config
print("=== Config readback (fast.top3) ===")
print(f"  MASR_SHORT_VARIANT       = {Config.MASR_SHORT_VARIANT}")
print(f"  MASR_SHORT_RES_LOOKBACK  = {Config.MASR_SHORT_RES_LOOKBACK}")
print(f"  MASR_SHORT_RES_TOL_ATR_MULT = {Config.MASR_SHORT_RES_TOL_ATR_MULT}")
print(f"  MASR_SHORT_TP1_RR        = {Config.MASR_SHORT_TP1_RR}")
print(f"  MASR_SHORT_SL_ATR_MULT   = {Config.MASR_SHORT_SL_ATR_MULT}")

# 簡單斷言
assert Config.MASR_SHORT_RES_LOOKBACK == 150, "LOOKBACK 沒套到"
assert abs(Config.MASR_SHORT_RES_TOL_ATR_MULT - 0.4) < 1e-9, "TOL 沒套到"
assert abs(Config.MASR_SHORT_TP1_RR - 1.5) < 1e-9, "TP1_RR 沒套到"
assert abs(Config.MASR_SHORT_SL_ATR_MULT - 2.5) < 1e-9, "SL_ATR 沒套到"

# 重用既有 verifier 的 verify_one 函式
from binance.client import Client
import _verify_masr_short_v2_port as V

V.client = Client(os.getenv("BINANCE_API_KEY", ""),
                   os.getenv("BINANCE_SECRET", ""), testnet=False)

print("\n=== MASR Short v2 Port Equivalence — fast.top3 config ===")
print("Variant: fast")
print(f"Config: LOOKBACK=150, TOL=0.4, TP1_RR=1.5, SL_ATR=2.5\n")

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
results = []
for sym in SYMS:
    print(f"--- {sym} ---")
    r = V.verify_one(sym, "fast")
    results.append(r)
    print(f"  → bars={r['bars_processed']}  bt={r['backtest_signals']}  "
          f"live={r['live_signals']}  exact={r['exact_matches']}  "
          f"cooldown_acc={r['cooldown_acceptable']}  "
          f"mismatches={len(r['real_mismatches'])}")

# Summary
print(f"\n{'='*78}\n SUMMARY (fast.top3)\n{'='*78}")
print(f"{'symbol':<14} {'bars':>7} {'bt':>5} {'live':>5} "
      f"{'exact':>6} {'cooldwn':>8} {'mismatch':>9}")
total_mm = 0
for r in results:
    print(f"{r['symbol']:<14} {r['bars_processed']:>7} {r['backtest_signals']:>5} "
          f"{r['live_signals']:>5} {r['exact_matches']:>6} "
          f"{r['cooldown_acceptable']:>8} {len(r['real_mismatches']):>9}")
    total_mm += len(r["real_mismatches"])

if total_mm > 0:
    print(f"\n❌ EQUIVALENCE FAILED — {total_mm} real mismatches under fast.top3")
    for r in results:
        for mm in r["real_mismatches"][:5]:
            print(f"  {mm}")
    sys.exit(1)

print(f"\n✅ MASR Short v2 fast.top3 EQUIVALENCE VERIFIED")
print(f"   3 syms × 12m × variant=fast under sweep-found config")
print(f"   live signals all match backtest exact (entry/sl/tp/score)")
print("\nEXIT=0")
