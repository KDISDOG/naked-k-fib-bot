# Audit: MASR_SHORT_V2_SLOW candidate `v2slow_top2_slow_RES_LOOKBA75_RES_TOL0p3_TP1_RR1p5_SL2p5`

_Generated: 2026-04-30T15:00:34.565402+00:00_


**Candidate**: `slow top2: RES_LOOKBACK=75, RES_TOL_ATR=0.3, TP1_RR=1.5, SL_ATR=2.5`

**Logic**: AND

**Rules JSON**:
```json
[]
```

## Status: **ROBUST**

_3/3 positive, wr_std=4.4pp, min_n=195_


## Per-segment metrics

| Segment | n_trades | win_rate | total_pnl |
| --- | --- | --- | --- |
| seg1 | 228 | 53.5% | +21.83U |
| seg2 | 294 | 63.9% | +79.19U |
| seg3 | 195 | 61.0% | +50.31U |
| **total** | **717** | **+50.44U avg** | **+151.33U** |

## Stability metrics

| Metric | Value |
| --- | --- |
| all_positive | True |
| n_negative_segments | 0 / 3 |
| wr_std (pp) | 4.40 |
| pnl_consistency | 0.536 |
| min_n_trades | 195 |
| sign_flip_count | 0 |
| concentration | 52.3% |
| stability_adjusted_pnl | +81.08U |

## Per-coin × per-segment (kept coins only)

| symbol | seg1 (n/wr/pnl) | seg2 | seg3 | total |
| --- | --- | --- | --- | --- |
| BTCUSDT | 41/51%/+1.5 | 62/56%/+3.3 | 32/75%/+6.8 | n=135 pnl=+11.63U |
| ETHUSDT | 46/46%/+0.2 | 43/67%/+20.9 | 23/52%/-4.3 | n=112 pnl=+16.85U |
| SOLUSDT | 42/57%/+13.9 | 63/62%/+15.8 | 19/63%/+1.5 | n=124 pnl=+31.20U |
| XRPUSDT | 38/55%/+2.0 | 49/67%/+18.4 | 39/59%/+6.7 | n=126 pnl=+27.11U |
| DOGEUSDT | 39/59%/-0.1 | 43/70%/+13.7 | 9/78%/+4.1 | n=91 pnl=+17.74U |
| 1000PEPEUSDT | 22/55%/+4.2 | 34/65%/+7.1 | 17/41%/-3.6 | n=73 pnl=+7.68U |
| SKYAIUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 29/69%/+38.1 | n=29 pnl=+38.05U |
| XAUUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 1/100%/+1.2 | n=1 pnl=+1.23U |
| XAGUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 26/50%/-0.2 | n=26 pnl=-0.17U |