> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# ADR-0001: One engine for every mode

**Status:** accepted · **Since:** 2.0

## Context

Separate backtest and live code paths drift apart, and backtests stop predicting live behaviour.

## Decision

Backtests, replays, forward tests and live trading all run `core.engine.Engine`. Only the clock, the feed and the execution object differ.

## Consequences

Backtest results reflect live logic. A backtest is slower than a vectorised one; research mode recovers the speed ([ADR-0005](ADR-0005-parallel-research-mode.md)).
