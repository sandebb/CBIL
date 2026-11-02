> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# phase1

folds: 12 (2023-11-13 .. 2026-10-01)

| metric | blend | deeplob | ewma | lgbm | naive |
|---|---|---|---|---|---|
| y_g_n.pinball | 0.0942 | 0.0957 | 0.0990 | 0.0946 | 0.1017 |
| y_r_n.pinball | 0.2434 | 0.2458 | 0.2474 | 0.2442 | 0.2533 |
| y_u_n.pinball | 0.0864 | 0.0869 | 0.0893 | 0.0865 | 0.0952 |
| y_d_n.pinball | 0.0737 | 0.0743 | 0.0781 | 0.0740 | 0.0807 |
| y_v.pinball | 0.1546 | 0.1569 | 0.1944 | 0.1553 | 0.1944 |
| y_r_n.coverage | 0.7771 | 0.7717 | 0.7916 | 0.7745 | 0.8043 |
| y_u_n.coverage | 0.7825 | 0.7992 | 0.8051 | 0.7808 | 0.8107 |
| y_d_n.coverage | 0.7900 | 0.7938 | 0.7971 | 0.7846 | 0.8006 |
| y_v.coverage | 0.7872 | 0.7904 | 0.7976 | 0.7739 | 0.7976 |
| y_r.rank_ic | 0.0694 | 0.0598 | -0.0084 | 0.0714 | -0.0470 |
| oc.rank_ic | 0.0562 | 0.0467 | -0.0058 | 0.0620 | -0.0701 |
| y_g.rank_ic | 0.1493 | 0.1335 | -0.0055 | 0.1434 | -0.0873 |
| y_r.hit_rate | 0.5571 | 0.5500 | 0.5319 | 0.5478 | 0.5319 |
| y_v.r2 | 0.3068 | 0.2964 | -0.0073 | 0.2990 | -0.0073 |
| ohlc.mape_open | 0.6684 | 0.6793 | 0.6929 | 0.6695 | 0.6969 |
| ohlc.mape_high | 1.4021 | 1.4017 | 1.4359 | 1.4092 | 1.4806 |
| ohlc.mape_low | 1.4101 | 1.4313 | 1.4595 | 1.4040 | 1.4779 |
| ohlc.mape_close | 1.8181 | 1.8359 | 1.8360 | 1.8202 | 1.8363 |
| fill.low_d10 | 0.8279 | 0.8293 | 0.8294 | 0.8167 | 0.8283 |
| fill.low_d50 | 0.6713 | 0.6729 | 0.6788 | 0.6618 | 0.6674 |
| fill.low_d90 | 0.3698 | 0.3740 | 0.3507 | 0.3651 | 0.3269 |

lgbm rank IC across folds: mean 0.0714, std 0.0281, t 8.05

## deeplob vs lgbm, fold by fold (12 folds)

| target | deeplob better in | mean change vs lgbm | worst fold |
|---|---|---|---|
| y_g_n pinball | 3 of 12 | +1.04% | +3.58% |
| y_r_n pinball | 5 of 12 | +0.66% | +3.14% |
| y_u_n pinball | 2 of 12 | +0.46% | +1.48% |
| y_d_n pinball | 2 of 12 | +0.36% | +1.51% |
| y_v pinball | 2 of 12 | +0.99% | +2.93% |
| y_r.rank_ic | higher in 4 of 10 | -0.0068 | -0.0271 |
| oc.rank_ic | higher in 3 of 10 | -0.0131 | -0.0319 |
| y_g.rank_ic | higher in 3 of 12 | -0.0098 | -0.0526 |
| y_v.r2 | higher in 5 of 12 | -0.0026 | -0.0242 |

## blend vs lgbm, fold by fold (12 folds)

| target | blend better in | mean change vs lgbm | worst fold |
|---|---|---|---|
| y_g_n pinball | 9 of 12 | -0.51% | +0.75% |
| y_r_n pinball | 9 of 12 | -0.32% | +0.62% |
| y_u_n pinball | 9 of 12 | -0.15% | +0.28% |
| y_d_n pinball | 11 of 12 | -0.39% | +0.23% |
| y_v pinball | 11 of 12 | -0.48% | +0.53% |
| y_r.rank_ic | higher in 6 of 10 | +0.0047 | -0.0126 |
| oc.rank_ic | higher in 5 of 10 | -0.0016 | -0.0161 |
| y_g.rank_ic | higher in 9 of 12 | +0.0059 | -0.0204 |
| y_v.r2 | higher in 12 of 12 | +0.0078 | +0.0002 |
