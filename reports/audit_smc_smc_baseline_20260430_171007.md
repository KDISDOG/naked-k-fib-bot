# Audit: SMC candidate `smc_baseline`

_Generated: 2026-04-30T09:10:07.598346+00:00_


**Candidate**: `no filter`

**Logic**: AND

**Rules JSON**:
```json
[]
```

## Status: **OVERFIT_SUSPECT**

_1 segment negative + concentration 77% > 70%_


## Per-segment metrics

| Segment | n_trades | win_rate | total_pnl |
| --- | --- | --- | --- |
| seg1 | 66 | 40.9% | -4.86U |
| seg2 | 94 | 50.0% | +7.74U |
| seg3 | 106 | 41.5% | +2.32U |
| **total** | **266** | **+1.73U avg** | **+5.20U** |

## Stability metrics

| Metric | Value |
| --- | --- |
| all_positive | False |
| n_negative_segments | 1 / 3 |
| wr_std (pp) | 4.15 |
| pnl_consistency | 0.000 |
| min_n_trades | 66 |
| sign_flip_count | 1 |
| concentration | 77.0% |
| stability_adjusted_pnl | +0.00U |

## Per-coin × per-segment (kept coins only)

| symbol | seg1 (n/wr/pnl) | seg2 | seg3 | total |
| --- | --- | --- | --- | --- |
| BTCUSDT | 10/30%/-0.6 | 14/50%/-0.3 | 8/38%/-1.2 | n=32 pnl=-2.02U |
| ETHUSDT | 11/55%/+0.6 | 18/50%/+0.2 | 13/38%/+0.6 | n=42 pnl=+1.44U |
| SOLUSDT | 11/55%/+2.1 | 21/43%/+1.0 | 17/29%/-5.7 | n=49 pnl=-2.63U |
| XRPUSDT | 12/42%/-2.0 | 11/45%/-1.4 | 6/33%/+0.2 | n=29 pnl=-3.24U |
| DOGEUSDT | 7/43%/+0.6 | 13/69%/+5.4 | 17/47%/-0.1 | n=37 pnl=+5.89U |
| 1000PEPEUSDT | 15/27%/-5.5 | 17/47%/+2.9 | 20/40%/-0.5 | n=52 pnl=-3.12U |
| SKYAIUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 15/53%/+5.2 | n=15 pnl=+5.18U |
| XAUUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 1/100%/+0.1 | n=1 pnl=+0.10U |
| XAGUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 5/60%/+4.0 | n=5 pnl=+4.02U |
| CLUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 4/25%/-0.4 | n=4 pnl=-0.43U |