> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# ADR-0010: Desk as a separate process over a local read-only service

**Status:** accepted · **Since:** 3.8

## Context

A GUI in the trading process could stall the engine or invite unsafe actions.

## Decision

The session serves read-only JSON on 127.0.0.1 with a per-session token; the PySide6 desk polls it. The only action is the kill switch.

## Consequences

The GUI can crash without affecting trading. Manual order entry is deliberately absent.
