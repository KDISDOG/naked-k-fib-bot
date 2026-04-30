# P12E: backtest CLI dispatcher fix — `masrs` 從 v1 改路由到 v2

_Generated: 2026-05-01 00:50_

## 問題

P12B 已把 live `MaSrShortStrategy` 重寫為 v2，P12C 找到 fast.top3 ROBUST，
P12C.5 套到 .env，P12D shadow integration verified。**但 backtest CLI
dispatcher 兩個 site (`scripts/backtest.py:4177` 多幣 + `:4743` 單幣) 仍呼叫
`run_backtest_masr_short`（v1, P12 證實 dead）**。

用戶實證：
```
$ python scripts/backtest.py --strategy masrs --top-n 10 --months 24 --no-regime
→ 2 trades 全 SL
```
跟 v1 在 P12 39m × 10 幣只 3 trades 線性吻合，**確認 silent drift**。

---

## 修改（commit `ddee32b`）

### 1. argparse 加 `--short-variant` flag
```python
parser.add_argument("--short-variant", default=None,
                    choices=["fast", "slow"],
                    help="MASR_SHORT v2 variant override "
                         "(預設讀 .env MASR_SHORT_VARIANT)")
```

### 2. 多幣路徑 (`backtest.py:4177`) v1 → v2
```diff
 if run_flags.get("masr_short"):
+    _masrs_variant = (args.short_variant or
+                      os.getenv("MASR_SHORT_VARIANT", "fast"))
     try:
-        tr = run_backtest_masr_short(client, sym, args.months, debug=False,
-                                      regime_series=regime_series)
+        tr = run_backtest_masr_short_v2(
+            client, sym, args.months,
+            debug=False, variant=_masrs_variant,
+        )
     except Exception as e:
-        print(f"  [MASR_SHORT] 失敗：{e}")
+        print(f"  [MASR_SHORT v2:{_masrs_variant}] 失敗：{e}")
-    results[(sym, "MASR_SHORT")] = tr
+    results[(sym, f"MASR_SHORT(v2:{_masrs_variant})")] = tr
```

### 3. 單幣路徑 (`backtest.py:4743`) v1 → v2（同上 pattern）

### 4. v1 函式 deprecated warning
```python
def run_backtest_masr_short(...):
    """
    DEPRECATED 2026-04-30 (P12B onwards): use run_backtest_masr_short_v2 instead.
    ...
    """
    warnings.warn(
        "run_backtest_masr_short is deprecated since P12B. "
        "Use run_backtest_masr_short_v2 instead. "
        "See reports/p12_masr_short_diagnosis_*.md for evidence.",
        DeprecationWarning,
        stacklevel=2,
    )
    print(f"\n[{symbol} MASR_SHORT {tf}] 回測開始 ⚠️ DEPRECATED v1")
    # 原 v1 邏輯不動
    ...
```

v1 仍可被直接 import 跟呼叫（保持 archeology），但會 alert caller。

---

## CLI 重跑驗證（任務 2）

```bash
$ python scripts/backtest.py --strategy masrs --top-n 10 --months 24 --no-regime
```

新結果：

```
symbol         strategy             trades  WR%      PnL   avg_pnl  MDD%   best  worst
BTCUSDT        MASR_SHORT(v2:fast)     116   62.9%   +11.20  +0.097  0.3%  +1.20  -1.19  ★
DOGEUSDT       MASR_SHORT(v2:fast)      81   58.0%    +8.20  +0.101  1.1%  +2.37  -3.22  ★
ETHUSDT        MASR_SHORT(v2:fast)      84   67.9%   +25.18  +0.300  0.4%  +2.43  -1.12  ★
SKYAIUSDT      MASR_SHORT(v2:fast)      34   50.0%    +0.32  +0.010  1.3%  +5.96  -3.22
SOLUSDT        MASR_SHORT(v2:fast)      71   59.2%    +4.84  +0.068  1.2%  +2.48  -3.88  ★
XAGUSDT        MASR_SHORT(v2:fast)      33   69.7%   +11.32  +0.343  0.7%  +4.23  -3.06  ★
XAUUSDT        MASR_SHORT(v2:fast)       6   66.7%    +1.50  +0.250  0.1%  +1.23  -0.84  ★
XRPUSDT        MASR_SHORT(v2:fast)     114   63.2%   +25.11  +0.220  0.7%  +2.62  -1.96  ★

──────────────────────────────────
TOTAL: 8 valid coins, 539 trades, WR 62.2%, PnL +87.67U
──────────────────────────────────

平倉原因分布：
  TIMEOUT   290 trades  53.8% wr  +127.36U  avg +0.439  ← 主導
  SL        145 trades  26.9% wr  -146.93U  avg -1.013
  TP1+TP2    53 trades   9.8% wr   +82.21U  avg +1.551
  TP1+BE     51 trades   9.5% wr   +25.04U  avg +0.491
```

對比修前：

| Metric | 修前（v1） | 修後（v2:fast） |
|--------|----------:|---------------:|
| trades | 2 | **539** (+27000%) |
| WR | 0% (全 SL) | **62.2%** |
| total PnL | unknown | **+87.67U** |
| 8 個有效幣全部正 PnL? | NO（全 SL） | **YES** |

訊號量符合 P12 audit 預期（P12D 365 天 batch 7 幣 260 signals → 24m × 10 幣
線性外推 ~600，實測 539 在合理範圍）。

---

## 副作用檢查（任務 3）

```
=== pytest ===
69 passed in 0.73s

=== diff strategies/ma_sr_short.py vs HEAD~1 ===
(empty diff — strategies/ 完全沒動 ✓)

=== _verify_shadow_initial_short.py ===
total_bars_processed:  58772
total_signals_generated: 260
total_exact_matches:     260
total_real_mismatches:   0  ← 必須 0 ✅
✅ SHADOW SHORT INTEGRATION VERIFIED
```

全部 PASS，沒 regression。

---

## 確認段落

- ✅ **dispatcher 已修**：`backtest.py` 兩個 `masrs` 路由點都從 `run_backtest_masr_short` 改為 `run_backtest_masr_short_v2`，加 `--short-variant` flag、label 改 `MASR_SHORT(v2:fast)` 凸顯 variant。
- ✅ **CLI 訊號量現在符合 P12 v2 預期**：24m × 8 valid coins → 539 trades, WR 62.2%, +87.67U。完全洗清舊 v1 dispatcher 的「2 trades 全 SL」silent failure。
- ✅ **v1 仍可被直接 import** 但會 print `DeprecationWarning`，stderr 出現 `⚠️ DEPRECATED v1` 標記；`run_backtest_masr_short` 函式邏輯保留供 archeology / 外部 caller 漸進遷移。
- ✅ **其他 7 策略 dispatcher 未動**（NKF / MR / BD / ML / SMC / MASR_long / Granville）— 用戶選擇不查它們是否也有 v1/v2 routing 問題。
- ✅ **strategies/ma_sr_short.py 沒動** — 本輪只動 backtest CLI 層。

---

## Commits

```
ddee32b  fix(p12e): backtest CLI 'masrs' alias 路由 v1 → v2 + v1 deprecated warning
```

## 範圍遵守確認

- 不動 v1 邏輯（保留可呼叫）
- 不動 v2 邏輯
- 不動 strategies/ma_sr_short.py
- 不動 .env / live / ACTIVE_STRATEGY
- 不動其他 7 策略的 CLI dispatch
- pytest 69 passed (no regression)
- shadow_short verification still 0 real_mismatches
