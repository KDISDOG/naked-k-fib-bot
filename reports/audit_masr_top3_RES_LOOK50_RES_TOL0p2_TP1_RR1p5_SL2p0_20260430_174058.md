# Audit: MASR candidate `top3_RES_LOOK50_RES_TOL0p2_TP1_RR1p5_SL2p0`

_Generated: 2026-04-30T09:40:58.572019+00:00_


**Candidate**: `RES_LOOKBACK=50, RES_TOL_ATR=0.2, TP1_RR=1.5, SL_ATR=2.0`

**Logic**: AND

**Rules JSON**:
```json
[]
```

## Status: **ROBUST**

_3/3 positive, wr_std=2.4pp, min_n=267_


## Per-segment metrics

| Segment | n_trades | win_rate | total_pnl |
| --- | --- | --- | --- |
| seg1 | 267 | 55.4% | +67.71U |
| seg2 | 282 | 49.6% | +26.16U |
| seg3 | 403 | 52.4% | +58.81U |
| **total** | **952** | **+50.89U avg** | **+152.68U** |

## Stability metrics

| Metric | Value |
| --- | --- |
| all_positive | True |
| n_negative_segments | 0 / 3 |
| wr_std (pp) | 2.36 |
| pnl_consistency | 0.649 |
| min_n_trades | 267 |
| sign_flip_count | 0 |
| concentration | 44.3% |
| stability_adjusted_pnl | +99.10U |

## Per-coin × per-segment (kept coins only)

| symbol | seg1 (n/wr/pnl) | seg2 | seg3 | total |
| --- | --- | --- | --- | --- |
| BTCUSDT | 83/58%/+25.5 | 84/55%/+12.5 | 73/55%/+3.3 | n=240 pnl=+41.37U |
| ETHUSDT | 64/53%/+8.1 | 50/54%/-3.2 | 60/47%/+8.4 | n=174 pnl=+13.22U |
| SOLUSDT | 35/46%/+1.1 | 38/45%/+2.3 | 50/52%/+9.0 | n=123 pnl=+12.31U |
| XRPUSDT | 35/51%/+0.6 | 41/32%/-13.5 | 38/45%/-1.4 | n=114 pnl=-14.25U |
| DOGEUSDT | 36/58%/+7.7 | 36/56%/+12.4 | 32/53%/+7.4 | n=104 pnl=+27.48U |
| 1000PEPEUSDT | 14/79%/+24.7 | 33/52%/+15.7 | 37/30%/-6.0 | n=84 pnl=+34.37U |
| SKYAIUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 29/59%/+20.1 | n=29 pnl=+20.14U |
| XAUUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 52/69%/+5.0 | n=52 pnl=+5.05U |
| XAGUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 27/52%/+2.3 | n=27 pnl=+2.34U |
| CLUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 5/100%/+10.7 | n=5 pnl=+10.66U |