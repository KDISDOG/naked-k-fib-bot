# P12C: MASR Short v2 Sweep + Stability Audit

_Generated: 2026-04-30 23:05_

對 v2 兩個 variant (slow/fast) 做 coordinate descent sweep（4 個 entry/exit
params），對 raw top-3 + baseline 各跑 stability audit。

範圍嚴格遵守：純 sweep + audit + 報告，不改 strategies/ma_sr_short.py（P12B
已完成移植），不動 ACTIVE_STRATEGY，不上 live。

> ⚠️ **cooldown drift caveat**：所有 sweep / audit 的 PnL 數字都來自 backtest
> path（含內建 `cooldown_until` skip）。live 的 cooldown 由 bot_main +
> db.in_cooldown 處理，不完全對齊（見 `scripts/BACKLOG.md` #4）。所以 adj_pnl
> 是「backtest cooldown 模型下的表現」，**不是 live 真實預測**。P12D shadow
> integration 完成後才能 reconcile。

---

## Param grid

```
MASR_SHORT_RES_LOOKBACK     ∈ {50, 75, 100, 125, 150}
MASR_SHORT_RES_TOL_ATR_MULT ∈ {0.20, 0.30, 0.40, 0.50}
MASR_SHORT_TP1_RR           ∈ {1.5, 2.0, 2.5, 3.0}
MASR_SHORT_SL_ATR_MULT      ∈ {1.5, 2.0, 2.5, 3.0}
```

baseline = .env.example 預設值：
```
LOOKBACK=100, TOL=0.3, TP1=2.0, SL=1.2
```

> 注意 baseline 的 SL=1.2 不在 grid 內（grid 從 1.5 起）。grid 設計的考量是
> 讓 SL 寬一點以容忍 short 1H 的 noise；P12 audit baseline 的 SL=1.2 已是
> 歷史寬度，sweep 是去找「再寬些」的最佳點。

---

## Variant: slow

### Sweep result (raw, win-rate-focused score)

| Rank | Config | Score | n_trades | wr | Total PnL (39m) |
|------|--------|------:|---------:|----:|----------------:|
| baseline | LOOK=100, TOL=0.3, TP1=2.0, SL=1.2 | 0.6761 | 594 | 47.8% | +55.02U |
| #1 | LOOK=75, TOL=0.3, TP1=1.5, **SL=3.0** | 0.8463 | 742 | 59.8% | +149.07U |
| #2 | LOOK=75, TOL=0.3, TP1=1.5, SL=2.5 | 0.8461 | 717 | 59.8% | +151.33U |
| #3 | LOOK=75, TOL=0.4, TP1=1.5, SL=3.0 | 0.8327 | 715 | 58.9% | +131.16U |

最終收斂：`LOOK=75, TOL=0.3, TP1=1.5, SL=3.0`（iter 3 無變動）。

### Stability audit (slow)

| Config | seg1 | seg2 | seg3 | wr_std | min_n | conc | adj PnL | Status |
|--------|-----:|-----:|-----:|-------:|------:|-----:|--------:|--------|
| baseline | +4.18 | +28.34 | +22.50 | 4.4pp | 161 | — | **+24.15U** | **ROBUST** |
| #1 | +21.41 | +74.55 | +53.10 | 4.7pp | 203 | — | **+83.58U** | **ROBUST** |
| #2 | +21.83 | +79.19 | +50.31 | 4.4pp | 195 | — | **+81.08U** | **ROBUST** |
| #3 | +17.68 | +58.43 | +55.04 | 4.9pp | 206 | — | **+75.77U** | **ROBUST** |

4/4 slow ROBUST。所有 top configs adj PnL > baseline × 3.1。

---

## Variant: fast

### Sweep result (raw)

| Rank | Config | Score | n_trades | wr | Total PnL (39m) |
|------|--------|------:|---------:|----:|----------------:|
| baseline | LOOK=100, TOL=0.3, TP1=2.0, SL=1.2 | 0.6421 | 1011 | 45.4% | +39.50U |
| #1 | LOOK=150, TOL=0.4, TP1=1.5, SL=3.0 | 0.8651 | 1012 | 61.2% | +149.65U |
| #2 | LOOK=125, TOL=0.4, TP1=1.5, SL=3.0 | 0.8620 | 1027 | 61.0% | +162.31U |
| #3 | LOOK=150, TOL=0.4, TP1=1.5, SL=2.5 | 0.8583 | 982 | 60.7% | +135.50U |

最終收斂：`LOOK=150, TOL=0.4, TP1=1.5, SL=3.0`（iter 3 無變動）。

### Stability audit (fast)

| Config | seg1 | seg2 | seg3 | wr_std | min_n | adj PnL | Status |
|--------|-----:|-----:|-----:|-------:|------:|--------:|--------|
| baseline | +0.23 | +17.75 | +21.52 | 3.0pp | 274 | **+11.66U** | **ROBUST** |
| #1 | +45.01 | +41.47 | +63.18 | 2.7pp | 266 | **+121.13U** | **ROBUST** |
| #2 | +43.97 | +45.39 | +72.95 | 2.6pp | 271 | **+122.29U** | **ROBUST** |
| #3 | +41.96 | +43.10 | +50.44 | **2.2pp** | 264 | **+124.23U** | **ROBUST** |

4/4 fast ROBUST。所有 top configs adj PnL > baseline × 10。

---

## Cross-variant comparison (stability-adjusted ranking)

| Rank | Variant.Config | Total PnL | wr_std | min_n | **Adj PnL** | Status |
|------|----------------|----------:|-------:|------:|------------:|--------|
| 1 | **fast.top3** (LOOK=150, TOL=0.4, TP1=1.5, SL=2.5) | +135.50U | **2.2pp** | 264 | **+124.23U** | ROBUST |
| 2 | fast.top2 (LOOK=125, TOL=0.4, TP1=1.5, SL=3.0) | +162.31U | 2.6pp | 271 | +122.29U | ROBUST |
| 3 | fast.top1 (LOOK=150, TOL=0.4, TP1=1.5, SL=3.0) | +149.65U | 2.7pp | 266 | +121.13U | ROBUST |
| 4 | slow.top1 (LOOK=75, TOL=0.3, TP1=1.5, SL=3.0) | +149.07U | 4.7pp | 203 | +83.58U | ROBUST |
| 5 | slow.top2 (LOOK=75, TOL=0.3, TP1=1.5, SL=2.5) | +151.33U | 4.4pp | 195 | +81.08U | ROBUST |
| 6 | slow.top3 (LOOK=75, TOL=0.4, TP1=1.5, SL=3.0) | +131.16U | 4.9pp | 206 | +75.77U | ROBUST |
| 7 | slow.baseline | +55.02U | 4.4pp | 161 | +24.15U | ROBUST |
| 8 | fast.baseline | +39.50U | 3.0pp | 274 | +11.66U | ROBUST |

**Stability-adjusted overall winner**: **fast.top3** (`LOOK=150, TOL=0.4, TP1=1.5, SL=2.5`)
— adj +124.23U，wr_std 2.2pp 全部最低，三段全正且分布最均勻。

---

## 結論段（Q1–Q7）

### Q1: slow sweep raw best vs slow baseline

slow raw #1：`LOOK=75, TOL=0.3, TP1=1.5, SL=3.0`
- score 0.6761 → **0.8463** (Δ +0.1702, +25%)
- wr 47.8% → **59.8%** (Δ **+12.0pp**)
- total PnL +55.02U → **+149.07U** (Δ +94.05U, +171%)

關鍵變更：`TP1_RR 2.0 → 1.5`（早出 TP1 換高 WR）；`SL 1.2 → 3.0`（SL 大幅放寬讓 trade 撐到 TP1）；`LOOKBACK 100 → 75`（小幅縮短）。

### Q2: fast sweep raw best vs fast baseline

fast raw #1：`LOOK=150, TOL=0.4, TP1=1.5, SL=3.0`
- score 0.6421 → **0.8651** (Δ +0.2230, +35%)
- wr 45.4% → **61.2%** (Δ **+15.8pp** — 全 8 個 config 中最高)
- total PnL +39.50U → **+149.65U** (Δ +110.15U, +279%)

關鍵變更：同 slow（TP1 1.5 / SL 3.0），加上 `LOOKBACK 100 → 150`（fast variant 似乎喜歡更長的 lookback）跟 `TOL 0.3 → 0.4`（容忍更寬的支撐位 cluster）。

### Q3: raw best 在 stability audit 下還是 best 嗎？

**不完全是**：
- **slow**：raw best (slow.top1) → stability adj +83.58U；slow.top2 (raw 第二) adj +81.08U 接近。raw 順序大致維持。
- **fast**：**stability adj #1 是 fast.top3 (raw 第三)** adj +124.23U，raw best (fast.top1) 排到 stability #3 (adj +121.13U)。差距小但**確實翻盤**（差 3.1U）。

raw → stability 排名翻盤這次是 fast 那邊。但 4 個 fast configs adj 都 > +120U，**沒有 P2B-1.5 那種「raw 看似強、stability 完全 REJECTED」的 false positive**。

### Q4: 是否有 config 達到 ROBUST 且 adj PnL 顯著超 baseline？

定義「顯著」= adj PnL > baseline × 1.2。

| Config | baseline 對應 | baseline adj | 1.2× threshold | 此 config adj | ratio | 顯著？ |
|---|---|---:|---:|---:|---:|:---:|
| slow.top1 | slow | +24.15 | +28.98 | +83.58 | 3.46× | ✅ |
| slow.top2 | slow | +24.15 | +28.98 | +81.08 | 3.36× | ✅ |
| slow.top3 | slow | +24.15 | +28.98 | +75.77 | 3.14× | ✅ |
| fast.top1 | fast | +11.66 | +13.99 | +121.13 | 10.39× | ✅ |
| fast.top2 | fast | +11.66 | +13.99 | +122.29 | 10.49× | ✅ |
| fast.top3 | fast | +11.66 | +13.99 | +124.23 | 10.65× | ✅ |

**全部 6 個 top configs 顯著超 baseline**（slow ~3×、fast ~10×）。

### Q5: cross-variant 看，slow 還是 fast 給更好的 stability-adjusted top？

**fast 顯著更好**：
- fast top 三組 adj 都 > +120U；slow top 三組 adj 都在 +75~+84U
- fast top 三組 wr_std 都 ≤ 2.7pp；slow top 三組 wr_std 都在 4.4~4.9pp（fast 三段更平均）
- fast min_n_trades 都 ≥ 264；slow 都在 195~206（fast 樣本更多）

但 fast 本身的 baseline 比 slow 的 baseline 弱（+11.66 vs +24.15）。fast 的 sweep 把它從低點拉到高點，**fast 的可塑性更高**。

**結論**：**fast 是 stability-adjusted 主推**，slow 作為次選 / cross-validate。

### Q6: 推薦的最終 config

**保守版（stability-adjusted #1）**：
```
variant = fast
LOOKBACK = 150     (was 100)
TOL_ATR  = 0.4     (was 0.3)
TP1_RR   = 1.5     (was 2.0)
SL_ATR   = 2.5     (was 1.2)
```
adj +124.23U / wr_std 2.2pp / min_n 264。三段 PnL 最均勻：+41.96 / +43.10 / +50.44，完全沒有「某一段 outsized」的 overfit 風險。

**進取版（raw #1, fast.top1）**：
```
variant = fast
LOOKBACK = 150     (was 100)
TOL_ATR  = 0.4     (was 0.3)
TP1_RR   = 1.5     (was 2.0)
SL_ATR   = 3.0     (was 1.2)   ← 進取版多放 0.5×ATR
```
adj +121.13U / wr_std 2.7pp / min_n 266。raw total PnL +149.65U 略高，但 stability 略遜。

**慎重備選（slow.top1）**：
```
variant = slow
LOOKBACK = 75      (was 100)
TOL_ATR  = 0.3     (unchanged)
TP1_RR   = 1.5     (was 2.0)
SL_ATR   = 3.0     (was 1.2)
```
adj +83.58U。slow 用 i+1 close 確認，比 fast 更保守的 trade entry timing。如果擔心 fast 1-bar 確認太敏感、想用更穩的進場，這是 cross-validate 的選擇。

### Q7: 是否該推進到 P12D（shadow integration with cooldown 驗證）還是回 P12C 找更保守 config？

**進 P12D**。三個理由：

1. **P12C 已經找到 8/8 ROBUST 的 config**——sweep 跟 audit 都極度乾淨，沒有 P2B-1.5 那種 raw → stability 全面崩盤的 false positive。再 sweep 邊際效益遞減且增加過擬風險。

2. **真正的不確定性是 cooldown drift**（BACKLOG #4）：所有 P12C adj PnL 都基於 backtest cooldown 模型，但 live cooldown 可能不一致 → live 多 30-60% trade。這 30-60% 是賺是賠未知。**不解這個問題，sweep 出來的數字都打折扣**。

3. **P12D shadow runner 設計** 應該包含：
   - bar-level 比對（既有 P12B verifier 邏輯）
   - **cooldown 一致性比對**（live 是否在 backtest cooldown 區間內仍下單？）
   - 真實 testnet 訊號發生時 backtest 是否同步觸發？

完成 P12D 後才能評估「保守版 vs 進取版」要選哪個——因為 cooldown drift 會放大或縮小 SL 寬度（3.0×ATR vs 2.5×ATR）的差異。

---

## 推薦的最終 .env 變更（diff format，不直接套用）

**保守版（stability-adjusted #1，推薦）**：

```diff
# === MASR Short (P12C sweep 後保守版，stability-adjusted #1)===
# 證據:reports/p12c_masr_short_sweep_*.md
# fast.top3 ROBUST: 三段全正 / wr_std 2.2pp / min_n 264 / adj PnL +124.23U
# variant=fast 顯著優於 slow（adj 高 ~50%、wr_std 低 ~2pp）

-MASR_SHORT_RES_LOOKBACK=100
+MASR_SHORT_RES_LOOKBACK=150
-MASR_SHORT_RES_TOL_ATR_MULT=0.3
+MASR_SHORT_RES_TOL_ATR_MULT=0.4
-MASR_SHORT_TP1_RR=2.0
+MASR_SHORT_TP1_RR=1.5
-MASR_SHORT_SL_ATR_MULT=1.2
+MASR_SHORT_SL_ATR_MULT=2.5

# variant default 也建議從 slow 改為 fast（P12C cross-variant 顯示 fast 全面領先）
-MASR_SHORT_VARIANT=slow
+MASR_SHORT_VARIANT=fast
```

**進取版（fast.top1）**：跟保守版一樣只有 `MASR_SHORT_SL_ATR_MULT=3.0` (vs 2.5)。raw PnL +14U 但 wr_std 略升。

**等用戶 review 報告 + Q4 證據後手動套**。本輪不直接動 .env / .env.example；不改 ACTIVE_STRATEGY。

---

## 範圍遵守確認

✅ 沒做 shadow integration（留 P12D）  
✅ 沒動 ACTIVE_STRATEGY（仍 ma_sr_breakout long-only）  
✅ 沒動 .env / .env.example（推薦寫在報告，不自動套）  
✅ 沒動 strategies/ma_sr_short.py（P12B 完成的事，本輪不改 logic）  
✅ 沒動 backtest.py（v1+v2 全保留）  
✅ pytest 仍 69 passed (無 regression)

## 證據檔案

| 檔案 | 內容 |
|------|------|
| `.cache/sweep_results/run_backtest_masr_short_v2_slow_*.json` | slow sweep 完整 history |
| `.cache/sweep_results/run_backtest_masr_short_v2_fast_*.json` | fast sweep 完整 history |
| `.cache/masr_short_sweep_top_20260430_2231.pkl` | top-3 + baseline 整合 |
| `.cache/p12c_audit_20260430_2303.pkl` | 8 個 audit 結果 |
| `reports/audit_masr_short_v2_*.md` | 每個 audit 的細節報告 |
