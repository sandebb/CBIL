> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Data architecture

## Core entities (`core/models.py`, `core/journal.py`)

| Entity | Key fields | Notes |
|---|---|---|
| `Instrument` | key `NSE:SBIN`, token, broker_symbol, lot, tick | From each broker's symbol master; keys are broker-neutral |
| `Quote` | ltp, volume, bid, ask, sizes, circuits, OHLC, prev_close, `bids` / `asks` | Depth tuples `(price, qty[, orders])`, best first, 5 to 50 levels |
| `Bar` | key, ts (start, IST), OHLCV, ofi, interval | 1-minute live bars; daily bars from history |
| `OrderRequest` | key, side, qty, price, type, product, strategy, intent, meta | `meta`: stop, reason, risk |
| `Order` | client_id, req, broker_id, status, filled_qty, avg_price, created, updated | Status: NEW, OPEN, TRIGGER_PENDING, PARTIAL, FILLED, CANCELLED, REJECTED, UNKNOWN |
| `Fill` | client_id, ts, price, qty | |
| `PosState` | strategy, key, product (MIS/CNC), qty, avg, entry_ts, realized | One per strategy sleeve and stock |
| `SignalRec` | strategy, key, side, intent, signal_ts, ref_price, client_id, order_ts, status, fill_ts, fill_price | The journal's unit ([ADR-0014](adr/ADR-0014-signal-journal.md)) |

## Stores

| Store | Path | Format | Written by | Read by |
|---|---|---|---|---|
| Session state | `runs/<mode>/state.db` | SQLite (WAL): `orders`, `fills`, `trades`, `results`, `equity`, `events`, `kv`, `regime` | Engine, OMS | Reports, restarts |
| Signal journal | `runs/<mode>/journal/<date>.jsonl` | Append-only JSON lines: `signal`, `fill`, `status` events | Engine | Desk (live and review), restarts |
| Chart bars | `runs/<mode>/journal/<date>.bars.json` | `{key: [[epoch, o, h, l, c, v], …]}` for scrips with signals | Engine (5 min, end of day) | Desk review |
| History | `data/history.db` | SQLite (WAL): daily and 1-minute bars | `fetch-history`, sessions | Backtests, research, swing charts |
| Recordings | `recordings/<date>.jsonl.gz` | One quote per line, depth trimmed to `feeds.record_depth` | Forward and live sessions | `replay`, `book-study` |
| Broker cache | `.cache/<broker>/` | Session tokens (mode 0600), symbol masters | `login`, adapters | Adapters |
| Allocation table | `.cache/multi_state.json` | Stock → account; open owners | Multi-broker router | `nsealgo accounts` |
| GPU decisions | `.cache/compute_autotune.json` | Per kernel and size: CPU/GPU, timings, parity | Compute layer | Compute layer |
| Monitor address | `runs/<mode>/monitor.json` | URL and token for the running session | Session | Desk |
| Kill switch | `runs/<mode>/KILL` | Presence = set | CLI, desk | Risk manager |

## Lineage

- **Live:** broker WebSocket → `Quote` → bars and book signals → strategy decisions → journal and orders → fills → `state.db`, journal and reports.
- **Research:** `fetch-history` → `history.db` → backtests (same engine) → `runs/backtest…/state.db` → reports.

## Volumes and retention (guidance)

| Data | Typical size | Keep |
|---|---|---|
| Journal plus bars | Kilobytes to a few MB per day | Always |
| `state.db` | Grows slowly | Always |
| Recordings | 5 levels × universe: ~1–3 GB/day compressed; 30 levels × 50 stocks: ~1 GB/day | 30–90 days, or archive |
| `history.db` | Grows with universe × years | Always (re-downloadable) |
| Logs | Small | 90 days |

Backups: see [Backup and recovery](../07-operations/backup-and-recovery.md).
