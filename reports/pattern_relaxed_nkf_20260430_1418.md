# NKF Relaxed Mining (Direction A)

_Generated: 2026-04-30 06:18 UTC_

_Source: 10 coins; 6 valid (n≥10)_

_Threshold: PnL_gap > 1.0σ (OR) WR diff > 5.0pp; pnl_std=8.09_


## Per-coin baseline

| symbol | n_trades | win_rate | total_pnl | atr_pct_med | adx_med | btc_corr_30d | asset_class |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BTCUSDT | 25 | 32.0% | +4.01 | 3.5998 | 25.08 | 1 | crypto_major |
| ETHUSDT | 30 | 53.3% | +1.65 | 4.8246 | 28.01 | 0.937 | crypto_major |
| SOLUSDT | 39 | 48.7% | +5.61 | 6.7478 | 25.03 | 0.832 | crypto_alt |
| XRPUSDT | 39 | 28.2% | -13.07 | 5.5635 | 22.87 | 0.824 | crypto_alt |
| DOGEUSDT | 35 | 45.7% | -2.59 | 6.8698 | 26.45 | 0.611 | meme |
| 1000PEPEUSDT | 25 | 40.0% | +10.24 | 10.2353 | 26.51 | 0.743 | meme |


## Single-feature candidates (Direction A)


### Feature: `atr_pct_med`

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| high | 2 | 60 | +3.83 | 42.9% |
| low | 2 | 55 | +2.83 | 42.7% |
| mid | 2 | 78 | -3.73 | 38.5% |

### Feature: `adx_med`

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| high | 2 | 55 | +5.94 | 46.7% |
| low | 2 | 78 | -3.73 | 38.5% |
| mid | 2 | 60 | +0.71 | 38.9% |

🎯 CANDIDATE: `adx_med >= 26.468` (trigger=both, WR Δ=+8.2pp, PnL gap σ=1.20)

### Feature: `range_share`

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| high | 2 | 64 | -4.53 | 30.1% |
| low | 2 | 55 | +5.94 | 46.7% |
| mid | 2 | 74 | +1.51 | 47.2% |

🎯 CANDIDATE: `range_share <= 0.2265` (trigger=both, WR Δ=+16.6pp, PnL gap σ=1.30)

### Feature: `whipsaw_idx`

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| high | 2 | 78 | -3.73 | 38.5% |
| low | 2 | 55 | +2.83 | 42.7% |
| mid | 2 | 60 | +3.83 | 42.9% |

### Feature: `gap_freq`

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| na | 6 | 193 | +2.83 | 42.9% |

### Feature: `volume_quote_med`

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| high | 2 | 55 | +2.83 | 42.7% |
| low | 2 | 60 | +3.83 | 42.9% |
| mid | 2 | 78 | -3.73 | 38.5% |

### Feature: `btc_corr_30d`

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| high | 2 | 55 | +2.83 | 42.7% |
| low | 2 | 60 | +3.83 | 42.9% |
| mid | 2 | 78 | -3.73 | 38.5% |

### Feature: `asset_class`

| _tier | n_coins | trades_total | median_pnl | median_wr |
| --- | --- | --- | --- | --- |
| crypto_alt | 2 | 78 | -3.73 | 38.5% |
| crypto_major | 2 | 55 | +2.83 | 42.7% |
| meme | 2 | 60 | +3.83 | 42.9% |

## All candidates summary

| # | Feature | Rule | Threshold | Trigger | WR Δ | PnL σ |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | adx_med | >= | 26.468 | both | +8.2pp | 1.20 |
| 2 | range_share | <= | 0.2265 | both | +16.6pp | 1.30 |