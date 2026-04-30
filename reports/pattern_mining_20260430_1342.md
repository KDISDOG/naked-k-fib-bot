# Pattern mining report

_Generated: 2026-04-30 05:42 UTC_

_Source: features × 5 strategies × 10 coins (wf_dir=`wf_results`)_

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
| masr | 10 | 9 | 7.33 | 45.6% | 13.03 | OK |
| bd | 10 | 9 | 0.98 | 42.3% | 9.86 | OK |
| mr | 10 | 6 | -4.45 | 42.9% | 2.77 | OK |
| smc | 10 | 7 | -2.02 | 41.4% | 3.97 | OK |
| nkf | 10 | 6 | 2.83 | 42.9% | 8.09 | OK |


## 3. 各策略 × 特徵 tier 表


### `masr`

**atr_pct_med**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 3 | 199 | +25.83 | 45.6% | 10.2353 |
| low | 3 | 378 | +24.98 | 50.8% | 3.5998 |
| mid | 3 | 228 | +5.95 | 39.8% | 6.2556 |

**adx_med**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 3 | 80 | +3.46 | 57.7% | 29.74 |
| low | 3 | 403 | +7.33 | 42.3% | 25.03 |
| mid | 3 | 322 | +25.83 | 45.6% | 26.51 |

**range_share**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 3 | 403 | +7.33 | 42.3% | 0.284 |
| low | 3 | 193 | +27.01 | 48.3% | 0.184 |
| mid | 3 | 209 | +3.46 | 45.6% | 0.22 |

**whipsaw_idx**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 3 | 246 | +5.95 | 42.3% | 0.137 |
| low | 3 | 368 | +27.01 | 50.8% | 0.112 |
| mid | 3 | 191 | -0.35 | 33.3% | 0.118 |

**gap_freq**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| na | 9 | 805 | +7.33 | 45.6% | 0 |

**volume_quote_med**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 3 | 455 | +24.98 | 48.3% | 8288617753 |
| low | 3 | 121 | +3.46 | 57.7% | 533012297 |
| mid | 3 | 229 | +5.95 | 42.3% | 852463497 |

**btc_corr_30d**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 3 | 455 | +24.98 | 48.3% | 0.937 |
| low | 3 | 80 | +3.46 | 57.7% | 0.535 |
| mid | 3 | 270 | +5.95 | 42.3% | 0.743 |

**asset_class**

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| cfd | 2 | 54 | +0.44 | 45.8% |
| crypto_alt | 3 | 236 | +7.33 | 42.3% |
| crypto_major | 2 | 342 | +27.06 | 49.6% |
| meme | 2 | 173 | +12.74 | 38.1% |

**Signals (7)**

- ⚠️ **atr_pct_med** (better tier=`high`): low PnL=+24.98U (WR 50.8%, n=378) vs high PnL=+25.83U (WR 45.6%, n=199) — WR gap -5.2pp > 5pp
- ⚠️ **adx_med** (better tier=`low`): low PnL=+7.33U (WR 42.3%, n=403) vs high PnL=+3.46U (WR 57.7%, n=80) — WR gap +15.4pp > 5pp
- ⚠️ **range_share** (better tier=`low`): low PnL=+27.01U (WR 48.3%, n=193) vs high PnL=+7.33U (WR 42.3%, n=403) — PnL gap -19.69U > 1.5×std (13.03); WR gap -6.0pp > 5pp
- ⚠️ **whipsaw_idx** (better tier=`low`): low PnL=+27.01U (WR 50.8%, n=368) vs high PnL=+5.95U (WR 42.3%, n=246) — PnL gap -21.07U > 1.5×std (13.03); WR gap -8.5pp > 5pp
- ⚠️ **volume_quote_med** (better tier=`high`): low PnL=+3.46U (WR 57.7%, n=121) vs high PnL=+24.98U (WR 48.3%, n=455) — PnL gap +21.52U > 1.5×std (13.03); WR gap -9.4pp > 5pp
- ⚠️ **btc_corr_30d** (better tier=`high`): low PnL=+3.46U (WR 57.7%, n=80) vs high PnL=+24.98U (WR 48.3%, n=455) — PnL gap +21.52U > 1.5×std (13.03); WR gap -9.4pp > 5pp
- ⚠️ **asset_class**: best=`crypto_major` PnL=+27.06U/WR=49.6%, worst=`cfd` PnL=+0.44U/WR=45.8% — PnL gap +26.62U > 1.5×std (13.03)

### `bd`

**atr_pct_med**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 3 | 175 | +0.78 | 47.1% | 10.2353 |
| low | 3 | 263 | +2.15 | 41.7% | 3.5998 |
| mid | 3 | 264 | +0.98 | 34.8% | 6.2556 |

**adx_med**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 3 | 58 | +0.98 | 47.1% | 29.74 |
| low | 3 | 354 | -11.12 | 34.8% | 25.03 |
| mid | 3 | 290 | +3.20 | 42.3% | 26.51 |

**range_share**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 3 | 354 | -11.12 | 34.8% | 0.284 |
| low | 3 | 161 | +0.98 | 41.7% | 0.184 |
| mid | 3 | 187 | +2.15 | 49.2% | 0.22 |

**whipsaw_idx**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 3 | 281 | +2.15 | 45.3% | 0.137 |
| low | 3 | 251 | +0.78 | 41.7% | 0.112 |
| mid | 3 | 170 | +0.98 | 42.3% | 0.118 |

**gap_freq**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| na | 9 | 702 | +0.98 | 42.3% | 0 |

**volume_quote_med**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 3 | 351 | +3.20 | 41.7% | 8288617753 |
| low | 3 | 107 | +2.15 | 49.2% | 533012297 |
| mid | 3 | 244 | +0.67 | 34.8% | 852463497 |

**btc_corr_30d**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 3 | 351 | +3.20 | 41.7% | 0.937 |
| low | 3 | 58 | +0.98 | 47.1% | 0.535 |
| mid | 3 | 293 | +0.67 | 42.3% | 0.743 |

**asset_class**

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| cfd | 2 | 41 | +1.57 | 42.5% |
| crypto_alt | 3 | 269 | +0.78 | 45.3% |
| crypto_major | 2 | 234 | -8.13 | 35.5% |
| meme | 2 | 158 | +7.21 | 45.7% |

**Signals (6)**

- ⚠️ **atr_pct_med** (better tier=`low`): low PnL=+2.15U (WR 41.7%, n=263) vs high PnL=+0.78U (WR 47.1%, n=175) — WR gap +5.4pp > 5pp
- ⚠️ **adx_med** (better tier=`high`): low PnL=-11.12U (WR 34.8%, n=354) vs high PnL=+0.98U (WR 47.1%, n=58) — WR gap +12.2pp > 5pp
- ⚠️ **range_share** (better tier=`low`): low PnL=+0.98U (WR 41.7%, n=161) vs high PnL=-11.12U (WR 34.8%, n=354) — WR gap -6.9pp > 5pp
- ⚠️ **volume_quote_med** (better tier=`high`): low PnL=+2.15U (WR 49.2%, n=107) vs high PnL=+3.20U (WR 41.7%, n=351) — WR gap -7.5pp > 5pp
- ⚠️ **btc_corr_30d** (better tier=`high`): low PnL=+0.98U (WR 47.1%, n=58) vs high PnL=+3.20U (WR 41.7%, n=351) — WR gap -5.4pp > 5pp
- ⚠️ **asset_class**: best=`meme` PnL=+7.21U/WR=45.7%, worst=`crypto_major` PnL=-8.13U/WR=35.5% — PnL gap +15.34U > 1.5×std (9.86); WR gap +10.2pp > 5pp

### `mr`

**atr_pct_med**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 2 | 132 | -1.96 | 48.4% | 11.0568 |
| low | 2 | 119 | -3.59 | 42.0% | 5.1941 |
| mid | 2 | 164 | -5.55 | 39.0% | 6.8088 |

**adx_med**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 2 | 101 | -1.55 | 45.5% | 30.355 |
| low | 2 | 154 | -4.68 | 39.3% | 23.95 |
| mid | 2 | 160 | -4.86 | 44.7% | 26.48 |

**range_share**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 2 | 154 | -4.68 | 39.3% | 0.3305 |
| low | 2 | 101 | -1.55 | 45.5% | 0.2 |
| mid | 2 | 160 | -4.86 | 44.7% | 0.225 |

**whipsaw_idx**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 2 | 154 | -4.68 | 39.3% | 0.131 |
| low | 2 | 101 | -1.55 | 45.5% | 0.11 |
| mid | 2 | 160 | -4.86 | 44.7% | 0.121 |

**gap_freq**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| na | 6 | 415 | -4.45 | 42.9% | 0 |

**volume_quote_med**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 2 | 157 | -5.53 | 38.2% | 5375487856 |
| low | 2 | 132 | -1.96 | 48.4% | 327341435 |
| mid | 2 | 126 | -3.61 | 42.9% | 763326373 |

**btc_corr_30d**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 2 | 157 | -5.53 | 38.2% | 0.8845 |
| low | 2 | 108 | -1.57 | 46.3% | 0.209 |
| mid | 2 | 150 | -4.00 | 44.9% | 0.7835 |

**asset_class**

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| crypto_alt | 3 | 194 | -2.74 | 43.1% |
| crypto_major | 1 | 61 | -4.43 | 41.0% |
| meme | 2 | 160 | -4.86 | 44.7% |

**Signals (6)**

- ⚠️ **atr_pct_med** (better tier=`high`): low PnL=-3.59U (WR 42.0%, n=119) vs high PnL=-1.96U (WR 48.4%, n=132) — WR gap +6.3pp > 5pp
- ⚠️ **adx_med** (better tier=`high`): low PnL=-4.68U (WR 39.3%, n=154) vs high PnL=-1.55U (WR 45.5%, n=101) — WR gap +6.2pp > 5pp
- ⚠️ **range_share** (better tier=`low`): low PnL=-1.55U (WR 45.5%, n=101) vs high PnL=-4.68U (WR 39.3%, n=154) — WR gap -6.2pp > 5pp
- ⚠️ **whipsaw_idx** (better tier=`low`): low PnL=-1.55U (WR 45.5%, n=101) vs high PnL=-4.68U (WR 39.3%, n=154) — WR gap -6.2pp > 5pp
- ⚠️ **volume_quote_med** (better tier=`low`): low PnL=-1.96U (WR 48.4%, n=132) vs high PnL=-5.53U (WR 38.2%, n=157) — WR gap -10.2pp > 5pp
- ⚠️ **btc_corr_30d** (better tier=`low`): low PnL=-1.57U (WR 46.3%, n=108) vs high PnL=-5.53U (WR 38.2%, n=157) — WR gap -8.1pp > 5pp

### `smc`

**atr_pct_med**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 3 | 104 | +5.18 | 53.3% | 10.2353 |
| low | 2 | 74 | -0.29 | 44.1% | 4.2122 |
| mid | 2 | 78 | -2.94 | 41.1% | 6.1556 |

**adx_med**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 3 | 109 | +1.44 | 47.6% | 28.01 |
| low | 2 | 78 | -2.94 | 41.1% | 23.95 |
| mid | 2 | 69 | +1.94 | 47.3% | 25.765 |

**range_share**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 3 | 110 | -2.63 | 40.8% | 0.284 |
| low | 2 | 57 | +3.31 | 50.5% | 0.2 |
| mid | 2 | 89 | +1.38 | 46.3% | 0.225 |

**whipsaw_idx**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 3 | 115 | -2.63 | 41.4% | 0.125 |
| low | 2 | 47 | +1.58 | 47.0% | 0.1085 |
| mid | 2 | 94 | -0.84 | 43.0% | 0.1165 |

**gap_freq**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| na | 7 | 256 | -2.02 | 41.4% | 0 |

**volume_quote_med**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 3 | 123 | -2.02 | 40.8% | 8288617753 |
| low | 2 | 67 | +1.03 | 45.9% | 327341435 |
| mid | 2 | 66 | +1.33 | 47.7% | 763326373 |

**btc_corr_30d**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 3 | 123 | -2.02 | 40.8% | 0.937 |
| low | 2 | 52 | +5.54 | 53.7% | 0.209 |
| mid | 2 | 81 | -3.18 | 39.9% | 0.7835 |

**asset_class**

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| crypto_alt | 3 | 93 | -2.63 | 41.4% |
| crypto_major | 2 | 74 | -0.29 | 44.1% |
| meme | 2 | 89 | +1.38 | 46.3% |

**Signals (6)**

- ⚠️ **atr_pct_med** (better tier=`high`): low PnL=-0.29U (WR 44.1%, n=74) vs high PnL=+5.18U (WR 53.3%, n=104) — WR gap +9.2pp > 5pp
- ⚠️ **adx_med** (better tier=`high`): low PnL=-2.94U (WR 41.1%, n=78) vs high PnL=+1.44U (WR 47.6%, n=109) — WR gap +6.5pp > 5pp
- ⚠️ **range_share** (better tier=`low`): low PnL=+3.31U (WR 50.5%, n=57) vs high PnL=-2.63U (WR 40.8%, n=110) — WR gap -9.7pp > 5pp
- ⚠️ **whipsaw_idx** (better tier=`low`): low PnL=+1.58U (WR 47.0%, n=47) vs high PnL=-2.63U (WR 41.4%, n=115) — WR gap -5.6pp > 5pp
- ⚠️ **volume_quote_med** (better tier=`low`): low PnL=+1.03U (WR 45.9%, n=67) vs high PnL=-2.02U (WR 40.8%, n=123) — WR gap -5.1pp > 5pp
- ⚠️ **btc_corr_30d** (better tier=`low`): low PnL=+5.54U (WR 53.7%, n=52) vs high PnL=-2.02U (WR 40.8%, n=123) — PnL gap -7.55U > 1.5×std (3.97); WR gap -12.9pp > 5pp

### `nkf`

**atr_pct_med**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 2 | 60 | +3.83 | 42.9% | 8.5526 |
| low | 2 | 55 | +2.83 | 42.7% | 4.2122 |
| mid | 2 | 78 | -3.73 | 38.5% | 6.1556 |

**adx_med**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 2 | 55 | +5.94 | 46.7% | 27.26 |
| low | 2 | 78 | -3.73 | 38.5% | 23.95 |
| mid | 2 | 60 | +0.71 | 38.9% | 25.765 |

**range_share**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 2 | 64 | -4.53 | 30.1% | 0.338 |
| low | 2 | 55 | +5.94 | 46.7% | 0.218 |
| mid | 2 | 74 | +1.51 | 47.2% | 0.2495 |

**whipsaw_idx**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 2 | 78 | -3.73 | 38.5% | 0.131 |
| low | 2 | 55 | +2.83 | 42.7% | 0.1135 |
| mid | 2 | 60 | +3.83 | 42.9% | 0.121 |

**gap_freq**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| na | 6 | 193 | +2.83 | 42.9% | 0 |

**volume_quote_med**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 2 | 55 | +2.83 | 42.7% | 11168498856 |
| low | 2 | 60 | +3.83 | 42.9% | 662542078.5 |
| mid | 2 | 78 | -3.73 | 38.5% | 1657410728 |

**btc_corr_30d**

| _tier | n_coins | trades_total | median_pnl | median_wr | median_feat |
| --- | --- | --- | --- | --- | --- |
| high | 2 | 55 | +2.83 | 42.7% | 0.9685 |
| low | 2 | 60 | +3.83 | 42.9% | 0.677 |
| mid | 2 | 78 | -3.73 | 38.5% | 0.828 |

**asset_class**

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| crypto_alt | 2 | 78 | -3.73 | 38.5% |
| crypto_major | 2 | 55 | +2.83 | 42.7% |
| meme | 2 | 60 | +3.83 | 42.9% |

**Signals (2)**

- ⚠️ **adx_med** (better tier=`high`): low PnL=-3.73U (WR 38.5%, n=78) vs high PnL=+5.94U (WR 46.7%, n=55) — WR gap +8.2pp > 5pp
- ⚠️ **range_share** (better tier=`low`): low PnL=+5.94U (WR 46.7%, n=55) vs high PnL=-4.53U (WR 30.1%, n=64) — WR gap -16.6pp > 5pp

## 4. 建議的 .env 新增閾值（皆需 sweep 確認）

- **[masr]** atr_pct_med >= `6.7820` (better=high tier, PnL gap +0.86U, WR diff -5.2pp)；推薦做 sweep 確認
- **[masr]** adx_med <= `25.9568` (better=low tier, PnL gap -3.87U, WR diff +15.4pp)；推薦做 sweep 確認
- **[masr]** range_share <= `0.2179` (better=low tier, PnL gap -19.69U, WR diff -6.0pp)；推薦做 sweep 確認
- **[masr]** whipsaw_idx <= `0.1156` (better=low tier, PnL gap -21.07U, WR diff -8.5pp)；推薦做 sweep 確認
- **[masr]** volume_quote_med >= `1381273122.7600` (better=high tier, PnL gap +21.52U, WR diff -9.4pp)；推薦做 sweep 確認
- **[masr]** btc_corr_30d >= `0.8262` (better=high tier, PnL gap +21.52U, WR diff -9.4pp)；推薦做 sweep 確認
- **[masr]** asset_class — better=crypto_major (median PnL +27.06U)，worse=cfd (+0.44U) → 考慮在 MASR 分支加白名單；推薦做 sweep 確認
- **[bd]** atr_pct_med <= `5.2975` (better=low tier, PnL gap -1.37U, WR diff +5.4pp)；推薦做 sweep 確認
- **[bd]** adx_med >= `28.0352` (better=high tier, PnL gap +12.10U, WR diff +12.2pp)；推薦做 sweep 確認
- **[bd]** range_share <= `0.2179` (better=low tier, PnL gap -12.10U, WR diff -6.9pp)；推薦做 sweep 確認
- **[bd]** volume_quote_med >= `1381273122.7600` (better=high tier, PnL gap +1.05U, WR diff -7.5pp)；推薦做 sweep 確認
- **[bd]** btc_corr_30d >= `0.8262` (better=high tier, PnL gap +2.22U, WR diff -5.4pp)；推薦做 sweep 確認
- **[bd]** asset_class — better=meme (median PnL +7.21U)，worse=crypto_major (-8.13U) → 考慮在 BD 分支加白名單；推薦做 sweep 確認
- **[mr]** atr_pct_med >= `7.8795` (better=high tier, PnL gap +1.63U, WR diff +6.3pp)；推薦做 sweep 確認
- **[mr]** adx_med >= `26.9600` (better=high tier, PnL gap +3.13U, WR diff +6.2pp)；推薦做 sweep 確認
- **[mr]** range_share <= `0.2186` (better=low tier, PnL gap -3.13U, WR diff -6.2pp)；推薦做 sweep 確認
- **[mr]** whipsaw_idx <= `0.1169` (better=low tier, PnL gap -3.13U, WR diff -6.2pp)；推薦做 sweep 確認
- **[mr]** volume_quote_med <= `666036229.6500` (better=low tier, PnL gap -3.57U, WR diff -10.2pp)；推薦做 sweep 確認
- **[mr]** btc_corr_30d <= `0.6968` (better=low tier, PnL gap -3.95U, WR diff -8.1pp)；推薦做 sweep 確認
- **[smc]** atr_pct_med >= `6.8649` (better=high tier, PnL gap +5.47U, WR diff +9.2pp)；推薦做 sweep 確認
- **[smc]** adx_med >= `26.5076` (better=high tier, PnL gap +4.37U, WR diff +6.5pp)；推薦做 sweep 確認
- **[smc]** range_share <= `0.2199` (better=low tier, PnL gap -5.94U, WR diff -9.7pp)；推薦做 sweep 確認
- **[smc]** whipsaw_idx <= `0.1149` (better=low tier, PnL gap -4.22U, WR diff -5.6pp)；推薦做 sweep 確認
- **[smc]** volume_quote_med <= `673723362.1800` (better=low tier, PnL gap -3.05U, WR diff -5.1pp)；推薦做 sweep 確認
- **[smc]** btc_corr_30d <= `0.7404` (better=low tier, PnL gap -7.55U, WR diff -12.9pp)；推薦做 sweep 確認
- **[nkf]** adx_med >= `26.4680` (better=high tier, PnL gap +9.68U, WR diff +8.2pp)；推薦做 sweep 確認
- **[nkf]** range_share <= `0.2265` (better=low tier, PnL gap -10.47U, WR diff -16.6pp)；推薦做 sweep 確認