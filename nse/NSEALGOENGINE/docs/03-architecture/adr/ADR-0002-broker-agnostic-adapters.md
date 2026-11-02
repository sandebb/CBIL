> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# ADR-0002: Broker-agnostic adapter contract

**Status:** accepted · **Since:** 2.0

## Context

Brokers differ in symbols, products, statuses and limits; strategies must not care which broker is used.

## Decision

Every broker implements `brokers/base.BrokerAdapter`, with a symbol master that maps broker symbols to neutral keys (`NSE:SBIN`) and a fixed status mapping.

## Consequences

Adding a broker is local work ([guide](../../04-design/broker-adapter-guide.md)). Broker-specific features (TBT, `full_d30`) live behind optional interfaces.
