> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# ADR-0013: Uncertain orders are confirmed, never assumed rejected

**Status:** accepted · **Since:** 3.6.1

## Context

Timeouts, Fyers code 201 (no acknowledgement) and SDK-swallowed errors (−99) leave an order's fate unknown.

## Decision

Adapters raise `OrderUncertain` (not a `BrokerError`). The OMS marks the order UNKNOWN and confirms it from the order book by its tag (Fyers' `1:` prefix stripped).

## Consequences

No untracked live orders, and no duplicates from blind retries.
