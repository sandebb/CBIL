> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# ADR-0015: Documentation as code, with generated references

**Status:** accepted · **Since:** 3.11

## Context

A single owner across every role needs documentation that survives change.

## Decision

Markdown in `docs/`, organised by role; CLI, configuration and test references generated from the code; `test_docs.py` checks links and freshness.

## Consequences

Docs change in the same commits as code; stale references fail the tests.
