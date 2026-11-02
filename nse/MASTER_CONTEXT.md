> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# MASTER_CONTEXT — NSE Trading System (handover document)

**Owner:** Sandeep · **Location:** `%NSE_ROOT%\data\` on the HP PC (4 cores, 16 GB RAM, Windows 11, Python 3.14) · **Replacement PC planned:** PX13 (Radeon 8060S GPU)
**State as of:** 18 Oct 2026 · **Copies:** `%NSE_ROOT%\data\MASTER_CONTEXT.md` and the same file in each of the four project folders.

This document covers four projects that work together:

| Project | Folder | One-line role |
|---|---|---|
| **NSEDATAENGINE** ("DATA") | `%NSE_ROOT%\code\NSEDATAENGINE` | Source of truth for all market data (daily prices, alt data, pre-open, at-close). Owns the data **contract**. |
| **NSEMLENGINE** ("ML") | `%NSE_ROOT%\code\NSEMLENGINE` | Next-day OHLCV forecasts (P10/P50/P90) for every stock: LightGBM + DeepLOB blend, walk-forward tested. |
| **NSEALGOENGINE** ("ALGO", package `nsealgo` v3.11) | `%NSE_ROOT%\code\NSEALGOENGINE` | Strategies, backtester, forward test, live execution, OMS, brokers, research scripts, position sizing. |
| **NSEUIENGINE** ("UI") | `%NSE_ROOT%\code\NSEUIENGINE` | Daily Fyers token, swing trader (1-share live trades), reporting/tracking tools. |

---

## 1. Core goal and high-level architecture

### Goal
An automated trading system for **NSE cash-market equities** (stocks only — futures/options were removed in v3.0). It researches, backtests, paper-trades and live-trades intraday and swing strategies, and uses ML forecasts of the next session's open/high/low/close/volume to rank stocks. The current research focus is an **overnight trade**: buy the top-ranked predicted gap-up stocks in the 15:40–16:00 post-close session (fills at the official close) and sell them the next morning, sized by the new volume-aware sizing model. **Universe (19 Oct 2026):** no price floor (was ₹50); trade-for-trade (BE/BZ) and ASM/GSM-listed stocks are excluded (₹50 floor returns if the list is missing). **Exit rule (switched 19 Oct 2026, user accepts the risk):** if the ML's predicted close is above the 09:07 indicative open, limit-sell at open + 75% of the way to the predicted high from 09:15 and sell the rest at market at 12:00; otherwise sell in the pre-open auction (`nsealgo exit-overnight`).

### Data flow

```
                 NSE / Fyers / Yahoo
                        │
        ┌───────────────▼────────────────┐
        │ NSEDATAENGINE  (19:30 update)  │  raw_ohlcv, market, features, data_ref\* (delivery, deals,
        │  contract\schema.yaml = truth  │  insider, short_selling, mtf, sectors, F&O universe ...),
        │  pre-open 09:00 / at-close 15:30│  preopen\YYYY-MM-DD, at_close\YYYY-MM-DD
        └───────┬───────────────┬────────┘
                │ (read through nse_contract.py)
   ┌────────────▼───────┐   ┌───▼──────────────────────────┐
   │ NSEMLENGINE        │   │ NSEALGOENGINE (nsealgo)      │
   │ build → walk-fwd → │──►│ strategies, OMS, brokers,    │
   │ predict (night)    │   │ backtest / forward / live,   │
   │ predict-close 15:3x│   │ research scripts, sizing     │
   └────────────────────┘   └───────────┬──────────────────┘
                                         │ orders (Shoonya; Fyers = data only)
   ┌─────────────────────────────────────▼──┐
   │ NSEUIENGINE: Fyers token 08:00, swing   │
   │ trader, reports, ML accuracy tracking   │
   └─────────────────────────────────────────┘
```

### Daily timeline (IST, Mon–Fri, Windows Task Scheduler)

| Time | Task | Project |
|---|---|---|
| 08:00 (+ logon) | `NSEUIENGINE-FyersToken` — refresh Fyers token into Windows env vars | UI |
| 08:58 | `NSEDATAENGINE-PreOpen` — record the pre-open auction (fast polling 09:06:00–09:07:10) | DATA |
| 14:57 | `NSEDATAENGINE-AtClose` — Fyers quote snapshots at 15:00:00 and 15:30:15 (close = 15:00–15:30 VWAP) | DATA |
| 15:3x | `predict-close` tasks — 15:37 forecast from the snapshot (original + ALT-CLOSE 15:33 + ALT-LINK 15:35 when registered) | ML |
| 19:05 | `NSEALGOENGINE-MinuteArchive` — add the day's 1-minute bars to `data\minute_archive` + liquidity table (register: `deploy\windows\register_minute_archive_task.ps1`) | ALGO |
| 19:30 | NSEDATAENGINE daily update (bhavcopy, alt data, features) | DATA |
| 20:00 / 20:40 / 21:20 / 22:00 | Nightly `predict`: original / ALT / ALT-CLOSE / ALT-LINK (retrain every 21 trading days) | ML |
| 22:00 / Sat 10:00 | `NSEUIENGINE-SignalLog`, `NSEUIENGINE-EdgeReport` | UI |

Tasks use **Password logon + WakeToRun** so they run while logged off.

---

## 2. Tech stack and versions

**Python 3.14 (system, PC).** The Cowork Linux VM used for testing has Python 3.10, pandas 2.3, polars 2.0, LightGBM 4.7; code must run on both.

| Package | Version on PC (NSEALGOENGINE `.venv`) | Used by |
|---|---|---|
| numpy | 2.5.4 | all |
| pandas | **3.0.6** (copy-on-write, `str` dtype — code must be pandas-3 safe) | ALGO, DATA backtest |
| pyarrow | 26.0.0 | parquet everywhere |
| scipy 1.18.1, statsmodels 0.15.0, hmmlearn 0.3.3 | | ALGO regime / stats |
| typer 0.27.3 | | `nsealgo` CLI |
| PyYAML 6.0.3, requests 2.31, plotly 7.1.0, psutil, threadpoolctl | | ALGO |
| fyers-apiv3 **3.2.0** (must be ≥ 3.1.19; older corrupt TBT depth) | | ALGO data / TBT |
| NorenRestApiPy 0.0.22 | | Shoonya orders |
| polars ≥ 1.21 (`min_samples` argument names) | | ML, DATA |
| lightgbm ≥ 4.0 (use the native `lgb.train` API — scikit-learn may be missing) | | ML |
| torch ≥ 2.4 (CPU build; ROCm build on PX13 via `setup_env.ps1 -Gpu`) | | ML DeepLOB |
| yfinance | | DATA (market indices, Nikkei) |
| pyotp 2.10, requests 2.34 | NSEUIENGINE `.venv` | UI token |
| openpyxl (+ LibreOffice for formula recalculation) | not in ALGO venv — `pip install openpyxl` for workbooks | research workbooks |

Brokers: **Shoonya (Finvasia) for orders** (delivery CNC + intraday MIS; dormant until enabled), **Fyers for data** (quotes, 1-minute history, TBT 50-level depth, token via NSEUIENGINE). Other adapters exist (Zerodha, Upstox depth, Dhan, Angel, ICICI, Kotak, 5paisa, AliceBlue, Groww, Yahoo, paper).

---

## 3. File structure

### 3.1 NSEDATAENGINE
| Path | What it does |
|---|---|
| `contract\schema.yaml` | **Single source of truth** for every dataset, file location and column (version 1.8). Change data here first. |
| `nse_contract.py` | Reads the contract: `file_path()`, `location()`, `read()`, `validate()`. Imported by ML (and optionally ALGO). |
| `nse_updater.py` | Daily update of the equity dataset from NSE (history 1991 →). `run_update.bat`. |
| `nse_common.py` | Shared formulas/rules for the updater and fresh build (features, adjustments). |
| `nse_fresh_build.py` | Rebuild a fresh 5-year dataset straight from NSE with an audit trail. |
| `nse_alt_data.py` | Alt datasets into `data_ref\`: delivery, deals, insider, pledge, shareholding, surveillance, index changes, **short_selling, mtf** (new). `daily` / `backfill --start 2019-01-01 [--only ...]` / `show`. |
| `nse_preopen.py` | Pre-open auction recorder (`record` / `once` / `report` / `show` / `reparse`) → `data\preopen\YYYY-MM-DD.parquet` + raw JSON (60 days). `run_preopen.bat`. |
| `nse_close_snapshot.py` | 15:00 / 15:30:15 Fyers snapshots → `data\at_close\YYYY-MM-DD.parquet` (close = 30-min VWAP) for the 15:37 forecast. `run_at_close.bat`. |
| `nse_fno_universe.py`, `nse_sectors.py` | Daily F&O list; sector of every stock. |
| `nse_signals.py`, `bt_strategies.py`, `nse_backtest.py`, `nse_portfolio.py` | v3.11 swing signals for the next session; whole-universe daily backtest; portfolio money management. |
| `data\` | `raw_ohlcv.parquet` (split/bonus back-adjusted), `market.parquet`, `features_output_clean.parquet`, `preopen\`, `at_close\` (from 19 Oct). |
| `data_ref\` | Alt data parquet files (short_selling/mtf appear after the backfill). |
| `migrate\` | PC move & setup scripts: `pack_project.ps1`, `unpack_project.ps1`, `setup_new_pc.ps1`, `set_env_vars.ps1`, `register_project.ps1`, `register_preopen_task.ps1`, `set_time_sync.ps1`, `move_to_shared_data.ps1`. |
| `MIGRATION.md`, `project_manifest.json` | Move-to-new-PC instructions / file manifest. |

### 3.2 NSEMLENGINE
| Path | What it does |
|---|---|
| `nseml\__main__.py` | CLI: `paths`, `build`, `folds`, `baselines`, `report`, `rescore`, `predict`, `predict-close`, `close-check`, `link-map`. `--config` handled before imports. |
| `nseml\config.py` | Settings + paths from env vars (`NSEMLENGINE_HOME`, `NSEMLENGINE_WORK_DIR`, `NSEMLENGINE_CONFIG`, `NSEDATAENGINE_HOME`). ALT models can never use `work\`. |
| `nseml\data.py` | Reads NSEDATAENGINE through the contract; `load_alt()` (delivery/short/MTF, point-in-time), `load_market(extra=)` for the 15:37 row. |
| `nseml\features.py` | 77 scale-free features + targets (gap, return, wicks, volume, vol-scaled) → `dataset_v2.parquet`. Optional blocks: ALT (10), NOWCAST (2), GAP (39, unused), LINKS (12 + mined pairs). |
| `nseml\nowcast.py` | Today's delivery-% estimate at 15:37 (LightGBM, out-of-sample yearly walk-forward). |
| `nseml\links.py` | ALT-LINK: 12 hand-built delivery interaction columns + data-mined "system map" pairs (frozen `link_map.json`). |
| `nseml\baselines.py`, `deep.py`, `metrics.py`, `splits.py` | LightGBM quantile models; DeepLOB adapted to daily data; pinball/IC/coverage metrics; walk-forward folds (5-yr rolling train, 63-day val/test, quarterly step). |
| `nseml\live.py` | Nightly forecasts (`predictions_YYYYMMDD.*`, `latest.csv`), 15:37 at-close forecasts (`latest_close.csv`), `close_check`. |
| `nseml\openmodel.py` | **Abandoned** 4th OPEN model (CLI hooks removed). Safe to delete. |
| `config.yaml` | Original model. `config_alt.yaml` (+delivery/short/MTF), `config_alt_close.yaml` (N-1 alt data + nowcast, usable 15:37), `config_alt_link.yaml` (ALT-CLOSE + links), `config_alt_gap.yaml` (**superseded**), `config_open.yaml` (**abandoned**). |
| `nseml.cmd`, `nseml_alt.cmd`, `nseml_alt_close.cmd`, `nseml_model.cmd MODEL cmd` | Wrappers that set config + work folder (`nseml_model.cmd` forwards args verbatim — commas preserved). |
| `run_*.bat`, `register_*.ps1` | Scheduled-task entry points / registration (`register_model_tasks.ps1 -Model alt_link`). |
| `run_alt_walkforward.bat` | Builds + 12-fold walk-forward for ALT, ALT-CLOSE, ALT-LINK, then comparisons. |
| `compare_models.py` | Fold-by-fold comparison A vs B → `compare_<A>_vs_<B>.txt`. |
| `tests\test_lookahead.py` | No-look-ahead / target / DeepLOB-causality tests. |
| `work\`, `work_alt*\` | Datasets, results (`results\phase1`, `phase1_pc`), live models, predictions. |

### 3.3 NSEALGOENGINE (`nsealgo` 3.11)
| Path | What it does |
|---|---|
| `src\nsealgo\cli.py` | All commands (`doctor`, `backtest`, `walkforward`, `forwardtest`, `live`, `desk`, `kill`, `fetch-history`, `fetch-minute-archive`, `minute-archive-status`, `tbt-record`, `book-study`, **`liquidity-table`**, **`size-overnight`**, **`exit-overnight`**, ...). |
| `src\nsealgo\core\` | Engine, OMS, risk (books, kill switch, daily loss limits), costs, regime (HMM), portfolio, journal, indicators. |
| `src\nsealgo\brokers\` | Broker adapters, routing (orders → Shoonya, data → Fyers), multi-account, static-IP transport. |
| `src\nsealgo\strategies\` | 17 intraday/swing strategies (ORB, PEAD, momentum, pullback, VWAP fade, microstructure, ...). |
| `src\nsealgo\data\` | History DB (`data\history.db`), feeds, depth, Fyers TBT recorder, **`minute_archive.py`** (1-minute parquet archive 2018 →, 90-day spans). |
| `src\nsealgo\sizing\overnight_exit.py` | **New (19 Oct).** The morning sale: hold/limit/auction decision per stock from the plan CSV, the pre-open recorder and the ML P50 high/close → `runs\overnight_exit_<day>.csv` + order list (no orders sent). |
| `src\nsealgo\sizing\liquidity.py` | **New.** Per-stock size of the post-close session and pre-open auction (share of daily volume) → `data\liquidity_table.parquet`. |
| `src\nsealgo\sizing\overnight.py` | **New.** Overnight sizing model: edge model, caps, water-filling allocation, `plan_tonight()`. |
| `src\nsealgo\backtest\`, `forwardtest\`, `desk\`, `monitor\`, `parallel\`, `compute\` | Backtester, paper-on-live, PySide desk GUI, monitoring, multi-core jobs, GPU/CPU kernels. |
| `config\system.yaml` | Main configuration (brokers, risk, costs, sessions, paths). `config\universes\` incl. `liquid_union_2018.csv` (899 stocks). |
| `scripts\research\` | `ml_overnight_backtest.py`, `ml_gap_topn.py`, `rank_topn_intraday.py`, `rank_topn_test.py`, `opening_window_test.py`, `ml_range_orders.py`, `ml_range_directional.py`, `rsi_intraday_selection.py`, `tbt_event_study.py`, `fyers_history_depth.py`, **`overnight_sizing_backtest.py`**, **`overnight_sizing_workbook.py`**. |
| `scripts\windows\` | `setup.ps1`, `setup_shoonya.ps1` (`-TestOrder -Enable`), `morning.ps1`. |
| `runs\` | Research outputs: `ML_overnight_backtest_full.xlsx`, `ML_overnight_sizing.xlsx`, `overnight_sizing*.csv/.txt`, `overnight_edge.json`, candidates parquet, rank/gap test reports. |
| `docs\` | Full documentation suite (10 sections, 15 ADRs); `tests\unit\test_docs.py` keeps references current. |
| `tests\unit\` | Unit tests incl. `test_overnight_sizing.py` (9 tests). |

### 3.4 NSEUIENGINE
| Path | What it does |
|---|---|
| `fyers_token_manager.py`, `setup_fyers_token.ps1` | Daily Fyers token (refresh token → TOTP login → browser) into env vars `FYERS_ACCESS_TOKEN`, `FYERS_WS_TOKEN`, `FYERS_TOKEN_DATE`, ... |
| `fyers_token.py` | Helper any script imports to read today's token. |
| `swing_trader.py`, `setup_swing_trader.ps1`, `go_live.ps1` | Live v3.11 swing signals on Fyers, 1 share per signal. |
| `preflight.py`, `set_static_ip.ps1`, `SET_STATIC_IP.cmd` | Pre-live checks, static-IP proxy. |
| `holdings.py`, `whatif.py`, `edge_report.py`, `signal_ml_tracker.py`, `ml_accuracy.py` | Holdings, "what-if all signals", weekly edge after costs, signal vs ML log, ML forecast accuracy. |
| `register_eod_tasks.ps1`, `make_tasks_run_logged_off.ps1` | Task registration (SignalLog 22:00, EdgeReport Sat 10:00); convert tasks to run logged off. |

`%NSE_ROOT%` root also holds older one-off scripts (`backtest_engine.py`, `run_pipeline_parquet*.py`, `validate_*.py`, ...) from earlier work — **not part of the four projects**.

---

## 4. Current state of development

### Works (tested and/or running daily)
- **DATA:** daily updater, contract 1.8 + validator (handles per-day files), alt datasets (delivery etc.), F&O universe, sectors; pre-open recorder registered (first full live recording Mon 19 Oct; one test snapshot for 16 Oct exists).
- **UI:** Fyers token task (08:00), swing trader, reports.
- **ML original model:** 77 inputs, walk-forward `phase1`/`phase1_pc` (12 folds Nov 2023 – Oct 2026, ~3.2 h on the HP), nightly forecasts, `predict-close` code, `close-check`. Look-ahead tests pass.
- **ALGO:** engine, backtest/forward/live plumbing, Shoonya routing (dormant), minute archive downloader (resumable; `no_data` / BE-BZ handling fixed), research scripts listed above.
- **Overnight research:** full backtest 718 nights (fixed ₹1 lakh/stock, locked-at-upper-band stocks excluded) + Excel workbook with scenarios (skip Friday / weekend), red minus figures, compounding sheet.
- **Overnight sizing model (new, 18 Oct):** `nsealgo size-overnight --capital 500000 --top 5` (or no `--top` = model picks from top 40). Verified: KKT optimality on sampled nights, backtest positions recompute exactly, 9 unit tests, pandas 3.0 synthetic run. Backtest (658 nights, ₹5 lakh, top 5): equal split ₹707/night vs optimized ₹1,269/night after charges; slippage ₹431 → ₹280.

### Half-done / waiting on a run
- **Short-selling / MTF backfill** — code done, not yet run on the PC: `python nse_alt_data.py backfill --start 2019-01-01 --only short_selling,mtf` (in NSEDATAENGINE). Blocks the ALT models (the `paths` check and ALT-LINK map refuse to proceed without it).
- **ALT / ALT-CLOSE / ALT-LINK models** — code complete and tested on a VM subset; full builds + walk-forwards not yet run: `run_alt_walkforward.bat` (≈3.5–4 h per model). Then register tasks: `register_alt_task.ps1`, `register_alt_close_tasks.ps1`, `register_model_tasks.ps1 -Model alt_link`, `register_close_tasks.ps1`. Keep/discard rule agreed in advance: keep a model only if it beats its reference on close-to-close or open-to-close rank IC in ≥ 8 of 12 test quarters without worse pinball.
- **At-close snapshot / 15:37 forecast** — first real run Monday 19 Oct 15:00; live Fyers path not yet proven.
- **Minute archive** — 455 of 899 stocks downloaded; rerun `nsealgo fetch-minute-archive` until complete, then rerun `scripts\research\overnight_sizing_backtest.py` (refreshes liquidity table + edge model; repeat monthly).
- **Auction-impact coefficient (KAPPA = 0.5)** — a prior; calibrate from a few weeks of pre-open recordings.
- **Shoonya live overnight step** — stays OFF. Only after the user tests a 1-share post-close order (`product C`, `MKT`, `DAY`, `amo NO`; avoid EOS retention) and explicitly asks to enable it.

### Broken / superseded / to clean up
- Nothing known broken. Superseded or abandoned: `config_alt_gap.yaml` (replaced by ALT-LINK), `nseml\openmodel.py`, `config_open.yaml`, `work_open\` (contains a test frame), old `work\results\compare_original_vs_alt.txt`. Deletion needs the user's permission (deletes in connected folders are disabled by default).
- Known data quirks: Fyers' 09:15 bar included the auction volume Nov 2023 – Sep 2025 only; a few stocks have volume mismatches around corporate actions (handled in `liquidity.py`); `close_lock_bars.parquet` covers only recent history.db days (older days use the price-band rule).

### Pending research (to-do list)
- **Overnight exit at a target (done 19 Oct 2026):** `scripts\research\overnight_exit_backtest.py`. Midpoint target for every stock loses (₹1,237 → ₹867/night, optimized top 5, ₹5 lakh); waiting only when ML close > actual open wins on all 8 gated rules, best "75% to high, else at 12:00" ₹1,529/night but t = 1.5 and mostly 2025. Adopted as the exit rule; check it in paper/live results over the next 1–2 months.
- **Overnight trade, flexible exit (added 19 Oct 2026):** instead of always selling at the next pre-open, use other parameters/features (e.g. ML forecast for the next days, gap size, delivery %, volume, trend, opening-auction behaviour) to decide each morning whether to sell or hold for more days; backtest against the sell-next-open rule with the same charges, slippage and sizing.
- Delivery ratio vs RSI vs volatility relationship.
- Do short-selling / MTF data have predictive value?
- Paper-track the ML open→close short list; pre-open report after 3–4 weeks; point opening-window / top-N tests at the minute archive; optional MTO parser for pre-2019 delivery; a 15:00 forecast (buy 15:00–15:30 VWAP) if the overnight trade must scale beyond ~₹50 lakh.

---

## 5. Rules and conventions established in the session

**Safety (non-negotiable)**
1. Never place trades or move money on the user's behalf. Sizing outputs are **files to review** (`overnight_orders_<day>.csv`); live order steps stay off until the user enables them himself.
2. Secrets: Fyers secrets were exposed in chat once — rotate them. Shoonya password/secrets sit in plain user env vars. Never print, log or commit credentials.
3. Do not delete files in `%NSE_ROOT%` without explicit permission; superseded files are listed instead.

**Engineering**
4. **Keep working models untouched.** New ideas become parallel models (own `config_<model>.yaml` + `work_<model>\`), compared fold by fold; discard what adds no value.
5. **No look-ahead.** Every input must be known at decision time (19:30 for nightly, 15:37 for at-close). Alt data at N-1 for 15:37 models; walk-forward only; frozen artefacts (e.g. `link_map.json`) drawn only from pre-test data. Run `tests\test_lookahead.py` after feature changes.
6. **Data contract first:** every NSEDATAENGINE file/column is defined in `contract\schema.yaml`; other projects read via `nse_contract.py`, never hard-coded paths. No hard-coded paths in general — env vars with defaults.
7. Back up a file before major edits as `<file>.bak_pre_<change>`; add a CHANGELOG entry (ALGO).
8. Code must run on **pandas 3 / numpy 2.5** (PC) and pandas 2.3 (VM): typed empty frames, no chained assignment, no deprecated APIs. Use native `lgb.train` (scikit-learn may be missing).
9. Batch (`.cmd/.bat`) files use **CRLF** line endings; forward arguments with `%*` (never `%1..%9` — commas split `lgbm,deeplob,blend`).
10. Scheduled tasks: Password logon + WakeToRun, logs to `<work>\logs\scheduled_runs.txt`.
11. Walk-forward baselines: use `--last 12` (not `--last 0`).
12. Costs used everywhere: Shoonya delivery ≈ 23.3 bps per ₹1 lakh round trip (STT 0.1% × 2, stamp 0.015%, NSE 0.00297%, SEBI ₹10/cr, GST 18%, DP ₹9 + GST = ₹10.62); intraday ≈ 9.2 bps incl. slippage.
13. Long jobs on the Cowork VM must finish within ~175 s per call (cache and chunk work).

**Output / communication style**
14. Plain language, short sentences, no jargon in user-facing text and tables; **clear, self-explaining table headers** (the user found compact headers confusing).
15. Spreadsheets: Arial 10, blue inputs on yellow, formulas where Excel can recalculate, losses shown with a minus sign in red (`#,##0;[Red]-#,##0;0`), dates `dd-mmm-yyyy`.
16. Rupee figures in ₹ / lakh / crore; show worked examples on a real night when explaining a model.
17. Results must be net of all charges and state the period, number of nights and assumptions (fills, slippage).
