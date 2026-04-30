# Audit: MASR_SHORT candidate `v2slow_cfd_excluded`

_Generated: 2026-04-30T12:10:47.638014+00:00_


**Candidate**: `v2 slow, cfd 排除`

**Logic**: AND

**Rules JSON**:
```json
[]
```

## Status: **STABLE_BUT_THIN**

_3/3 positive but wr_std=5.4pp >= 5.0pp_


## Per-segment metrics

| Segment | n_trades | win_rate | total_pnl |
| --- | --- | --- | --- |
| seg1 | 184 | 41.3% | +4.18U |
| seg2 | 249 | 51.0% | +28.34U |
| seg3 | 139 | 54.0% | +28.34U |
| **total** | **572** | **+20.29U avg** | **+60.86U** |

## Stability metrics

| Metric | Value |
| --- | --- |
| all_positive | True |
| n_negative_segments | 0 / 3 |
| wr_std (pp) | 5.41 |
| pnl_consistency | 0.439 |
| min_n_trades | 139 |
| sign_flip_count | 0 |
| concentration | 46.6% |
| stability_adjusted_pnl | +26.70U |

## Per-coin × per-segment (kept coins only)

| symbol | seg1 (n/wr/pnl) | seg2 | seg3 | total |
| --- | --- | --- | --- | --- |
| BTCUSDT | 34/35%/-0.3 | 59/51%/+1.9 | 24/58%/+3.5 | n=117 pnl=+5.15U |
| ETHUSDT | 38/32%/-1.3 | 33/48%/+3.9 | 22/59%/+2.4 | n=93 pnl=+4.99U |
| SOLUSDT | 42/55%/+5.6 | 46/48%/+7.7 | 13/46%/-0.7 | n=101 pnl=+12.58U |
| XRPUSDT | 29/41%/-0.5 | 43/58%/+12.0 | 25/48%/-0.0 | n=97 pnl=+11.48U |
| DOGEUSDT | 23/43%/+0.8 | 35/46%/+0.7 | 8/50%/+0.2 | n=66 pnl=+1.69U |
| 1000PEPEUSDT | 18/39%/-0.2 | 33/55%/+2.2 | 15/33%/-1.3 | n=66 pnl=+0.62U |
| SKYAIUSDT | 0/0%/+0.0 | 0/0%/+0.0 | 32/66%/+24.4 | n=32 pnl=+24.36U |