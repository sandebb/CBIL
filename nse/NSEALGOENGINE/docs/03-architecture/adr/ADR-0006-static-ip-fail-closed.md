> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# ADR-0006: Static IP via proxy, checked fail-closed

**Status:** accepted · **Since:** 3.1

## Context

SEBI requires API orders from a registered static IP; StaticIP.in delivers one as an HTTP proxy.

## Decision

Each order broker's REST traffic goes through `brokers/transport.BrokerTransport` with its configured proxy. Before login, the egress IP is checked through the proxy, and any mismatch refuses login.

## Consequences

Wrong-IP orders are impossible from nsealgo; a proxy outage stops trading rather than leaking orders.
