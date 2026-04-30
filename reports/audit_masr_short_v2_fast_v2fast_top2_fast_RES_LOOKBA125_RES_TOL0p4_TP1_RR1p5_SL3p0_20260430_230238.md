# Audit: MASR_SHORT_V2_FAST candidate `v2fast_top2_fast_RES_LOOKBA125_RES_TOL0p4_TP1_RR1p5_SL3p0`

_Generated: 2026-04-30T15:02:38.901509+00:00_


**Candidate**: `fast top2: RES_LOOKBACK=125, RES_TOL_ATR=0.4, TP1_RR=1.5, SL_ATR=3.0`

**Logic**: AND

**Rules JSON**:
```json
[]
```

## Status: **ROBUST**

_3/3 positive, wr_std=2.6pp, min_n=271_


## Per-segment metrics

| Segment | n_trades | win_rate | total_pnl |
| --- | --- | --- | --- |
| seg1 | 350 | 59.4% | +43.97U |
| seg2 | 406 | 59.6% | +45.39U |
| seg3 | 271 | 64.9% | +72.95U |
| **total** | **1027** | **+54.10U avg** | **+162.31U** |

## Stability metrics

| Metric | Value |
| --- | --- |
| all_positive | True |
| n_negative_segments | 0 / 3 |
| wr_std (pp) | 2.56 |
| pnl_consistency | 0.753 |
| min_n_trades | 271 |
| sign_flip_count | 0 |
| concentration | 44.9% |
| stability_adjusted_pnl | +122.29U |

## Per-coin × per-segment (kept coins only)

| symbol | seg1 (n/wr/pnl) | seg2 | seg3 | total |
| --- | --- | --- | --- | --- |
| BTCUSDT | 68/60%/+7.1 | 94/57%/+4.4 | 50/70%/+10.6 | n=212 pnl=+22.07U |
| ETHUSDT | 68/62%/+5.5 | 57/63%/+15.8 | 34/68%/+6.5 | n=159 pnl=+27.76U |
| SOLUSDT | 65/62%/+15.3 | 83/59%/+6.8 | 21/76%/+2.8 | n=169 pnl=+24.93U |
| XRPUSDT | 60/52%/+4.6 | 61/66%/+12.0 | 51/55%/+10.5 | n=172 pnl=+27.03U |
| DOGEUSDT | 41/44%/-4.7 | 58/52%/-0.3 | 11/64%/+5.5 | n=110 pnl=+0.46U |
| 1000PEPEUSDT | 48/75%/+16.2 | 53/62%/+6.7 | 28/61%/-1.8 | n=129 pnl=+21.23U |
| SKYAIUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 40/68%/+28.0 | n=40 pnl=+27.99U |
| XAUUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 5/60%/+1.8 | n=5 pnl=+1.78U |
| XAGUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 31/65%/+9.1 | n=31 pnl=+9.06U |