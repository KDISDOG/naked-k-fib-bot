# NKF 2-Feature Combo Mining (Direction B)

_Generated: 2026-04-30 06:18 UTC_

_Source: 10 coins_

_Cell threshold: ≥3 coins AND ≥30 trades; signal: WR diff > 8pp AND PnL excess > 1.0σ_


## Top combinations (14 顯著 / 28 pair total)


### `atr_pct_med` × `volume_quote_med`

| _ti | _tj | n_coins | trades_total | median_wr | median_pnl | sym_list |
| --- | --- | --- | --- | --- | --- | --- |
| high | high | 1 | 39 | 48.7% | +5.61 | SOLUSDT |
| high | low | 4 | 66 | 28.3% | -1.29 | 1000PEPEUSDT,CLUSDT,DOGEUSDT,SKYAIUSDT |
| low | high | 4 | 99 | 42.7% | +1.78 | BTCUSDT,ETHUSDT,XAGUSDT,XRPUSDT |
| low | low | 1 | 2 | 0.0% | -0.41 | XAUUSDT |

🎯 CANDIDATE: `NKF` requires (atr_pct_med <= 6.5017 AND volume_quote_med >= 795472542.5)
   target quadrant coins: BTCUSDT, ETHUSDT, XAGUSDT, XRPUSDT (n=99 trades, wr=42.7%, PnL/coin med=+1.78U)

### `atr_pct_med` × `btc_corr_30d`

| _ti | _tj | n_coins | trades_total | median_wr | median_pnl | sym_list |
| --- | --- | --- | --- | --- | --- | --- |
| high | high | 2 | 64 | 44.4% | +7.92 | 1000PEPEUSDT,SOLUSDT |
| high | low | 3 | 41 | 16.7% | -2.59 | CLUSDT,DOGEUSDT,SKYAIUSDT |
| low | high | 3 | 94 | 32.0% | +1.65 | BTCUSDT,ETHUSDT,XRPUSDT |
| low | low | 2 | 7 | 30.0% | +0.76 | XAGUSDT,XAUUSDT |

🎯 CANDIDATE: `NKF` requires (atr_pct_med <= 6.5017 AND btc_corr_30d >= 0.677)
   target quadrant coins: BTCUSDT, ETHUSDT, XRPUSDT (n=94 trades, wr=32.0%, PnL/coin med=+1.65U)

### `adx_med` × `range_share`

| _ti | _tj | n_coins | trades_total | median_wr | median_pnl | sym_list |
| --- | --- | --- | --- | --- | --- | --- |
| high | low | 5 | 68 | 40.0% | +1.65 | 1000PEPEUSDT,ETHUSDT,SKYAIUSDT,XAGUSDT,XAUUSDT |
| low | high | 5 | 138 | 32.0% | +0.00 | BTCUSDT,CLUSDT,DOGEUSDT,SOLUSDT,XRPUSDT |

🎯 CANDIDATE: `NKF` requires (adx_med >= 26.48 AND range_share <= 0.225)
   target quadrant coins: 1000PEPEUSDT, ETHUSDT, SKYAIUSDT, XAGUSDT, XAUUSDT (n=68 trades, wr=40.0%, PnL/coin med=+1.65U)

### `adx_med` × `whipsaw_idx`

| _ti | _tj | n_coins | trades_total | median_wr | median_pnl | sym_list |
| --- | --- | --- | --- | --- | --- | --- |
| high | high | 1 | 2 | 0.0% | -0.41 | XAUUSDT |
| high | low | 4 | 66 | 46.7% | +1.78 | 1000PEPEUSDT,ETHUSDT,SKYAIUSDT,XAGUSDT |
| low | high | 4 | 113 | 37.0% | -1.29 | CLUSDT,DOGEUSDT,SOLUSDT,XRPUSDT |
| low | low | 1 | 25 | 32.0% | +4.01 | BTCUSDT |

🎯 CANDIDATE: `NKF` requires (adx_med >= 26.48 AND whipsaw_idx <= 0.121)
   target quadrant coins: 1000PEPEUSDT, ETHUSDT, SKYAIUSDT, XAGUSDT (n=66 trades, wr=46.7%, PnL/coin med=+1.78U)

### `adx_med` × `gap_freq`

| _ti | _tj | n_coins | trades_total | median_wr | median_pnl | sym_list |
| --- | --- | --- | --- | --- | --- | --- |
| high | high | 1 | 2 | 0.0% | -0.41 | XAUUSDT |
| high | low | 4 | 66 | 46.7% | +1.78 | 1000PEPEUSDT,ETHUSDT,SKYAIUSDT,XAGUSDT |
| low | low | 5 | 138 | 32.0% | +0.00 | BTCUSDT,CLUSDT,DOGEUSDT,SOLUSDT,XRPUSDT |

🎯 CANDIDATE: `NKF` requires (adx_med >= 26.48 AND gap_freq <= 0.0)
   target quadrant coins: 1000PEPEUSDT, ETHUSDT, SKYAIUSDT, XAGUSDT (n=66 trades, wr=46.7%, PnL/coin med=+1.78U)

### `adx_med` × `volume_quote_med`

| _ti | _tj | n_coins | trades_total | median_wr | median_pnl | sym_list |
| --- | --- | --- | --- | --- | --- | --- |
| high | high | 2 | 35 | 56.7% | +1.78 | ETHUSDT,XAGUSDT |
| high | low | 3 | 33 | 16.7% | -0.41 | 1000PEPEUSDT,SKYAIUSDT,XAUUSDT |
| low | high | 3 | 103 | 32.0% | +4.01 | BTCUSDT,SOLUSDT,XRPUSDT |
| low | low | 2 | 35 | 22.9% | -1.29 | CLUSDT,DOGEUSDT |

🎯 CANDIDATE: `NKF` requires (adx_med <= 26.48 AND volume_quote_med >= 795472542.5)
   target quadrant coins: BTCUSDT, SOLUSDT, XRPUSDT (n=103 trades, wr=32.0%, PnL/coin med=+4.01U)

### `range_share` × `whipsaw_idx`

| _ti | _tj | n_coins | trades_total | median_wr | median_pnl | sym_list |
| --- | --- | --- | --- | --- | --- | --- |
| high | high | 4 | 113 | 37.0% | -1.29 | CLUSDT,DOGEUSDT,SOLUSDT,XRPUSDT |
| high | low | 1 | 25 | 32.0% | +4.01 | BTCUSDT |
| low | high | 1 | 2 | 0.0% | -0.41 | XAUUSDT |
| low | low | 4 | 66 | 46.7% | +1.78 | 1000PEPEUSDT,ETHUSDT,SKYAIUSDT,XAGUSDT |

🎯 CANDIDATE: `NKF` requires (range_share <= 0.225 AND whipsaw_idx <= 0.121)
   target quadrant coins: 1000PEPEUSDT, ETHUSDT, SKYAIUSDT, XAGUSDT (n=66 trades, wr=46.7%, PnL/coin med=+1.78U)

### `range_share` × `gap_freq`

| _ti | _tj | n_coins | trades_total | median_wr | median_pnl | sym_list |
| --- | --- | --- | --- | --- | --- | --- |
| high | low | 5 | 138 | 32.0% | +0.00 | BTCUSDT,CLUSDT,DOGEUSDT,SOLUSDT,XRPUSDT |
| low | high | 1 | 2 | 0.0% | -0.41 | XAUUSDT |
| low | low | 4 | 66 | 46.7% | +1.78 | 1000PEPEUSDT,ETHUSDT,SKYAIUSDT,XAGUSDT |

🎯 CANDIDATE: `NKF` requires (range_share <= 0.225 AND gap_freq <= 0.0)
   target quadrant coins: 1000PEPEUSDT, ETHUSDT, SKYAIUSDT, XAGUSDT (n=66 trades, wr=46.7%, PnL/coin med=+1.78U)

### `range_share` × `volume_quote_med`

| _ti | _tj | n_coins | trades_total | median_wr | median_pnl | sym_list |
| --- | --- | --- | --- | --- | --- | --- |
| high | high | 3 | 103 | 32.0% | +4.01 | BTCUSDT,SOLUSDT,XRPUSDT |
| high | low | 2 | 35 | 22.9% | -1.29 | CLUSDT,DOGEUSDT |
| low | high | 2 | 35 | 56.7% | +1.78 | ETHUSDT,XAGUSDT |
| low | low | 3 | 33 | 16.7% | -0.41 | 1000PEPEUSDT,SKYAIUSDT,XAUUSDT |

🎯 CANDIDATE: `NKF` requires (range_share >= 0.225 AND volume_quote_med >= 795472542.5)
   target quadrant coins: BTCUSDT, SOLUSDT, XRPUSDT (n=103 trades, wr=32.0%, PnL/coin med=+4.01U)

### `whipsaw_idx` × `volume_quote_med`

| _ti | _tj | n_coins | trades_total | median_wr | median_pnl | sym_list |
| --- | --- | --- | --- | --- | --- | --- |
| high | high | 2 | 78 | 38.5% | -3.73 | SOLUSDT,XRPUSDT |
| high | low | 3 | 37 | 0.0% | -0.41 | CLUSDT,DOGEUSDT,XAUUSDT |
| low | high | 3 | 60 | 53.3% | +1.92 | BTCUSDT,ETHUSDT,XAGUSDT |
| low | low | 2 | 31 | 28.3% | +2.00 | 1000PEPEUSDT,SKYAIUSDT |

🎯 CANDIDATE: `NKF` requires (whipsaw_idx <= 0.121 AND volume_quote_med >= 795472542.5)
   target quadrant coins: BTCUSDT, ETHUSDT, XAGUSDT (n=60 trades, wr=53.3%, PnL/coin med=+1.92U)

### `whipsaw_idx` × `btc_corr_30d`

| _ti | _tj | n_coins | trades_total | median_wr | median_pnl | sym_list |
| --- | --- | --- | --- | --- | --- | --- |
| high | high | 2 | 78 | 38.5% | -3.73 | SOLUSDT,XRPUSDT |
| high | low | 3 | 37 | 0.0% | -0.41 | CLUSDT,DOGEUSDT,XAUUSDT |
| low | high | 3 | 80 | 40.0% | +4.01 | 1000PEPEUSDT,BTCUSDT,ETHUSDT |
| low | low | 2 | 11 | 38.3% | -2.16 | SKYAIUSDT,XAGUSDT |

🎯 CANDIDATE: `NKF` requires (whipsaw_idx <= 0.121 AND btc_corr_30d >= 0.677)
   target quadrant coins: 1000PEPEUSDT, BTCUSDT, ETHUSDT (n=80 trades, wr=40.0%, PnL/coin med=+4.01U)

### `gap_freq` × `volume_quote_med`

| _ti | _tj | n_coins | trades_total | median_wr | median_pnl | sym_list |
| --- | --- | --- | --- | --- | --- | --- |
| high | low | 1 | 2 | 0.0% | -0.41 | XAUUSDT |
| low | high | 5 | 138 | 48.7% | +1.92 | BTCUSDT,ETHUSDT,SOLUSDT,XAGUSDT,XRPUSDT |
| low | low | 4 | 66 | 28.3% | -1.29 | 1000PEPEUSDT,CLUSDT,DOGEUSDT,SKYAIUSDT |

🎯 CANDIDATE: `NKF` requires (gap_freq <= 0.0 AND volume_quote_med >= 795472542.5)
   target quadrant coins: BTCUSDT, ETHUSDT, SOLUSDT, XAGUSDT, XRPUSDT (n=138 trades, wr=48.7%, PnL/coin med=+1.92U)

### `gap_freq` × `btc_corr_30d`

| _ti | _tj | n_coins | trades_total | median_wr | median_pnl | sym_list |
| --- | --- | --- | --- | --- | --- | --- |
| high | low | 1 | 2 | 0.0% | -0.41 | XAUUSDT |
| low | high | 5 | 158 | 40.0% | +4.01 | 1000PEPEUSDT,BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT |
| low | low | 4 | 46 | 31.2% | -1.29 | CLUSDT,DOGEUSDT,SKYAIUSDT,XAGUSDT |

🎯 CANDIDATE: `NKF` requires (gap_freq <= 0.0 AND btc_corr_30d >= 0.677)
   target quadrant coins: 1000PEPEUSDT, BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT (n=158 trades, wr=40.0%, PnL/coin med=+4.01U)

### `volume_quote_med` × `btc_corr_30d`

| _ti | _tj | n_coins | trades_total | median_wr | median_pnl | sym_list |
| --- | --- | --- | --- | --- | --- | --- |
| high | high | 4 | 133 | 40.4% | +2.83 | BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT |
| high | low | 1 | 5 | 60.0% | +1.92 | XAGUSDT |
| low | high | 1 | 25 | 40.0% | +10.24 | 1000PEPEUSDT |
| low | low | 4 | 43 | 8.3% | -1.50 | CLUSDT,DOGEUSDT,SKYAIUSDT,XAUUSDT |

🎯 CANDIDATE: `NKF` requires (volume_quote_med >= 795472542.5 AND btc_corr_30d >= 0.677)
   target quadrant coins: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT (n=133 trades, wr=40.4%, PnL/coin med=+2.83U)

## All candidates summary

| # | Pair | Quadrant | n_trades | WR | PnL/coin | WR Δ | PnL σ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | atr_pct_med × volume_quote_med | low_high | 99 | 42.7% | +1.78U | +14.3pp | 2.00 |
| 2 | atr_pct_med × btc_corr_30d | low_high | 94 | 32.0% | +1.65U | +15.3pp | 2.00 |
| 3 | adx_med × range_share | high_low | 68 | 40.0% | +1.65U | +8.0pp | 2.00 |
| 4 | adx_med × whipsaw_idx | high_low | 66 | 46.7% | +1.78U | +9.7pp | 2.00 |
| 5 | adx_med × gap_freq | high_low | 66 | 46.7% | +1.78U | +14.7pp | 2.00 |
| 6 | adx_med × volume_quote_med | low_high | 103 | 32.0% | +4.01U | +15.3pp | 2.00 |
| 7 | range_share × whipsaw_idx | low_low | 66 | 46.7% | +1.78U | +9.7pp | 2.00 |
| 8 | range_share × gap_freq | low_low | 66 | 46.7% | +1.78U | +14.7pp | 2.00 |
| 9 | range_share × volume_quote_med | high_high | 103 | 32.0% | +4.01U | +15.3pp | 2.00 |
| 10 | whipsaw_idx × volume_quote_med | low_high | 60 | 53.3% | +1.92U | +53.3pp | 2.00 |
| 11 | whipsaw_idx × btc_corr_30d | low_high | 80 | 40.0% | +4.01U | +40.0pp | 2.00 |
| 12 | gap_freq × volume_quote_med | low_high | 138 | 48.7% | +1.92U | +20.4pp | 2.00 |
| 13 | gap_freq × btc_corr_30d | low_high | 158 | 40.0% | +4.01U | +8.8pp | 2.00 |
| 14 | volume_quote_med × btc_corr_30d | high_high | 133 | 40.4% | +2.83U | +32.0pp | 2.00 |