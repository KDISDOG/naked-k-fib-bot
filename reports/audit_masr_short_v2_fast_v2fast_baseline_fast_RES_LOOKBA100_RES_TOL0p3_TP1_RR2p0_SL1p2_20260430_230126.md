# Audit: MASR_SHORT_V2_FAST candidate `v2fast_baseline_fast_RES_LOOKBA100_RES_TOL0p3_TP1_RR2p0_SL1p2`

_Generated: 2026-04-30T15:01:26.196183+00:00_


**Candidate**: `fast baseline: RES_LOOKBACK=100, RES_TOL_ATR=0.3, TP1_RR=2.0, SL_ATR=1.2`

**Logic**: AND

**Rules JSON**:
```json
[]
```

## Status: **ROBUST**

_3/3 positive, wr_std=3.0pp, min_n=274_


## Per-segment metrics

| Segment | n_trades | win_rate | total_pnl |
| --- | --- | --- | --- |
| seg1 | 311 | 41.2% | +0.23U |
| seg2 | 426 | 46.9% | +17.75U |
| seg3 | 274 | 47.8% | +21.52U |
| **total** | **1011** | **+13.17U avg** | **+39.50U** |

## Stability metrics

| Metric | Value |
| --- | --- |
| all_positive | True |
| n_negative_segments | 0 / 3 |
| wr_std (pp) | 2.95 |
| pnl_consistency | 0.295 |
| min_n_trades | 274 |
| sign_flip_count | 0 |
| concentration | 54.5% |
| stability_adjusted_pnl | +11.66U |

## Per-coin × per-segment (kept coins only)

| symbol | seg1 (n/wr/pnl) | seg2 | seg3 | total |
| --- | --- | --- | --- | --- |
| BTCUSDT | 59/36%/-1.1 | 95/43%/-1.7 | 44/45%/+0.1 | n=198 pnl=-2.73U |
| ETHUSDT | 62/39%/-1.8 | 59/44%/+6.7 | 36/53%/+0.5 | n=157 pnl=+5.33U |
| SOLUSDT | 61/44%/+3.0 | 83/45%/+1.7 | 21/48%/+1.4 | n=165 pnl=+6.07U |
| XRPUSDT | 47/36%/-2.2 | 73/55%/+10.8 | 50/42%/+4.4 | n=170 pnl=+13.04U |
| DOGEUSDT | 42/52%/+3.3 | 64/48%/+2.3 | 12/50%/+4.4 | n=118 pnl=+10.01U |
| 1000PEPEUSDT | 40/42%/-1.0 | 52/48%/-2.0 | 29/52%/+2.7 | n=121 pnl=-0.24U |
| SKYAIUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 41/46%/+6.3 | n=41 pnl=+6.25U |
| XAUUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 5/40%/-0.6 | n=5 pnl=-0.62U |
| XAGUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 36/53%/+2.4 | n=36 pnl=+2.39U |