> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# phase1_alt

folds: 12 (2023-11-13 .. 2026-10-16)

| metric | blend | deeplob | lgbm |
|---|---|---|---|
| y_g_n.pinball | 0.0935 | 0.0953 | 0.0940 |
| y_r_n.pinball | 0.2438 | 0.2467 | 0.2444 |
| y_u_n.pinball | 0.0863 | 0.0869 | 0.0864 |
| y_d_n.pinball | 0.0738 | 0.0745 | 0.0740 |
| y_v.pinball | 0.1536 | 0.1560 | 0.1541 |
| y_r_n.coverage | 0.7760 | 0.7724 | 0.7712 |
| y_u_n.coverage | 0.7784 | 0.7913 | 0.7797 |
| y_d_n.coverage | 0.7874 | 0.7931 | 0.7815 |
| y_v.coverage | 0.7872 | 0.7915 | 0.7714 |
| y_r.rank_ic | 0.0718 | 0.0625 | 0.0551 |
| oc.rank_ic | 0.0584 | 0.0514 | 0.0463 |
| y_g.rank_ic | 0.1501 | 0.1290 | 0.1464 |
| y_r.hit_rate | 0.5470 | 0.5418 | 0.5455 |
| y_v.r2 | 0.3111 | 0.3011 | 0.3040 |
| ohlc.mape_open | 0.6669 | 0.6806 | 0.6664 |
| ohlc.mape_high | 1.4043 | 1.4070 | 1.4084 |
| ohlc.mape_low | 1.4058 | 1.4283 | 1.4043 |
| ohlc.mape_close | 1.8196 | 1.8383 | 1.8229 |
| fill.low_d10 | 0.8253 | 0.8192 | 0.8214 |
| fill.low_d50 | 0.6686 | 0.6650 | 0.6658 |
| fill.low_d90 | 0.3709 | 0.3698 | 0.3735 |

lgbm rank IC across folds: mean 0.0551, std 0.0374, t 5.10

## deeplob vs lgbm, fold by fold (12 folds)

| target | deeplob better in | mean change vs lgbm | worst fold |
|---|---|---|---|
| y_g_n pinball | 2 of 12 | +1.52% | +7.25% |
| y_r_n pinball | 3 of 12 | +0.93% | +3.82% |
| y_u_n pinball | 1 of 12 | +0.65% | +1.50% |
| y_d_n pinball | 2 of 12 | +0.68% | +1.83% |
| y_v pinball | 0 of 12 | +1.22% | +3.17% |
| y_r.rank_ic | higher in 5 of 12 | +0.0074 | -0.0405 |
| oc.rank_ic | higher in 5 of 12 | +0.0051 | -0.0347 |
| y_g.rank_ic | higher in 3 of 12 | -0.0175 | -0.0371 |
| y_v.r2 | higher in 6 of 12 | -0.0028 | -0.0344 |

## blend vs lgbm, fold by fold (12 folds)

| target | blend better in | mean change vs lgbm | worst fold |
|---|---|---|---|
| y_g_n pinball | 11 of 12 | -0.45% | +1.89% |
| y_r_n pinball | 8 of 12 | -0.23% | +1.00% |
| y_u_n pinball | 5 of 12 | -0.04% | +0.35% |
| y_d_n pinball | 9 of 12 | -0.31% | +0.30% |
| y_v pinball | 10 of 12 | -0.35% | +0.65% |
| y_r.rank_ic | higher in 9 of 12 | +0.0167 | -0.0161 |
| oc.rank_ic | higher in 7 of 12 | +0.0121 | -0.0254 |
| y_g.rank_ic | higher in 5 of 12 | +0.0037 | -0.0098 |
| y_v.r2 | higher in 10 of 12 | +0.0072 | -0.0072 |
