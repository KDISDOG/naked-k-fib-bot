# Audit: MASR_SHORT_V2_SLOW candidate `v2slow_top3_slow_RES_LOOKBA75_RES_TOL0p4_TP1_RR1p5_SL3p0`

_Generated: 2026-04-30T15:00:55.782068+00:00_


**Candidate**: `slow top3: RES_LOOKBACK=75, RES_TOL_ATR=0.4, TP1_RR=1.5, SL_ATR=3.0`

**Logic**: AND

**Rules JSON**:
```json
[]
```

## Status: **ROBUST**

_3/3 positive, wr_std=4.9pp, min_n=206_


## Per-segment metrics

| Segment | n_trades | win_rate | total_pnl |
| --- | --- | --- | --- |
| seg1 | 224 | 52.2% | +17.68U |
| seg2 | 285 | 64.2% | +58.43U |
| seg3 | 206 | 58.7% | +55.04U |
| **total** | **715** | **+43.72U avg** | **+131.16U** |

## Stability metrics

| Metric | Value |
| --- | --- |
| all_positive | True |
| n_negative_segments | 0 / 3 |
| wr_std (pp) | 4.90 |
| pnl_consistency | 0.578 |
| min_n_trades | 206 |
| sign_flip_count | 0 |
| concentration | 44.5% |
| stability_adjusted_pnl | +75.77U |

## Per-coin × per-segment (kept coins only)

| symbol | seg1 (n/wr/pnl) | seg2 | seg3 | total |
| --- | --- | --- | --- | --- |
| BTCUSDT | 42/57%/+1.4 | 59/63%/+7.0 | 39/69%/+4.3 | n=140 pnl=+12.75U |
| ETHUSDT | 43/47%/-1.9 | 40/60%/+5.1 | 24/54%/-1.1 | n=107 pnl=+2.03U |
| SOLUSDT | 43/53%/+11.9 | 59/63%/+7.7 | 17/65%/+0.8 | n=119 pnl=+20.51U |
| XRPUSDT | 43/53%/+3.4 | 47/70%/+18.5 | 37/57%/+9.5 | n=127 pnl=+31.42U |
| DOGEUSDT | 33/48%/-3.0 | 48/65%/+18.9 | 8/50%/-0.2 | n=89 pnl=+15.65U |
| 1000PEPEUSDT | 20/55%/+5.8 | 32/66%/+1.2 | 17/24%/-6.0 | n=69 pnl=+1.13U |
| SKYAIUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 32/72%/+42.3 | n=32 pnl=+42.26U |
| XAUUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 2/50%/+0.8 | n=2 pnl=+0.79U |
| XAGUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 30/57%/+4.6 | n=30 pnl=+4.61U |