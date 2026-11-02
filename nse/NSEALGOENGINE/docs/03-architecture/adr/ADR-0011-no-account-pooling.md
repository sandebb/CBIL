> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# ADR-0011: No pooling of other people's broker accounts

**Status:** accepted · **Since:** 3.2

## Context

Family members' accounts could have multiplied per-user data limits.

## Decision

nsealgo uses only the owner's accounts. Each data feed must be the account holder's own.

## Consequences

Deep coverage comes from the owner's own subscriptions (Fyers, Upstox).
