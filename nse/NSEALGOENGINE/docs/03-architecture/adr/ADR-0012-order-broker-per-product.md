> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# ADR-0012: Order broker chosen per product; Fyers default; Shoonya dormant

**Status:** accepted · **Since:** 3.7

## Context

After reviewing Fyers' documentation the owner chose Fyers for intraday orders and kept Shoonya configured but inactive.

## Decision

`broker.orders: {intraday, delivery}` selects the broker per product (a `ProductRouter` when they differ); `orders: false` makes a broker dormant on every path.

## Consequences

Switching brokers is a config change, and a dormant broker can't trade by accident.
