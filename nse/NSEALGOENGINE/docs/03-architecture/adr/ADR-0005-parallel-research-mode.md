> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# ADR-0005: Research mode: sleeves × time blocks across cores

**Status:** accepted · **Since:** 2.0

## Context

Single-process backtests left 15 of 16 cores idle.

## Decision

Split work by strategy sleeve and time block, with hold-aware warm-up and run-off. Merge equity by mark-to-market. Verify identical trades against one process.

## Consequences

Near-linear speedups; a parity test guards correctness.
