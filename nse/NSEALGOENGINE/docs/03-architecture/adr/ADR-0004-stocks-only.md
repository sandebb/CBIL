> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# ADR-0004: Stocks only; no derivatives

**Status:** accepted · **Since:** 3.0

## Context

The owner decided not to trade futures, options or any derivatives.

## Decision

Remove all derivative code, strategies needing shorts held overnight, and index/ETF sleeves. Keep 17 intraday and swing stock strategies.

## Consequences

A simpler system with fewer failure modes. Overnight shorts aren't possible in the cash market, so swing strategies are long-only.
