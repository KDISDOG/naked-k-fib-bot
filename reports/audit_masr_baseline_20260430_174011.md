# Audit: MASR candidate `baseline`

_Generated: 2026-04-30T09:40:11.441631+00:00_


**Candidate**: `baseline (current Config)`

**Logic**: AND

**Rules JSON**:
```json
[]
```

## Status: **REGIME_DEPENDENT**

_1 neg segment, magnitude ratio 37.2x > 1.5x_


## Per-segment metrics

| Segment | n_trades | win_rate | total_pnl |
| --- | --- | --- | --- |
| seg1 | 230 | 50.4% | +57.15U |
| seg2 | 222 | 39.2% | -1.86U |
| seg3 | 358 | 47.2% | +69.11U |
| **total** | **810** | **+41.47U avg** | **+124.41U** |

## Stability metrics

| Metric | Value |
| --- | --- |
| all_positive | False |
| n_negative_segments | 1 / 3 |
| wr_std (pp) | 4.73 |
| pnl_consistency | 0.252 |
| min_n_trades | 222 |
| sign_flip_count | 2 |
| concentration | 54.7% |
| stability_adjusted_pnl | +31.34U |

## Per-coin × per-segment (kept coins only)

| symbol | seg1 (n/wr/pnl) | seg2 | seg3 | total |
| --- | --- | --- | --- | --- |
| BTCUSDT | 61/51%/+15.5 | 69/45%/+1.2 | 63/57%/+8.3 | n=193 pnl=+24.98U |
| ETHUSDT | 59/53%/+14.7 | 38/45%/+2.2 | 52/46%/+12.2 | n=149 pnl=+29.15U |
| SOLUSDT | 36/44%/+4.8 | 31/35%/-1.8 | 46/39%/+4.3 | n=113 pnl=+7.33U |
| XRPUSDT | 27/48%/+4.2 | 29/31%/-5.3 | 41/46%/+7.1 | n=97 pnl=+5.95U |
| DOGEUSDT | 38/47%/+5.1 | 32/44%/+9.2 | 44/45%/+11.6 | n=114 pnl=+25.83U |
| 1000PEPEUSDT | 9/78%/+12.7 | 23/22%/-7.3 | 27/22%/-5.8 | n=59 pnl=-0.35U |
| SKYAIUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 26/58%/+27.0 | n=26 pnl=+27.01U |
| XAUUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 36/58%/+3.5 | n=36 pnl=+3.46U |
| XAGUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 18/33%/-2.6 | n=18 pnl=-2.57U |
| CLUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 5/80%/+3.6 | n=5 pnl=+3.63U |