# P2B-1.5: NKF Candidate Stability Audit

_Generated: 2026-04-30T17:03_

_Source: p2b15_audit_20260430_1653.pkl_


## Summary table

| # | Candidate | P2B-1 Δ | seg1 | seg2 | seg3 | wr_std (pp) | min_n | conc | adj PnL | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | `no filter (P1 baseline)` | (ref) | -8.50 | +6.20 | +3.41 | 6.8 | 53 | 64% | +0.00 | **REGIME_DEPENDENT** |
| c01 | `adx_med >= 26.468` | +6.05U | -3.21 | +1.20 | +9.18 | 10.8 | 15 | 88% | +0.00 | **REJECTED** |
| c06 | `adx_med >= 26.48 AND whipsaw_idx <= 0.121` | +6.46U | -3.21 | +1.20 | +9.59 | 12.2 | 15 | 89% | +0.00 | **REJECTED** |
| c12 | `whipsaw_idx <= 0.121 AND volume_quote_med >= 795` | +6.46U | -2.80 | +5.88 | +4.49 | 14.5 | 14 | 57% | +0.00 | **REJECTED** |
| c13 | `whipsaw_idx <= 0.121 AND btc_corr_30d >= 0.677` | +14.78U | -5.17 | +8.26 | +12.81 | 15.4 | 20 | 61% | +0.00 | **REJECTED** |
| c15 | `gap_freq <= 0.0 AND btc_corr_30d >= 0.677` | +7.31U | -6.69 | +4.93 | +10.19 | 8.0 | 46 | 67% | +0.00 | **REGIME_DEPENDENT** |


## Per-candidate detail


### baseline: `no filter (P1 baseline)`
- **Status**: REGIME_DEPENDENT — _1 segment negative_
- Coins kept: 10/10  →  BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, DOGEUSDT, 1000PEPEUSDT, SKYAIUSDT, XAUUSDT, XAGUSDT, CLUSDT
- Segments: seg1=-8.50U (n=81, wr=32.1%) ｜ seg2=+6.20U (n=53, wr=47.2%) ｜ seg3=+3.41U (n=72, wr=45.8%)
- Total: +1.11U / wr_std 6.81pp / sign_flips 1 / pnl_consistency 0.000

### c01: `adx_med >= 26.468`
- **Status**: REJECTED — _wr_std=10.8pp > 10.0pp_
- Coins kept: 5/10  →  1000PEPEUSDT, ETHUSDT, SKYAIUSDT, XAGUSDT, XAUUSDT
- Segments: seg1=-3.21U (n=18, wr=27.8%) ｜ seg2=+1.20U (n=15, wr=40.0%) ｜ seg3=+9.18U (n=35, wr=54.3%)
- Total: +7.16U / wr_std 10.83pp / sign_flips 1 / pnl_consistency 0.000
- Equivalent rule sets (same kept coins): c02 (+6.05U), c05 (+6.05U)

### c06: `adx_med >= 26.48 AND whipsaw_idx <= 0.121`
- **Status**: REJECTED — _wr_std=12.2pp > 10.0pp_
- Coins kept: 4/10  →  1000PEPEUSDT, ETHUSDT, SKYAIUSDT, XAGUSDT
- Segments: seg1=-3.21U (n=18, wr=27.8%) ｜ seg2=+1.20U (n=15, wr=40.0%) ｜ seg3=+9.59U (n=33, wr=57.6%)
- Total: +7.57U / wr_std 12.23pp / sign_flips 1 / pnl_consistency 0.000
- Equivalent rule sets (same kept coins): c07 (+6.46U), c09 (+6.46U), c10 (+6.46U)

### c12: `whipsaw_idx <= 0.121 AND volume_quote_med >= 795472542.5`
- **Status**: REJECTED — _wr_std=14.5pp > 10.0pp_
- Coins kept: 3/10  →  BTCUSDT, ETHUSDT, XAGUSDT
- Segments: seg1=-2.80U (n=23, wr=26.1%) ｜ seg2=+5.88U (n=14, wr=57.1%) ｜ seg3=+4.49U (n=23, wr=56.5%)
- Total: +7.58U / wr_std 14.49pp / sign_flips 1 / pnl_consistency 0.000

### c13: `whipsaw_idx <= 0.121 AND btc_corr_30d >= 0.677`
- **Status**: REJECTED — _wr_std=15.4pp > 10.0pp_
- Coins kept: 3/10  →  1000PEPEUSDT, BTCUSDT, ETHUSDT
- Segments: seg1=-5.17U (n=31, wr=22.6%) ｜ seg2=+8.26U (n=20, wr=50.0%) ｜ seg3=+12.81U (n=29, wr=58.6%)
- Total: +15.89U / wr_std 15.37pp / sign_flips 1 / pnl_consistency 0.000

### c15: `gap_freq <= 0.0 AND btc_corr_30d >= 0.677`
- **Status**: REGIME_DEPENDENT — _1 neg segment, magnitude ratio 1.5x > 1.5x_
- Coins kept: 5/10  →  1000PEPEUSDT, BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT
- Segments: seg1=-6.69U (n=62, wr=30.6%) ｜ seg2=+4.93U (n=46, wr=43.5%) ｜ seg3=+10.19U (n=50, wr=50.0%)
- Total: +8.43U / wr_std 8.04pp / sign_flips 1 / pnl_consistency 0.000

## Stability ranking (by stability-adjusted PnL)

| Rank | # | Candidate | Raw PnL | Consistency | Adjusted PnL | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | baseline | `no filter (P1 baseline)` | +1.11U | 0.000 | +0.00U | REGIME_DEPENDENT |
| 2 | c01 | `adx_med >= 26.468` | +7.16U | 0.000 | +0.00U | REJECTED |
| 3 | c06 | `adx_med >= 26.48 AND whipsaw_idx <= 0.12` | +7.57U | 0.000 | +0.00U | REJECTED |
| 4 | c12 | `whipsaw_idx <= 0.121 AND volume_quote_me` | +7.58U | 0.000 | +0.00U | REJECTED |
| 5 | c13 | `whipsaw_idx <= 0.121 AND btc_corr_30d >=` | +15.89U | 0.000 | +0.00U | REJECTED |
| 6 | c15 | `gap_freq <= 0.0 AND btc_corr_30d >= 0.67` | +8.43U | 0.000 | +0.00U | REGIME_DEPENDENT |

## 結論


### 1. P2B-1 raw rank #1 的 c13 是否真的最好？還是有別的 candidate 在分段下更穩？

**不是**。raw rank #1 是 c13（P2B-1 +14.78U），但 stability-adjusted PnL 排第一是 **c01**（adj +0.00U vs c13 +0.00U）。
c13 segment PnL 是 [-5.17, +8.26, +12.81]，seg1 大幅負，seg3 大幅正——raw +14.78U 是後段時段紅利，不是穩定 alpha。

### 2. 是否有 candidate 是 ROBUST（三段全正 + 低變異）？

**沒有**。所有 candidate 至少一段為負或 wr_std 過大；其中 baseline 自己就是 REGIME_DEPENDENT。
也沒有 STABLE_BUT_THIN（三段全正但樣本薄）。

### 3. 是否有 candidate 是 OVERFIT_SUSPECT？哪段集中？

**狹義 OVERFIT_SUSPECT 標籤上沒有觸發**（concentration < 70% 的閾值）。
但所有 candidate（包括 baseline）都呈現「seg1 負 → seg3 大正」的形態，代表 NKF 的賺賠分布**強烈集中在 39m 期間的後段**——這跟 OVERFIT_SUSPECT 描述的「過擬合」病徵實質一樣，只是定性不定量。用更嚴的閾值（concentration > 50% 加 seg1 負）來看，baseline、c01、c06、c12、c13 都會被歸為 OVERFIT_SUSPECT 變體。

### 4. 整體看 NKF 在 39m 是否有真實 alpha，還是只是運氣？

**證據傾向：運氣 + 後段紅利，alpha 弱或不存在。** Baseline (no filter) 39m 總 +1.11U 看似正期望，但拆分為 seg1 -8.50U / seg2 +6.20U / seg3 +3.41U——seg1 虧 8.5U，如果 39m 只取前 13 個月，NKF 是負期望策略。任何 P2B-1 candidate 的 +14.78U「improvement」都來自把 seg2/seg3 的後段表現推到主導，而非把 seg1 的虧損修好。換做下一個 13 個月（例如 2026Q2~2027）沒人保證這後段紅利還在。

### 5. 推薦進 P2B-2（trade-level mining）還是回 P2B-1 找更保守 candidate？

**建議：兩個都不做，先 stop 並重新評估 NKF。** 三個理由：
1. baseline 自身就是 REGIME_DEPENDENT，這不是 filter 能解的問題——filter 只能挑「在這 39m 哪些幣賺錢」，不能挑「在哪一段時間賺錢」；如果策略本身在 seg1 就是錯方向，下一個 seg1 仍然是錯的。
2. P2B-1 +14.78U 的 raw signal 在 stability audit 下完全消失——min_n_trades=20、wr_std=15.4pp，被歸為 REJECTED。raw → stability 排名翻盤。
3. trade-level mining (P2B-2) 解決的是「哪些訊號狀態下勝率高」，但 NKF 的問題不在於「進場狀態挑得不夠好」，而在於「在 seg1 那 13 個月的市場環境下，NKF 的進場邏輯本身對」——換句話說 P2B-2 能挑掉一些雜訊，但救不了這個結構性問題。

**具體建議**：
- 不要把 P2B-1 任何 candidate 推上 .env 或 active list。
- 不要做 P2B-2。
- 重新檢視 NKF：是不是該換 timeframe（從 1h → 4h，跟 MASR 對齊）？是不是該加更基本的 regime filter（HTF EMA200 trend）？這些是策略層的 question，不是 filter mining 能回答的。
- 把這份 audit 報告 + P2B-1 報告當成「NKF 在 39m 沒有 robust filter alpha」的決定性證據存檔，下次策略迭代時參考。