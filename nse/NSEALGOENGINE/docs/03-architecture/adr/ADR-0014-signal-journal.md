> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# ADR-0014: Append-only daily signal journal

**Status:** accepted · **Since:** 3.9–3.10

## Context

The desk needs every signal's lifecycle live, after restarts, and for past days.

## Decision

Record signal, order, fill and status events in memory and append them to `journal/<date>.jsonl`; save chart bars every 5 minutes and at day end; reload the day on restart.

## Consequences

Review mode needs no session. Backtest journals are planned next.
