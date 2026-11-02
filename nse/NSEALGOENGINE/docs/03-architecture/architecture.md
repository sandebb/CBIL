> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Architecture

## 1. Context

```
                 ┌──────────────┐  orders (HTTPS via StaticIP.in proxy)  ┌──────────────────┐
  Trader ──CLI──►│              │──────────────────────────────────────►│ Order broker      │
  Operator       │   nsealgo    │◄──────── quotes, depth (WebSocket) ────│ (Fyers by default)│
     │           │  (PX13, Win) │◄──── 30-level depth ─── Upstox Plus     └──────────────────┘
     └──desk────►│              │◄──── free feeds ─── Shoonya, AngelOne, …    ▲ NSE
                 └──────────────┘──── alerts ───► Telegram (optional)
```

## 2. Processes (containers)

| Process | What runs | Started by |
|---|---|---|
| **Session** | Engine thread; feed, WebSocket and keep-alive threads; monitor HTTP thread | `nsealgo forwardtest` / `live` / scheduled task |
| **Desk** | PySide6 GUI; polls the session's monitor | `nsealgo desk` |
| **Research workers** | One per physical core (61 maximum on Windows); always CPU-only | `backtest`, `walkforward`, `bench` |
| **CLI one-offs** | `login`, `fetch-history`, `doctor`, `netcheck`, `book-study`, … | Operator |

Processes share files, never memory: `state.db`, `history.db`, `recordings/`, `journal/`, `.cache/` and `monitor.json` ([Data architecture](data-architecture.md)).

## 3. Components of a session

```
 adapters ─► FeedMesh ─► DepthOverlayFeed(TieredDepth: TBT, Upstox D30) ─► RecordingFeed
                                                                              │ quotes/bars
 Engine ◄─────────────────────────────────────────────────────────────────────┘
  ├─ BarBuilder, quotes, timers, calendar        ├─ BookSignals (compute: CPU/GPU)
  ├─ Context ──► Strategies (17)                 ├─ Regime · Fragility · Crowding
  │      └─ submit ─► SignalJournal ─► RiskManager ─► OMS ─► execution
  │                                                    execution = PaperBroker | adapter | ProductRouter | MultiBroker
  ├─ Portfolio ◄── fills ──────────────────────────────┘        (adapter HTTP via BrokerTransport)
  ├─ Storage (state.db) · Notifier · Reports
  └─ Monitor (EngineSnapshot → HTTP on 127.0.0.1) ──► desk
```

Responsibilities live in the [Module reference](../04-design/module-reference.md). The day loop and order states are in the [Engine lifecycle](../04-design/engine-lifecycle.md).

## 4. Runtime flows

1. **Tick to trade:**
   - feed → quote (top of book, plus deep book if a depth tier covers it) → `Engine._on_market`;
   - → bars and book signals → strategy `on_quote` / `on_bar` / timer;
   - → `ctx.submit` → journal (signal) → risk → OMS (protect, rate budget) → execution → broker;
   - → fill → `_on_fill` → portfolio, journal, storage.
2. **Forward vs live:** identical up to execution. Forward uses `PaperBroker` in quote mode, and live resolves `broker.orders` through `build_execution` ([ADR-0012](adr/ADR-0012-order-broker-per-product.md)).
3. **Depth focus:** every 60 s the engine ranks positions, then candidates, and sends the list to every depth source. TBT takes the top 15 and Upstox the next 250 or 50 ([ADR-0008](adr/ADR-0008-tiered-depth.md)).
4. **Desk:** it polls `/api/status` (1 s), `/api/books` (250 ms, visible stocks only), `/api/strategy` (3 s) and `/api/series` (on open) ([Monitor API](../05-reference/monitor-api.md)).
5. **Review:** the desk reads `journal/<date>.jsonl` and `.bars.json` directly, with no session running.

## 5. Deployment

A single Windows 11 machine (the PX13). Python 3.12 venv; optional ROCm PyTorch.
- **Network:** internet to the brokers. Order traffic goes through the StaticIP.in proxy, and market data connects directly.
- **Scheduling:** Task Scheduler starts sessions before the open (`deploy/windows/register-tasks.ps1`). Keep-awake and power policies hold the machine up during market hours ([Installation](../07-operations/installation-guide.md)).

## 6. Cross-cutting concerns

| Concern | Approach |
|---|---|
| Configuration | `config/system.yaml` plus `.env` (`${VAR:-default}` expansion), merged over `config.py` defaults ([reference](../05-reference/configuration-reference.md)) |
| Time | IST everywhere; an exchange calendar; an injected clock (simulated in backtests and replays) |
| Errors | `BrokerError` = the broker refused (final). `OrderUncertain` = may have reached the broker, so confirm by tag ([ADR-0013](adr/ADR-0013-uncertain-orders.md)) |
| Concurrency | The engine is single-threaded. Feeds, monitor and keep-alives run on their own threads and hand data over through queues or locks. The monitor copies collections before reading |
| Logging | `nsealgo.*` loggers, plus storage events; the desk shows warnings and errors |
| Performance | See [PERFORMANCE](PERFORMANCE.md) and [PX13](PX13.md) |

## 7. Quality attributes and tactics

| Attribute | Tactics |
|---|---|
| Safety | Fail-closed checks; dormant brokers; kill switch; risk limits; protected order types |
| Correctness | One engine for every mode; identical parallel results; parity tests for GPU kernels |
| Availability | Feed failover; reconnect with resubscribe; restart-safe journal and OMS state |
| Observability | Desk, journal, events, reports, `netcheck`, `doctor` |
| Modifiability | Adapter contract; strategy base class; configuration over code; ADRs |
| Performance | Multi-core research; batched kernels; kept-alive order connections; depth only where it matters |
