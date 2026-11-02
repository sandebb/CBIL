> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# System overview

nsealgo turns trading ideas into tested, monitored, rule-abiding orders. One engine runs everywhere, in every mode below; what changes is where the market data comes from and where orders go.

| Mode | Market data | Orders | Command |
|---|---|---|---|
| Backtest (research or faithful) | stored history | simulated exchange | `nsealgo backtest` |
| Walk-forward | stored history | simulated, ±20% parameters | `nsealgo walkforward` |
| Replay | a recorded session | simulated, quote level | `nsealgo replay` |
| **Forward test** | **live** | **simulated (paper)**: nothing reaches a broker | `nsealgo forwardtest` |
| Live | live | real, via the configured broker | `nsealgo live --i-understand-the-risk` |

## Capabilities

```
          ┌──────────────── nsealgo ────────────────┐
 market   │  feeds ─► tick mesh ─► bars + depth      │   brokers (orders)
 data ───►│            │                             │──► Fyers (default) / any adapter
 (Fyers,  │            ▼                             │    static-IP proxy, rate budgets
 Upstox,  │  strategies (17) ─► risk ─► OMS ─────────┼──► paper exchange (forward tests)
 Dhan…)   │     ▲    regime · fragility · crowding   │
          │     └── book signals (OFI, icebergs)     │──► monitor (127.0.0.1) ──► desk GUI
          │  journal · storage · reports             │
          └──────────────────────────────────────────┘
```

- **Strategies:** 17 intraday and swing strategies, from opening-range breakouts to residual mean reversion ([catalogue](../05-reference/STRATEGY_CATALOGUE.md)).
- **Risk:**
  - fractional-Kelly sizing;
  - exposure, sector and loss limits;
  - regime (HMM + 200-DMA), market-fragility and crowding overlays;
  - a kill switch.
- **Execution:**
  - an order manager with rate budgets, marketable-limit protection, exit chasing and a self-trade guard;
  - per-product broker routing (`broker.orders`);
  - multi-account allocation;
  - dormant brokers;
  - a static-IP transport that refuses to trade from the wrong IP.
- **Market data:**
  - free-feed mesh;
  - Fyers whole-universe stream;
  - Fyers TBT 50-level and Upstox 30-level depth;
  - recordings for replay.
- **Compute:** research spread over every core. GPU (PyTorch on ROCm) is used per calculation only where it matches NumPy and is measured faster.
- **Desk:** a dashboard, tier-1 depth ladders, market watch, strategy windows (trade map, signal charts) and review of saved days.

## Key numbers (3.11.0)

| | |
|---|---|
| Source | 10 packages, ~15,000 lines of Python |
| Strategies | 17 (6 enabled by default) |
| Broker adapters | 15, plus multi-account routing |
| CLI commands | 26 ([CLI reference](../05-reference/cli-reference.md)) |
| Automated tests | 169, plus 1 that needs PyTorch |

For how it is built, see [Architecture](../03-architecture/architecture.md). For how to use it, see the [User guide](../08-user/user-guide.md).
