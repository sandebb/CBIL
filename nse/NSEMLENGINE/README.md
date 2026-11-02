> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# NSEMLENGINE

Next-day OHLCV forecasts for the NSE equity universe, adapted from DeepLOB to daily data.
Design: the "DeepLOB → Daily NSE OHLCV: Research Design" doc. Phase 1 built the data set, targets,
walk-forward evaluation and the baselines; phase 2 adds the DeepLOB-style network (`nseml\deep.py`), scored
on the same folds and metrics.

## Where things live

No path is written in the code. Set these once for your Windows user (`setup_env.ps1` does it):

| Variable | Default | What |
|---|---|---|
| `NSEMLENGINE_HOME` | this folder | project root |
| `NSEMLENGINE_WORK_DIR` | `%NSEMLENGINE_HOME%\work` | dataset, predictions, results |
| `NSEDATAENGINE_HOME` | `%NSEMLENGINE_HOME%\..\NSEDATAENGINE` | the data project (source of truth) |

Every input file and column is resolved through NSEDATAENGINE's contract
(`contract\schema.yaml`, read with its `nse_contract.py`). NSEMLENGINE never writes into NSEDATAENGINE,
and reads columns by name, so columns added there later do not affect it.

## New PC

Set up by `%NSE_ROOT%\code\NSEDATAENGINE\migrate\setup_new_pc.ps1` together with the other projects (see
NSEDATAENGINE's MIGRATION.md); this project's steps are in the `"setup"` block of `project_manifest.json`.

## Commands

```
python -m nseml paths        where every folder and input file resolves
python -m nseml build        features_v2 + targets -> work\dataset_v2.parquet   (~1 minute)
python -m nseml folds        list the walk-forward folds
python -m nseml baselines --models naive,ewma,lgbm --last 12 --name phase1
                             walk-forward run (resumable: finished folds are skipped; --fresh re-runs)
nseml.cmd baselines --models deeplob --last 12 --name phase1
                             phase 2 model on the same folds (nseml.cmd uses the GPU venv if set up)
nseml.cmd baselines --models blend --last 12 --name phase1
                             average of deeplob and lgbm (needs both); the report compares both with lgbm
nseml.cmd predict             nightly forecasts for the next session (what the scheduled task runs)
python -m nseml report --name phase1
python tests\test_lookahead.py   proves no feature uses data from after day t
```

## What phase 1 does

* **Prediction time:** after the NSEDATAENGINE 19:30 IST update on day t, for day t+1. Global closes that
  happen after 19:30 IST (US, Europe, NY commodities, USD/INR) are lagged one day (`market_lag` in
  `config.yaml`); Nikkei, Nifty and India VIX of day t are used as they are.
* **Universe (each day, from data up to that day):** 60-day median traded value ≥ ₹1 crore, close ≥ ₹10,
  ≥ 250 days of history, traded that day. 2005-2026: 2.92 M stock-days, 1,854 stocks.
* **Inputs (`nseml\features.py`, 77):** today's bar as log ratios to yesterday's close, multi-horizon
  returns, trend and oscillators as ratios, volatility (EWMA, ATR, Parkinson, wick averages), volume
  ratios and liquidity, skew/kurtosis of returns, 60-day beta/correlation/residual returns, F&O flag,
  calendar, 14 market inputs and 10 daily cross-sectional ranks. All scale-free.
* **Targets (5, next day vs today's close):** gap, close return, upper wick, lower wick (wicks ≥ 0 by
  construction) scaled by the stock's EWMA volatility, and log relative volume. Rupee OHLCV is rebuilt
  as O = C·e^g, C' = C·e^r, H = max(O,C')·e^u, L = min(O,C')·e^−d, so the bar is always valid.
  Moves beyond ±0.4 log in one day are treated as data errors (no target).
* **Walk-forward:** 5-year rolling train, 5-day gap, 63-day validation (early stopping), 63-day test,
  rolled forward a quarter at a time (`walk_forward` in `config.yaml`).
* **Baselines:** `naive` (unconditional quantiles), `ewma` (the same, scaled by each stock's volatility),
  `lgbm` (LightGBM quantile regression, 15 models per fold: 5 targets × P10/P50/P90).
* **Scores (`nseml\metrics.py`):** pinball loss per target, calibration (share of outcomes below each
  quantile), P10–P90 coverage, daily rank IC of the predicted return, R² of relative volume, rupee OHLC
  error of the rebuilt bar, and buy-limit fill rates at the predicted low.

## Phase 1 results (run `phase1`, 12 quarterly test folds, Nov 2023 - Oct 2026)

Full table: `work\results\phase1\summary.md`; per fold: `metrics_by_fold.csv`.

| | naive | ewma | lgbm |
|---|---|---|---|
| pinball gap / return / upper wick / lower wick (σ units) | .102 / .253 / .095 / .081 | .099 / .247 / .089 / .078 | **.095 / .244 / .086 / .074** |
| pinball relative volume | .194 | .194 | **.155** |
| R² relative volume | 0 | 0 | **0.30** |
| daily rank IC: close→close / open→close / gap | - | - | **0.072 / 0.063 / 0.144** |
| P10-P90 coverage (ideal 0.80) | 0.80 | 0.79-0.81 | 0.77-0.78 |

LightGBM beats the volatility baseline in 11-12 of 12 folds on every target. Caution: most of the return
ranking edge sits in the less liquid names; inside the F&O stocks the open→close rank IC is only 0.018
and the gross decile spread is about 5 bp/day. It is not a trading result until it survives costs in the
nsealgo backtester.

## Phase 2: deeplob (`nseml\deep.py`, settings under `deeplob:` in `config.yaml`)

One network for all stocks reads each stock's last 60 days of the 77 inputs: per-feature causal
convolutions (kernels 3 and 5) and a 1×F mix → two dilated causal inception blocks → LSTM → MLP over
[LSTM state, today's inputs] → monotone P10/P50/P90 heads for the 5 targets (81k parameters). Pinball loss
plus a small Huber term, AdamW one-cycle, early stopping on the validation window, models saved per fold
(`work\results\<run>\model_deeplob_<fold>_s0.pt`). Windows never contain later days
(`tests\test_lookahead.py`).

Result on the 12 folds (CPU budget: 150k training windows × ≤ 6 epochs, one seed - settings in
`work\results\phase1\config_deeplob_cloud.yaml`):

| vs LightGBM, per fold | deeplob alone | blend (deeplob + lgbm) / 2 |
|---|---|---|
| gap / return / upper / lower wick / volume pinball: folds better | 2 / 5 / 2 / 3 / 1 of 12 | **9 / 10 / 8 / 11 / 10 of 12** |
| mean change in pinball | +0.4% to +1.1% (worse) | **−0.1% to −0.55% (better)** |
| P10-P90 coverage (ideal 0.80) | 0.77-0.80 (closer) | 0.76-0.80 |
| rank IC close→close | 0.060 | 0.069 (lgbm 0.072) |

On this budget the network alone does not beat LightGBM; averaging the two does, in 8-11 of 12 folds on
every target. Next on the PX13 GPU (`setup_env.ps1 -Gpu`): the default budget (400k windows × 8 epochs),
`ensemble: 5`, and then delivery % once NSEDATAENGINE carries it.

## Nightly forecasts (`nseml\live.py`, settings under `live:` in `config.yaml`)

Scheduled task **NSEMLENGINE-Predict** (Mon-Fri 20:00, `register_tasks.ps1`) runs `run_predict.bat`:

1. waits (up to 90 min) until NSEDATAENGINE has today's session, then rebuilds the dataset (~30 s);
2. re-uses the newest models in `work\live\models\<date>\`, or retrains them when they are 21 trading days
   old: LightGBM (15 models) + deeplob on the last 5 years, early-stopped on the latest 63 days - exactly one
   walk-forward fold (about 20-30 min on a CPU, the first night too);
3. forecasts every stock in the day's universe with the blend (average of LightGBM and deeplob);
4. writes `work\predictions\predictions_<date>.parquet` + `.csv`, and `latest.parquet` / `latest.csv`.

Columns: `open/high/low/close_p10/p50/p90` (rupees), `volume_p10/p50/p90`, `exp_return_pct`, `exp_gap_pct`,
`exp_range_pct`, `rank_return` (0-1 within the day), `is_fno`, `daily_vol_pct`, `median_turnover_60d`, and
the model set used. The P50 bar is always a valid bar. The bands are built from the per-target quantiles:
`low_p10` = the deep end of tomorrow's low, `low_p90` the shallow end - read them as approximate ranges.
Log: `work\logs\predict_log.txt`. A finished day is never redone unless `--force`; `--retrain` trains now.

## Known data gaps (to fix in NSEDATAENGINE, then add here)

* Delivery %, number of trades and true VWAP are not in the contract yet (the design's most valuable
  additions). Add them to NSEDATAENGINE's contract first, then to `features.py`.
* Sector / index membership is not available, so no sector-relative features yet.
* `data_fresh` contains weekend/holiday copy rows; NSEMLENGINE uses the master store (`data`), which
  does not have this problem.
