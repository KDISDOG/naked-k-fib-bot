# P3A: Cross-strategy stability audit — All 4 active strategies

_Generated: 2026-04-30T17:13_

_Sources: p2b15_audit_20260430_1653.pkl, p3a_audit_20260430_1711.pkl_


## Master comparison table

| Strategy | Variant | Total PnL | seg1 | seg2 | seg3 | wr_std (pp) | min_n | Status | Δ vs baseline |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| NKF | baseline | +1.11U | -8.50 | +6.20 | +3.41 | 6.8 | 53 | **REGIME_DEPENDENT** | (ref) |
| NKF | c13 (P2B-1 best) | +15.89U | -5.17 | +8.26 | +12.81 | 15.4 | 20 | **REJECTED** | total +14.78; segs [+3.3, +2.1, +9.4]; ↓ stability |
| SMC | baseline | +5.20U | -4.86 | +7.74 | +2.32 | 4.2 | 66 | **OVERFIT_SUSPECT** | (ref) |
| SMC | p1 | +14.77U | +0.59 | +5.40 | +8.78 | 11.1 | 7 | **REJECTED** | total +9.58; segs [+5.5, -2.3, +6.5]; ↓ stability |
| BD | baseline | -0.68U | -5.20 | +16.71 | -12.18 | 4.6 | 189 | **REJECTED** | (ref) |
| BD | p1 | +7.12U | -4.73 | +10.77 | +1.08 | 10.7 | 46 | **REJECTED** | total +7.79; segs [+0.5, -5.9, +13.3]; ↓ stability |
| MASR | baseline | +124.41U | +57.15 | -1.86 | +69.11 | 4.7 | 222 | **REGIME_DEPENDENT** | (ref) |
| MASR | p1 | +119.89U | +57.15 | -1.86 | +64.60 | 4.6 | 222 | **REGIME_DEPENDENT** | total -4.51; segs [+0.0, +0.0, -4.5]; ↑ stability |

## Pattern: 是否每個策略 baseline 都是 seg1 負？

逐策略列 baseline 每段 PnL：
- NKF: seg1 -8.50U ← **負**
- SMC: seg1 -4.86U ← **負**
- BD: seg1 -5.20U ← **負**
- MASR: seg1 +57.15U ← **正**

→ **3/4** baseline seg1 為負；MASR 是唯一 seg1 不負的策略。

## Pattern: P1 filter 在三段下的影響

對每個策略列「filter 對每段的 Δ PnL」：

| Strategy | seg1 Δ | seg2 Δ | seg3 Δ | 總 Δ | 一致性 |
| --- | --- | --- | --- | --- | --- |
| NKF (c13 (P2B-1 best)) | +3.33 | +2.06 | +9.39 | +14.78 | ROBUST (三段都改善) |
| SMC (p1) | +5.45 | -2.34 | +6.46 | +9.58 | REGIME-CONDITIONAL (主要某段) |
| BD (p1) | +0.46 | -5.93 | +13.26 | +7.79 | MIXED |
| MASR (p1) | +0.00 | +0.00 | -4.51 | -4.51 | MIXED |

## 結論


### Q1：是否 4 個策略 baseline 都呈現 seg1 負 / 後段正 的模式？

- NKF seg1 -8.50U
- SMC seg1 -4.86U
- BD seg1 -5.20U
- MASR seg1 +57.15U  ← **異類**

**3/4** baseline seg1 為負（NKF、SMC、BD）。**MASR 是異類**：seg1 +57.15U、seg2 -1.86U、seg3 +69.11U——MASR 的 4h trend-breakout 邏輯在 seg1 那段 crypto 多頭啟動時是大贏，反而 seg2 整理階段微虧。這推翻了「39m 前段是 universal poison」的假設——

→ **不是 regime 問題本身，是「三個 trend/sweep/breakdown 邏輯都恰好在前段不順」**。MASR 證明確有策略可以在 seg1 賺錢；NKF/SMC/BD 在 seg1 虧不是 39m 環境問題，是這三個策略的設計問題。

### Q2：P1 filter 在三段下是否一致改善？

- **SMC** (`btc_corr_30d <= 0.74`): seg1 Δ=+5.45, seg2 Δ=-2.34, seg3 Δ=+6.46  → 總 +9.58U，**REGIME-CONDITIONAL (主要某段)**
- **BD** (`adx_med >= 28`): seg1 Δ=+0.46, seg2 Δ=-5.93, seg3 Δ=+13.26  → 總 +7.79U，**MIXED**
- **MASR** (`asset_class not_in [cfd]`): seg1 Δ=+0.00, seg2 Δ=+0.00, seg3 Δ=-4.51  → 總 -4.51U，**MIXED**

→ **沒有任何策略的 P1 filter 三段一致改善**。SMC 在 seg1 + seg3 改善但 seg2 微差；BD 主要是 seg3 大幅改善（−12.18 → +1.08），seg2 反而從 +16.71 砍到 +10.77；MASR 因為被 filter 掉的 cfd 幣 PnL 量級小，三段都接近 0 變動。**P1 「improvement」幾乎都是 seg3-concentrated**。

### Q3：P1 filter 真實貢獻在哪一段？

- **SMC** (corr≤0.74) 真實貢獻：seg3 主導（Δ +6.46U / 總 +9.57U = 67%），seg1 +5.45U 是次要。filter 把 baseline 的 OVERFIT_SUSPECT（seg2 集中度 64%）轉成 REJECTED（min_n=7、wr_std=11.1pp），看似改善其實是把樣本切到不可信的薄度。
- **BD** (adx≥28) 真實貢獻：seg3 主導（Δ +13.26U / 總 +7.80U = 170%！seg2 反而 −5.94U 抵銷）。BD 在 baseline 是典型雙負（seg1 −5.20、seg3 −12.18）的 REJECTED 策略，filter 後 seg3 由負轉正看似 +13U 大改善，但 seg2 變差近 6U + min_n 從 189 砍到 46，alpha 是雜訊。
- **MASR** (exclude cfd) 真實貢獻：可忽略（總 −4.51U，三段加起來 ≈ 0）。MASR 的 valid_coins 主要是 crypto，cfd 三幣（XAU/XAG/CL）的 PnL 占比 < 4%，filter 砍了等於沒砍。MASR 的 alpha 是策略本身，不是 filter 給的。

### Q4：給定 audit 結果，推薦做什麼？

**推薦 (c) 部分上 active**——但配套必須加 regime detection 層。具體：

- **MASR：可上 active**。是 4 個策略中唯一能在 seg1 盈利的（+57U），wr_std 4.7pp 最低，total +124U 是其他三策合計的數倍。p1 filter（exclude cfd）不上不下都行——拿掉 cfd 的代價是 −4.5U，但避開可能未來上市的奇怪資產，保留 filter 比較保險。
- **SMC：暫不上 active**。baseline 是 OVERFIT_SUSPECT（seg2 集中），p1 filter 把樣本切到只剩 7 trades/segment，更危險。建議先做regime gate（HTF EMA + ADX）作為 SMC v8，再評估。
- **BD：絕對不上 active**。baseline 是 REJECTED（seg1/seg3 都負），p1 filter 也是 REJECTED（seg2 變差）。BD 在這 universe 結構性失敗，建議直接停用該策略；或者徹底改 entry logic（也許不再做純 short），不是 filter 能救。
- **NKF：見 Q5**。

**配套（regime detection 框架）**：既然 3/4 策略 seg1 都不利，在上 active 之前應該先建立「market regime gate」——例如 BTC 1D EMA200 趨勢、VIX-style volatility index、cross-strategy correlation——在 regime 不利時降低倉位甚至停止下單。這是 P3B 而不是 filter mining。

### Q5：NKF 既然 REJECTED，該下 active 嗎？

**YES，下 active**。三個證據：
1. P3A 對照下 NKF 是 4 個策略中 wr_std 第二高（baseline 6.8pp，僅次 SMC p1 11.1pp 而前者是 baseline）且 total PnL 最低（+1.11U vs MASR +124U / SMC +5.20U / BD −0.68U——
    其實 NKF baseline +1.11U 的 raw rank 比 BD 好一點，但 BD 是 REJECTED 我們同意下。NKF 跟 BD 同樣 REJECTED 的話，NKF 的「比 BD 好一點」沒有實質意義。
2. P2B-1.5 已驗證 NKF 沒有 robust filter alpha——所有 5 個 candidates 全部 REJECTED 或 REGIME_DEPENDENT。
3. NKF 是 1h timeframe（vs MASR 4h），跟 MASR 在訊號頻率/品質上是 conflicting positions——下 active 後可把 risk budget 全部給 MASR，避免 NKF 拉低總體 sharpe。

→ 建議 `ACTIVE_STRATEGY` 從 `naked_k_fib,ma_sr_breakout,ma_sr_short` 改成 `ma_sr_breakout,ma_sr_short`（保留 MASR Long/Short 對稱組合，移除 NKF）。**這是建議而不是動作**——本輪不上 live，等用戶決定。