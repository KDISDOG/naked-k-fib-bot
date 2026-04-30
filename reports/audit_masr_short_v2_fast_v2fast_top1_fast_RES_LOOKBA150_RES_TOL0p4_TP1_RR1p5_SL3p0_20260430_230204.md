# Audit: MASR_SHORT_V2_FAST candidate `v2fast_top1_fast_RES_LOOKBA150_RES_TOL0p4_TP1_RR1p5_SL3p0`

_Generated: 2026-04-30T15:02:04.670225+00:00_


**Candidate**: `fast top1: RES_LOOKBACK=150, RES_TOL_ATR=0.4, TP1_RR=1.5, SL_ATR=3.0`

**Logic**: AND

**Rules JSON**:
```json
[]
```

## Status: **ROBUST**

_3/3 positive, wr_std=2.7pp, min_n=266_


## Per-segment metrics

| Segment | n_trades | win_rate | total_pnl |
| --- | --- | --- | --- |
| seg1 | 359 | 59.6% | +45.01U |
| seg2 | 387 | 59.7% | +41.47U |
| seg3 | 266 | 65.4% | +63.18U |
| **total** | **1012** | **+49.88U avg** | **+149.65U** |

## Stability metrics

| Metric | Value |
| --- | --- |
| all_positive | True |
| n_negative_segments | 0 / 3 |
| wr_std (pp) | 2.72 |
| pnl_consistency | 0.809 |
| min_n_trades | 266 |
| sign_flip_count | 0 |
| concentration | 42.2% |
| stability_adjusted_pnl | +121.13U |

## Per-coin × per-segment (kept coins only)

| symbol | seg1 (n/wr/pnl) | seg2 | seg3 | total |
| --- | --- | --- | --- | --- |
| BTCUSDT | 62/61%/+5.9 | 87/56%/+2.1 | 50/76%/+13.0 | n=199 pnl=+21.04U |
| ETHUSDT | 70/59%/+3.9 | 46/59%/+8.0 | 36/67%/+7.0 | n=152 pnl=+18.83U |
| SOLUSDT | 69/64%/+19.0 | 77/60%/+6.0 | 17/76%/+2.2 | n=163 pnl=+27.27U |
| XRPUSDT | 63/51%/+3.1 | 66/67%/+16.2 | 51/61%/+17.7 | n=180 pnl=+37.04U |
| DOGEUSDT | 42/43%/-6.1 | 62/60%/+9.3 | 10/50%/+3.2 | n=114 pnl=+6.41U |
| 1000PEPEUSDT | 53/77%/+19.2 | 49/57%/-0.2 | 28/61%/-1.7 | n=130 pnl=+17.39U |
| SKYAIUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 34/59%/+10.3 | n=34 pnl=+10.25U |
| XAUUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 6/67%/+1.9 | n=6 pnl=+1.92U |
| XAGUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 34/65%/+9.5 | n=34 pnl=+9.49U |