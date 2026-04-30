# Audit: BD candidate `bd_baseline`

_Generated: 2026-04-30T09:10:54.051699+00:00_


**Candidate**: `no filter`

**Logic**: AND

**Rules JSON**:
```json
[]
```

## Status: **REJECTED**

_2/3 segments negative_


## Per-segment metrics

| Segment | n_trades | win_rate | total_pnl |
| --- | --- | --- | --- |
| seg1 | 189 | 34.4% | -5.20U |
| seg2 | 208 | 45.7% | +16.71U |
| seg3 | 305 | 40.3% | -12.18U |
| **total** | **702** | **-0.23U avg** | **-0.68U** |

## Stability metrics

| Metric | Value |
| --- | --- |
| all_positive | False |
| n_negative_segments | 2 / 3 |
| wr_std (pp) | 4.61 |
| pnl_consistency | 0.000 |
| min_n_trades | 189 |
| sign_flip_count | 2 |
| concentration | 100.0% |
| stability_adjusted_pnl | -0.00U |

## Per-coin × per-segment (kept coins only)

| symbol | seg1 (n/wr/pnl) | seg2 | seg3 | total |
| --- | --- | --- | --- | --- |
| BTCUSDT | 20/20%/-2.5 | 33/33%/-4.8 | 49/31%/-12.2 | n=102 pnl=-19.46U |
| ETHUSDT | 46/33%/-4.7 | 48/58%/+10.8 | 38/32%/-2.8 | n=132 pnl=+3.20U |
| SOLUSDT | 32/41%/-0.7 | 44/50%/+5.7 | 41/44%/+3.4 | n=117 pnl=+8.38U |
| XRPUSDT | 40/35%/-2.0 | 43/33%/-4.0 | 52/37%/-5.1 | n=135 pnl=-11.12U |
| DOGEUSDT | 31/26%/-4.5 | 24/58%/+7.2 | 42/45%/-2.0 | n=97 pnl=+0.67U |
| 1000PEPEUSDT | 20/55%/+9.3 | 16/38%/+1.8 | 25/52%/+2.7 | n=61 pnl=+13.74U |
| SKYAIUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 17/47%/+0.8 | n=17 pnl=+0.78U |
| XAUUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 29/52%/+2.2 | n=29 pnl=+2.15U |
| XAGUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 12/33%/+1.0 | n=12 pnl=+0.98U |