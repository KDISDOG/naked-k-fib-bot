# Audit: MASR_SHORT_V2_FAST candidate `v2fast_top3_fast_RES_LOOKBA150_RES_TOL0p4_TP1_RR1p5_SL2p5`

_Generated: 2026-04-30T15:03:17.632452+00:00_


**Candidate**: `fast top3: RES_LOOKBACK=150, RES_TOL_ATR=0.4, TP1_RR=1.5, SL_ATR=2.5`

**Logic**: AND

**Rules JSON**:
```json
[]
```

## Status: **ROBUST**

_3/3 positive, wr_std=2.2pp, min_n=264_


## Per-segment metrics

| Segment | n_trades | win_rate | total_pnl |
| --- | --- | --- | --- |
| seg1 | 343 | 58.9% | +41.96U |
| seg2 | 375 | 60.0% | +43.10U |
| seg3 | 264 | 64.0% | +50.44U |
| **total** | **982** | **+45.17U avg** | **+135.50U** |

## Stability metrics

| Metric | Value |
| --- | --- |
| all_positive | True |
| n_negative_segments | 0 / 3 |
| wr_std (pp) | 2.20 |
| pnl_consistency | 0.917 |
| min_n_trades | 264 |
| sign_flip_count | 0 |
| concentration | 37.2% |
| stability_adjusted_pnl | +124.23U |

## Per-coin × per-segment (kept coins only)

| symbol | seg1 (n/wr/pnl) | seg2 | seg3 | total |
| --- | --- | --- | --- | --- |
| BTCUSDT | 55/51%/+1.4 | 84/61%/+5.6 | 47/70%/+7.4 | n=186 pnl=+14.43U |
| ETHUSDT | 67/61%/+3.7 | 49/61%/+14.8 | 37/68%/+9.9 | n=153 pnl=+28.36U |
| SOLUSDT | 67/61%/+15.8 | 70/56%/+3.7 | 17/65%/-0.1 | n=154 pnl=+19.37U |
| XRPUSDT | 61/51%/+2.1 | 68/66%/+12.5 | 53/64%/+16.7 | n=182 pnl=+31.30U |
| DOGEUSDT | 40/48%/-3.8 | 57/58%/+4.5 | 10/50%/+3.5 | n=107 pnl=+4.16U |
| 1000PEPEUSDT | 53/79%/+22.7 | 47/57%/+2.1 | 28/61%/-1.3 | n=128 pnl=+23.55U |
| SKYAIUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 34/50%/+0.3 | n=34 pnl=+0.32U |
| XAUUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 6/67%/+1.5 | n=6 pnl=+1.50U |
| XAGUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 32/72%/+12.5 | n=32 pnl=+12.51U |