# Audit: MASR_SHORT_V2_SLOW candidate `v2slow_top1_slow_RES_LOOKBA75_RES_TOL0p3_TP1_RR1p5_SL3p0`

_Generated: 2026-04-30T15:00:11.859466+00:00_


**Candidate**: `slow top1: RES_LOOKBACK=75, RES_TOL_ATR=0.3, TP1_RR=1.5, SL_ATR=3.0`

**Logic**: AND

**Rules JSON**:
```json
[]
```

## Status: **ROBUST**

_3/3 positive, wr_std=4.7pp, min_n=203_


## Per-segment metrics

| Segment | n_trades | win_rate | total_pnl |
| --- | --- | --- | --- |
| seg1 | 237 | 53.2% | +21.41U |
| seg2 | 302 | 64.2% | +74.55U |
| seg3 | 203 | 61.1% | +53.10U |
| **total** | **742** | **+49.69U avg** | **+149.07U** |

## Stability metrics

| Metric | Value |
| --- | --- |
| all_positive | True |
| n_negative_segments | 0 / 3 |
| wr_std (pp) | 4.66 |
| pnl_consistency | 0.561 |
| min_n_trades | 203 |
| sign_flip_count | 0 |
| concentration | 50.0% |
| stability_adjusted_pnl | +83.58U |

## Per-coin × per-segment (kept coins only)

| symbol | seg1 (n/wr/pnl) | seg2 | seg3 | total |
| --- | --- | --- | --- | --- |
| BTCUSDT | 45/53%/+1.4 | 65/57%/+2.9 | 35/77%/+9.3 | n=145 pnl=+13.71U |
| ETHUSDT | 47/45%/-0.7 | 43/67%/+17.6 | 25/48%/-6.0 | n=115 pnl=+10.94U |
| SOLUSDT | 43/53%/+14.2 | 66/64%/+13.6 | 20/65%/+1.9 | n=129 pnl=+29.73U |
| XRPUSDT | 42/57%/+3.3 | 49/67%/+19.1 | 40/60%/+8.9 | n=131 pnl=+31.26U |
| DOGEUSDT | 39/59%/+0.4 | 45/69%/+16.1 | 9/78%/+2.4 | n=93 pnl=+18.86U |
| 1000PEPEUSDT | 21/52%/+2.7 | 34/65%/+5.2 | 17/35%/-3.9 | n=72 pnl=+4.01U |
| SKYAIUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 30/70%/+40.9 | n=30 pnl=+40.93U |
| XAUUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 1/100%/+1.5 | n=1 pnl=+1.49U |
| XAGUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 26/50%/-1.8 | n=26 pnl=-1.85U |