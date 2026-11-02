> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# ADR-0009: Use the GPU per kernel only when correct and faster

**Status:** accepted · **Since:** 3.3

## Context

GPU kernel launches cost microseconds; small per-tick batches are faster on the CPU, and ROCm on Windows is new.

## Decision

For each kernel and size bucket, compare GPU results with NumPy, then time both. Use the GPU only if it matches and is over 10% faster. Cache per machine signature; fall back on any error; workers stay on the CPU.

## Consequences

No silent wrong answers; the GPU helps research and universe-scale batches.
