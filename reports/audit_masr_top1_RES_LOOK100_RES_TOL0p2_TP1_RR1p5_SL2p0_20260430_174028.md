# Audit: MASR candidate `top1_RES_LOOK100_RES_TOL0p2_TP1_RR1p5_SL2p0`

_Generated: 2026-04-30T09:40:28.229518+00:00_


**Candidate**: `RES_LOOKBACK=100, RES_TOL_ATR=0.2, TP1_RR=1.5, SL_ATR=2.0`

**Logic**: AND

**Rules JSON**:
```json
[]
```

## Status: **ROBUST**

_3/3 positive, wr_std=3.8pp, min_n=265_


## Per-segment metrics

| Segment | n_trades | win_rate | total_pnl |
| --- | --- | --- | --- |
| seg1 | 275 | 56.4% | +75.34U |
| seg2 | 265 | 47.5% | +15.41U |
| seg3 | 398 | 54.5% | +83.28U |
| **total** | **938** | **+58.01U avg** | **+174.03U** |

## Stability metrics

| Metric | Value |
| --- | --- |
| all_positive | True |
| n_negative_segments | 0 / 3 |
| wr_std (pp) | 3.79 |
| pnl_consistency | 0.478 |
| min_n_trades | 265 |
| sign_flip_count | 0 |
| concentration | 47.9% |
| stability_adjusted_pnl | +83.13U |

## Per-coin × per-segment (kept coins only)

| symbol | seg1 (n/wr/pnl) | seg2 | seg3 | total |
| --- | --- | --- | --- | --- |
| BTCUSDT | 80/59%/+23.7 | 77/49%/+2.3 | 68/57%/+7.9 | n=225 pnl=+33.92U |
| ETHUSDT | 70/57%/+12.8 | 49/57%/+2.8 | 54/46%/+7.6 | n=173 pnl=+23.27U |
| SOLUSDT | 43/53%/+13.8 | 35/43%/+3.5 | 57/54%/+8.6 | n=135 pnl=+25.81U |
| XRPUSDT | 32/53%/+4.0 | 40/28%/-17.5 | 47/49%/+1.2 | n=119 pnl=-12.33U |
| DOGEUSDT | 39/49%/+3.6 | 38/61%/+20.8 | 39/59%/+17.1 | n=116 pnl=+41.47U |
| 1000PEPEUSDT | 11/82%/+17.5 | 26/42%/+3.5 | 35/34%/-0.2 | n=72 pnl=+20.81U |
| SKYAIUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 28/57%/+24.7 | n=28 pnl=+24.66U |
| XAUUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 41/71%/+5.0 | n=41 pnl=+5.01U |
| XAGUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 22/55%/+0.2 | n=22 pnl=+0.18U |
| CLUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 7/100%/+11.2 | n=7 pnl=+11.24U |