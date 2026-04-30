# Audit: MASR candidate `top2_RES_LOOK100_RES_TOL0p2_TP1_RR1p5_SL2p5`

_Generated: 2026-04-30T09:40:45.567477+00:00_


**Candidate**: `RES_LOOKBACK=100, RES_TOL_ATR=0.2, TP1_RR=1.5, SL_ATR=2.5`

**Logic**: AND

**Rules JSON**:
```json
[]
```

## Status: **ROBUST**

_3/3 positive, wr_std=4.3pp, min_n=272_


## Per-segment metrics

| Segment | n_trades | win_rate | total_pnl |
| --- | --- | --- | --- |
| seg1 | 281 | 55.9% | +77.15U |
| seg2 | 272 | 46.3% | +1.36U |
| seg3 | 412 | 55.1% | +89.32U |
| **total** | **965** | **+55.94U avg** | **+167.83U** |

## Stability metrics

| Metric | Value |
| --- | --- |
| all_positive | True |
| n_negative_segments | 0 / 3 |
| wr_std (pp) | 4.33 |
| pnl_consistency | 0.304 |
| min_n_trades | 272 |
| sign_flip_count | 0 |
| concentration | 53.2% |
| stability_adjusted_pnl | +51.09U |

## Per-coin × per-segment (kept coins only)

| symbol | seg1 (n/wr/pnl) | seg2 | seg3 | total |
| --- | --- | --- | --- | --- |
| BTCUSDT | 82/59%/+25.9 | 82/49%/+3.1 | 69/57%/+7.2 | n=233 pnl=+36.16U |
| ETHUSDT | 72/57%/+14.8 | 51/55%/-2.4 | 54/43%/+6.6 | n=177 pnl=+19.00U |
| SOLUSDT | 45/51%/+11.0 | 34/41%/+1.6 | 60/57%/+11.9 | n=139 pnl=+24.52U |
| XRPUSDT | 32/53%/+1.7 | 42/26%/-20.3 | 48/48%/+2.6 | n=122 pnl=-15.99U |
| DOGEUSDT | 39/49%/+3.9 | 37/59%/+15.6 | 39/62%/+18.8 | n=115 pnl=+38.34U |
| 1000PEPEUSDT | 11/82%/+19.8 | 26/42%/+3.8 | 36/33%/+2.1 | n=73 pnl=+25.75U |
| SKYAIUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 28/57%/+20.0 | n=28 pnl=+19.99U |
| XAUUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 43/67%/+5.1 | n=43 pnl=+5.12U |
| XAGUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 31/74%/+10.0 | n=31 pnl=+10.00U |
| CLUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 4/100%/+4.9 | n=4 pnl=+4.94U |