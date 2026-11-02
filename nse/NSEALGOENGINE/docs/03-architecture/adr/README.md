> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Architecture decision records

Each record is short and immutable once accepted. To change a decision, add a new ADR that supersedes the old one, and update the status line of the old one.

| ADR | Decision | Since |
|---|---|---|
| [ADR-0001](ADR-0001-one-engine-every-mode.md) | One engine for every mode | 2.0 |
| [ADR-0002](ADR-0002-broker-agnostic-adapters.md) | Broker-agnostic adapter contract | 2.0 |
| [ADR-0003](ADR-0003-paper-first.md) | Forward test on live data before live trading | 2.0 |
| [ADR-0004](ADR-0004-stocks-only.md) | Stocks only; no derivatives | 3.0 |
| [ADR-0005](ADR-0005-parallel-research-mode.md) | Research mode: sleeves × time blocks across cores | 2.0 |
| [ADR-0006](ADR-0006-static-ip-fail-closed.md) | Static IP via proxy, checked fail-closed | 3.1 |
| [ADR-0007](ADR-0007-per-broker-order-budgets.md) | Order-rate budgets per broker; sticky stock allocation | 2.5 |
| [ADR-0008](ADR-0008-tiered-depth.md) | Tiered market depth with focus ranking | 3.2 |
| [ADR-0009](ADR-0009-measured-gpu.md) | Use the GPU per kernel only when correct and faster | 3.3 |
| [ADR-0010](ADR-0010-desk-separate-process.md) | Desk as a separate process over a local read-only service | 3.8 |
| [ADR-0011](ADR-0011-no-account-pooling.md) | No pooling of other people's broker accounts | 3.2 |
| [ADR-0012](ADR-0012-order-broker-per-product.md) | Order broker chosen per product; Fyers default; Shoonya dormant | 3.7 |
| [ADR-0013](ADR-0013-uncertain-orders.md) | Uncertain orders are confirmed, never assumed rejected | 3.6.1 |
| [ADR-0014](ADR-0014-signal-journal.md) | Append-only daily signal journal | 3.9–3.10 |
| [ADR-0015](ADR-0015-docs-as-code.md) | Documentation as code, with generated references | 3.11 |

Template for a new record: copy the structure (Status, Context, Decision, Consequences), take the next number, and add it to this table.
