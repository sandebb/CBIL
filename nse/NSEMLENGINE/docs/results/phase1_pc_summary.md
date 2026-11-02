> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# phase1_pc

folds: 12 (2023-11-13 .. 2026-10-01)

| metric | blend | deeplob | lgbm |
|---|---|---|---|
| y_g_n.pinball | 0.0942 | 0.0957 | 0.0946 |
| y_r_n.pinball | 0.2433 | 0.2458 | 0.2442 |
| y_u_n.pinball | 0.0864 | 0.0869 | 0.0865 |
| y_d_n.pinball | 0.0737 | 0.0743 | 0.0740 |
| y_v.pinball | 0.1547 | 0.1569 | 0.1553 |
| y_r_n.coverage | 0.7768 | 0.7713 | 0.7745 |
| y_u_n.coverage | 0.7823 | 0.7990 | 0.7808 |
| y_d_n.coverage | 0.7902 | 0.7938 | 0.7846 |
| y_v.coverage | 0.7877 | 0.7902 | 0.7739 |
| y_r.rank_ic | 0.0695 | 0.0602 | 0.0714 |
| oc.rank_ic | 0.0561 | 0.0469 | 0.0620 |
| y_g.rank_ic | 0.1496 | 0.1345 | 0.1434 |
| y_r.hit_rate | 0.5591 | 0.5503 | 0.5478 |
| y_v.r2 | 0.3063 | 0.2962 | 0.2990 |
| ohlc.mape_open | 0.6686 | 0.6796 | 0.6695 |
| ohlc.mape_high | 1.4019 | 1.4014 | 1.4092 |
| ohlc.mape_low | 1.4104 | 1.4315 | 1.4040 |
| ohlc.mape_close | 1.8172 | 1.8356 | 1.8202 |
| fill.low_d10 | 0.8285 | 0.8295 | 0.8167 |
| fill.low_d50 | 0.6719 | 0.6732 | 0.6618 |
| fill.low_d90 | 0.3700 | 0.3745 | 0.3651 |

lgbm rank IC across folds: mean 0.0714, std 0.0281, t 8.05

## deeplob vs lgbm, fold by fold (12 folds)

| target | deeplob better in | mean change vs lgbm | worst fold |
|---|---|---|---|
| y_g_n pinball | 3 of 12 | +1.09% | +3.67% |
| y_r_n pinball | 5 of 12 | +0.65% | +3.23% |
| y_u_n pinball | 2 of 12 | +0.45% | +1.42% |
| y_d_n pinball | 3 of 12 | +0.35% | +1.36% |
| y_v pinball | 2 of 12 | +1.02% | +3.01% |
| y_r.rank_ic | higher in 4 of 10 | -0.0063 | -0.0280 |
| oc.rank_ic | higher in 3 of 10 | -0.0128 | -0.0325 |
| y_g.rank_ic | higher in 3 of 12 | -0.0089 | -0.0501 |
| y_v.r2 | higher in 5 of 12 | -0.0028 | -0.0260 |

## blend vs lgbm, fold by fold (12 folds)

| target | blend better in | mean change vs lgbm | worst fold |
|---|---|---|---|
| y_g_n pinball | 9 of 12 | -0.49% | +0.78% |
| y_r_n pinball | 9 of 12 | -0.34% | +0.66% |
| y_u_n pinball | 9 of 12 | -0.12% | +0.27% |
| y_d_n pinball | 11 of 12 | -0.38% | +0.18% |
| y_v pinball | 10 of 12 | -0.42% | +0.55% |
| y_r.rank_ic | higher in 5 of 10 | +0.0049 | -0.0136 |
| oc.rank_ic | higher in 4 of 10 | -0.0017 | -0.0175 |
| y_g.rank_ic | higher in 9 of 12 | +0.0062 | -0.0191 |
| y_v.r2 | higher in 11 of 12 | +0.0072 | -0.0034 |
