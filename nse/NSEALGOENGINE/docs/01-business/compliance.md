> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Compliance

nsealgo trades the owner's own accounts, in NSE cash-market stocks, through broker APIs. This document maps each rule to the control that enforces it and to the evidence that shows it worked.

> Not legal advice. Rules change. Confirm the current position with your broker and the exchange circulars, and update this document when they change.

## SEBI retail algorithmic-trading framework (mandatory since 1 April 2026)

| Requirement | Control in nsealgo | Evidence |
|---|---|---|
| API orders come from a **static IP registered with the broker** | `brokers.<name>.proxy` and `static_ip`. Before login, the transport checks the egress IP through the proxy and **refuses to trade** if it differs ([ADR-0006](../03-architecture/adr/ADR-0006-static-ip-fail-closed.md)) | `nsealgo netcheck <broker>`; the login log line "orders leave from registered static IP" |
| Below the **order-rate threshold**, unregistered algos are allowed (10 orders/s per broker, as confirmed with the broker) | The OMS token bucket is capped at 10/s per broker unless `registered_algo: true`; multi-account budgets add up per broker ([ADR-0007](../03-architecture/adr/ADR-0007-per-broker-order-budgets.md)) | `oms.orders_per_second`; tests in `test_microstructure_units.py` |
| Above the threshold, the **algo must be registered** through the broker | `registered_algo` stays off until the broker confirms registration | Broker's registration confirmation, kept with the config |
| Orders are **tagged** with the algo or strategy | Every order carries an alphanumeric client ID (≤20 characters) as the broker order tag; Zerodha `algo_id` is supported | Broker order book; `state.db` orders table |
| **Market protection** for market and SL-M API orders (from 1 April 2026) | The OMS sends MARKET as a marketable LIMIT (±0.5%) and SL-M as SL (1% beyond the trigger) before any broker sees it | OMS `_protect`; tests |

## Broker terms

| Rule | Control |
|---|---|
| API access is personal and non-transferable; data is for the account holder's own use | Only the owner's accounts are configured. Pooling family accounts was declined ([ADR-0011](../03-architecture/adr/ADR-0011-no-account-pooling.md)) |
| Fyers: 200 calls/min (Standard plan) across all endpoints; the user is blocked for the day after more than 3 per-minute violations | A global call budget held at 90% (9/s, 180/min), or 540/min with `rate_per_minute: 600` on Prime |
| Fyers order tags: alphanumeric, 1–30 characters, not the client ID | OMS client IDs are uppercase letters and digits, at most 20 characters |
| Upstox `full_d30`: 50 instruments (per connection or per user; documented both ways) | The cap is detected automatically and the deep slots re-ranked ([MICROSTRUCTURE](../03-architecture/MICROSTRUCTURE.md)) |

## Scope limits that keep this personal

- **Stocks only,** with no derivatives ([ADR-0004](../03-architecture/adr/ADR-0004-stocks-only.md)).
- **No trading for others,** and no selling of signals. That would be a separately regulated activity, outside this project.
- **No manual order entry in the desk.** Every order comes from a tested strategy through the risk checks.

## Records

| Record | Where | Kept |
|---|---|---|
| Orders, fills, trades, equity, events | `runs/<mode>/state.db` | Indefinitely ([Backup](../07-operations/backup-and-recovery.md)) |
| Every signal's lifecycle and the day's charts | `runs/<mode>/journal/<date>.jsonl` and `.bars.json` | Indefinitely |
| Market data used | `data/history.db`; `recordings/*.jsonl.gz` | Per retention policy |
| Logs | `logs/` | 90 days suggested |

## Review triggers

Update this document when SEBI or the exchanges publish a circular on algo trading, a broker changes its API terms or limits, or nsealgo adds a broker or an order type.
