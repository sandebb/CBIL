> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# ADR-0008: Tiered market depth with focus ranking

**Status:** accepted · **Since:** 3.2

## Context

Deep books are scarce: TBT has 15 symbols at 50 levels; Upstox `full_d30` has 50 or 250 at 30 levels.

## Decision

Stack depth sources deepest first; rank stocks (positions, then candidates round-robin) every 60 s; merge only deeper, fresh, uncrossed books onto quotes.

## Consequences

Deep books go where they matter. Dhan's feed was replaced by Upstox in 3.6.
