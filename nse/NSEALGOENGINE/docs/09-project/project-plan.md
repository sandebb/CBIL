> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Project plan

## Delivery so far

| Release | Theme |
|---|---|
| 2.0 | Multi-core research mode; regime and indicator speedups |
| 2.1 | Windows 11 reference platform |
| 2.2–2.3 | Statistical and documented strategies |
| 2.4–2.5 | Scalping and market making; multi-broker allocation with per-broker order budgets |
| 3.0 | Stocks only (derivatives and index/ETF sleeves removed) |
| 3.1–3.2 | Upstox depth; static-IP transport; deeper books and focus ranking |
| 3.3 | GPU compute with measured CPU/GPU selection |
| 3.4 | Order-book signals in strategies; `book-study` |
| 3.5–3.6.1 | Fyers universe and TBT; Upstox 30-level tier; Fyers audit fixes |
| 3.7 | Order broker per product; Fyers default; Shoonya dormant |
| 3.8–3.10 | Trading desk; strategy windows; saved journal and review |
| 3.11 | This documentation suite |

## Current phase

**Forward testing on the PX13.** Run the [acceptance tests](../06-testing/acceptance-tests.md), then forward-test the default strategies until the promotion gates pass.

## Backlog (next iterations)

| Item | Requested | Notes |
|---|---|---|
| Journals for backtests, reviewable in the desk | Yes, next iteration | Research mode runs many engines in parallel; journals need merging per run |
| `book-study` on real recordings → decide on paid depth | — | Before renewing Upstox Plus or a TBT entitlement |
| UPS for the PX13 | — | Risk R07 |
| A second order broker as standby (dormant) | — | Config only |

## How work is managed

Each item becomes a change with tests and documentation, released through the [release process](release-process.md). Decisions are captured as [ADRs](../03-architecture/adr/README.md) and risks in the [risk register](../01-business/risk-register.md).
