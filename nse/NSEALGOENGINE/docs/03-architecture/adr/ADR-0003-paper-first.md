> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# ADR-0003: Forward test on live data before live trading

**Status:** accepted · **Since:** 2.0

## Context

Backtests overstate edges; live mistakes cost money.

## Decision

Strategies are forward-tested with the conservative paper exchange on live data, and must pass promotion gates (100 trades, 20 sessions, drawdown ≤ 1.5× backtest) before live use.

## Consequences

Weeks of evidence before capital; every window and the journal work identically in forward tests.
