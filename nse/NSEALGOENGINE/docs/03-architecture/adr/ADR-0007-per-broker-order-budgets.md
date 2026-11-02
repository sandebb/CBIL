> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# ADR-0007: Order-rate budgets per broker; sticky stock allocation

**Status:** accepted · **Since:** 2.5

## Context

The owner confirmed that the 10 OPS threshold applies per broker.

## Decision

Each account gets its own token bucket (≤10/s unless registered). In multi-account mode each stock is allocated to one account and stays there; positions never move.

## Consequences

Throughput adds up across brokers. Allocation is persisted in `.cache/multi_state.json`.
