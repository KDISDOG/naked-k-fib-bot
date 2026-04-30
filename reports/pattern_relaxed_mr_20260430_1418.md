# MR Relaxed Mining (Direction A)

_Generated: 2026-04-30 06:18 UTC_

_Source: 10 coins; 6 valid (n≥10)_

_Threshold: PnL_gap > 1.0σ (OR) WR diff > 5.0pp; pnl_std=2.77_


## Per-coin baseline

| symbol | n_trades | win_rate | total_pnl | atr_pct_med | adx_med | btc_corr_30d | asset_class |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ETHUSDT | 61 | 41.0% | -4.43 | 4.8246 | 28.01 | 0.937 | crypto_major |
| SOLUSDT | 96 | 35.4% | -6.62 | 6.7478 | 25.03 | 0.832 | crypto_alt |
| XRPUSDT | 58 | 43.1% | -2.74 | 5.5635 | 22.87 | 0.824 | crypto_alt |
| DOGEUSDT | 68 | 42.6% | -4.47 | 6.8698 | 26.45 | 0.611 | meme |
| 1000PEPEUSDT | 92 | 46.7% | -5.25 | 10.2353 | 26.51 | 0.743 | meme |
| SKYAIUSDT | 40 | 50.0% | +1.33 | 11.8782 | 32.7 | -0.193 | crypto_alt |


## Single-feature candidates (Direction A)


### Feature: `atr_pct_med`

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| high | 2 | 132 | -1.96 | 48.4% |
| low | 2 | 119 | -3.59 | 42.0% |
| mid | 2 | 164 | -5.55 | 39.0% |

🎯 CANDIDATE: `atr_pct_med >= 7.8795` (trigger=WR_gap_only, WR Δ=+6.3pp, PnL gap σ=0.59)

### Feature: `adx_med`

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| high | 2 | 101 | -1.55 | 45.5% |
| low | 2 | 154 | -4.68 | 39.3% |
| mid | 2 | 160 | -4.86 | 44.7% |

🎯 CANDIDATE: `adx_med >= 26.96` (trigger=both, WR Δ=+6.2pp, PnL gap σ=1.13)

### Feature: `range_share`

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| high | 2 | 154 | -4.68 | 39.3% |
| low | 2 | 101 | -1.55 | 45.5% |
| mid | 2 | 160 | -4.86 | 44.7% |

🎯 CANDIDATE: `range_share <= 0.2186` (trigger=both, WR Δ=+6.2pp, PnL gap σ=1.13)

### Feature: `whipsaw_idx`

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| high | 2 | 154 | -4.68 | 39.3% |
| low | 2 | 101 | -1.55 | 45.5% |
| mid | 2 | 160 | -4.86 | 44.7% |

🎯 CANDIDATE: `whipsaw_idx <= 0.1169` (trigger=both, WR Δ=+6.2pp, PnL gap σ=1.13)

### Feature: `gap_freq`

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| na | 6 | 415 | -4.45 | 42.9% |

### Feature: `volume_quote_med`

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| high | 2 | 157 | -5.53 | 38.2% |
| low | 2 | 132 | -1.96 | 48.4% |
| mid | 2 | 126 | -3.61 | 42.9% |

🎯 CANDIDATE: `volume_quote_med <= 666036229.65` (trigger=both, WR Δ=+10.2pp, PnL gap σ=1.29)

### Feature: `btc_corr_30d`

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| high | 2 | 157 | -5.53 | 38.2% |
| low | 2 | 108 | -1.57 | 46.3% |
| mid | 2 | 150 | -4.00 | 44.9% |

🎯 CANDIDATE: `btc_corr_30d <= 0.6968` (trigger=both, WR Δ=+8.1pp, PnL gap σ=1.43)

### Feature: `asset_class`

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| crypto_alt | 3 | 194 | -2.74 | 43.1% |
| crypto_major | 1 | 61 | -4.43 | 41.0% |
| meme | 2 | 160 | -4.86 | 44.7% |

## All candidates summary

| # | Feature | Rule | Threshold | Trigger | WR Δ | PnL σ |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | atr_pct_med | >= | 7.8795 | WR_gap_only | +6.3pp | 0.59 |
| 2 | adx_med | >= | 26.96 | both | +6.2pp | 1.13 |
| 3 | range_share | <= | 0.2186 | both | +6.2pp | 1.13 |
| 4 | whipsaw_idx | <= | 0.1169 | both | +6.2pp | 1.13 |
| 5 | volume_quote_med | <= | 666036229.65 | both | +10.2pp | 1.29 |
| 6 | btc_corr_30d | <= | 0.6968 | both | +8.1pp | 1.43 |