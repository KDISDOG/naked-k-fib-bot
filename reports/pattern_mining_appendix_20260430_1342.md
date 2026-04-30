# Pattern mining report

_Generated: 2026-04-30 05:42 UTC_

_Source: features × 2 strategies × 10 coins (wf_dir=`wf_results`)_

_Sample thresholds: per-coin n≥10; strategy needs ≥5 valid coins; signal = PnL gap>1.5×std OR WR diff>5pp_


## 1. Coin features (固定欄位)


| symbol | atr_pct_med | adx_med | range_share | whipsaw_idx | gap_freq | volume_quote_med | btc_corr_30d | asset_class | history_months_actual |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BTCUSDT | 3.5998 | 25.08 | 0.284 | 0.112 | 0 | 14,048,379,959 | 1 | crypto_major | 39 |
| ETHUSDT | 4.8246 | 28.01 | 0.216 | 0.115 | 0 | 8,288,617,753 | 0.937 | crypto_major | 39 |
| SOLUSDT | 6.7478 | 25.03 | 0.269 | 0.125 | 0 | 2,462,357,959 | 0.832 | crypto_alt | 39 |
| XRPUSDT | 5.5635 | 22.87 | 0.392 | 0.137 | 0 | 852,463,497 | 0.824 | crypto_alt | 39 |
| DOGEUSDT | 6.8698 | 26.45 | 0.23 | 0.124 | 0 | 674,189,249 | 0.611 | meme | 39 |
| 1000PEPEUSDT | 10.2353 | 26.51 | 0.22 | 0.118 | 0 | 650,894,908 | 0.743 | meme | 36.4 |
| SKYAIUSDT | 11.8782 | 32.7 | 0.184 | 0.105 | 0 | 3,787,962 | -0.193 | crypto_alt | 11.8 |
| XAUUSDT | 2.4233 | 29.74 | 0.219 | 0.18 | 0.007 | 533,012,297 | 0.582 | cfd | 4.7 |
| XAGUSDT | 6.2556 | 28.1 | 0.149 | 0.116 | 0 | 960,851,242 | 0.535 | cfd | 3.8 |
| CLUSDT | 7.0393 | 13.68 | 1 | 0.273 | 0 | 738,481,588 | -0.647 | cfd | 1 |


## 2. Strategy 全期成果（n≥10 才入統計）

| strategy | n_coins_total | n_coins_valid | median_pnl(U) | median_wr | pnl_std(U) | status |
| --- | --- | --- | --- | --- | --- | --- |
| granville | 10 | 0 | — | — | — | skipped |
| masr_short | 10 | 0 | — | — | — | skipped |


## 3. 各策略 × 特徵 tier 表


### `granville`

_skipped: only 0 valid coins (< 5)_


### `masr_short`

_skipped: only 0 valid coins (< 5)_


## 4. 建議的 .env 新增閾值（皆需 sweep 確認）


_(沒有 signal 超過顯著門檻)_
