> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# NSEUIENGINE Monorepo (`code/` & `data/` Mirrored Architecture)

This repository separates executable source code from all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, ML models, backtest runs, and generated reports) using two top-level mirrored directories controlled by a single environment variable: **`NSE_ROOT`**.

## 1. Environment Variable Setup (`NSE_ROOT`)
Set `NSE_ROOT` to the root directory of this repository. All subsystems resolve their code (`$NSE_ROOT/code/<ENGINE>`) and data/logs (`$NSE_ROOT/data/<ENGINE>`) relative to `NSE_ROOT` with zero hardcoded paths:
- **PowerShell:** `$env:NSE_ROOT = (Get-Location).Path`
- **Command Prompt (`.bat`):** `set NSE_ROOT=%CD%`
- **Linux / macOS:** `export NSE_ROOT=$(pwd)`

## 2. Mirrored Top-Level Directory Layout
- **`code/`** — Contains **only source code, static configurations, tests, and documentation** (no logs, databases, or Parquet files):
  - `code/NSEDATAENGINE/`
  - `code/NSEMLENGINE/`
  - `code/NSEALGOENGINE/`
  - `code/NSEUIENGINE/`
  - `code/Fyerstbt/`
- **`data/`** — Mirrors the exact subsystem hierarchy of `code/` and stores **all logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, backtest runs, and generated reports** (tracked as empty `.gitkeep` skeletons in Git):
  - `data/NSEDATAENGINE/` (`data/`, `data_fresh/`, `data_fresh_2019/`, `data_ref/`, `backtest_results/`, `signals/`, `tbt/`, `logs/`)
  - `data/NSEMLENGINE/` (`work/`, `work_alt/`, `work_alt_close*/`, `work_alt_gpu/`, `work_alt_intraday*/`, `work_alt_link*/`, `work_orig_gpu/`, `logs/`)
  - `data/NSEALGOENGINE/` (`data/minute_archive/`, `runs/`, `recordings/`, `cache/`, `logs/`)
  - `data/NSEUIENGINE/` (`reports/`, `state/`, `logs/`)
  - `data/Fyerstbt/` (`ticks/`, `state/`, `logs/`)

---

# NSEUIENGINE
Trading UI - Testing
# Monorepo Overview

This repository integrates the core systems required for trading data processing, machine learning pipeline execution, algorithmic trading strategy execution, user interface components, and broker integration.

## 1. Core Subsystems

- `NSEDATAENGINE/`: Data engine responsible for data ingestion, market data updates, historical data downloading, caching, and raw data processing.
- `NSEMLENGINE/`: Machine learning engine handling model training, feature engineering, pipeline evaluation, and ML prediction workflows.
- `NSEALGOENGINE/`: Algorithmic trading engine managing strategy execution, backtesting engine, signal generation, and automated trade execution.
- `NSEUIENGINE/`: User interface engine providing UI components, dashboard tools, visualization, and front-end interaction layers for trading activities.
- `Fyerstbt/`: Fyers broker integration and backtesting layer managing authentication, token management, backtesting support, and live order management.

## 2. Contract 1.8 Schema Enforcement

Contract 1.8 enforces strict data schema compliance across data pipelines:
- `columns()`: Defines and returns the mandatory column schema specifications required for dataset validation.
- `conform()`: Validates and converts incoming dataframes to conform strictly to Contract 1.8 schema requirements.
- `read()`: Safely reads and parses data files while applying schema enforcement and validation rules.

## 3. Key Environment Variables

- `NSEDATAENGINE_HOME`: Root directory path for data engine storage, configuration, and raw data cache.
- `NSEMLENGINE_HOME`: Base path for ML engine models, features, training artifacts, and pipeline configs.
- `NSEALGOENGINE_HOME`: Configuration and execution directory for algorithmic trading scripts and logs.
- `FYERS_ACCESS_TOKEN`: Authentication token for accessing Fyers trading API services.
- `FYERS_APP_ID`: Application ID registered with Fyers for OAuth authentication and API requests.

## 4. Main CLI Entrypoints

- `nsealgo`: CLI command to trigger and control algorithmic trading execution and strategy management.
- `python -m nseml`: CLI module entrypoint for running ML model training, feature generation, and evaluation workflows.
- `fyers_token_manager.py`: Utility script to manage, generate, and refresh Fyers API access tokens.
- `nse_updater.py`: Data update script for downloading and refreshing latest market data.
