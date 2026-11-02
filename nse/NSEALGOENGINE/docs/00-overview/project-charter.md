> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Project charter

| | |
|---|---|
| **Project** | nsealgo: automated trading of NSE cash-market stocks |
| **Sponsor and owner** | Sandeep (business owner, trader and operator) |
| **Status** | In development; release 3.11.0; forward testing before any live capital |
| **Reference platform** | ASUS ProArt PX13 (Ryzen AI Max+ 395, Radeon 8060S, 128 GB), Windows 11 |

## Purpose

Build a personal, rules-based trading system that finds, tests and executes intraday and swing trades in NSE stocks. It must be disciplined enough to trust with real money only after it has proven itself on live data with simulated orders.

## Objectives

| # | Objective | Measure |
|---|---|---|
| O1 | Research ideas quickly and honestly | Backtests use the live engine, NSE costs and conservative fills; walk-forward and promotion gates guard against overfitting |
| O2 | Prove strategies on live data before risking money | Forward tests (paper orders on live data) pass the promotion gates: at least 100 trades, 20 sessions, drawdown within 1.5× the backtest |
| O3 | Trade live safely and within the rules | SEBI retail-algo rules met (static IP, order-rate limits, tagging); a kill switch; fail-closed checks |
| O4 | See and understand every decision | The trading desk shows depth, signals, orders, fills and per-trade P&L live, and saved days can be reviewed |
| O5 | Use the PX13 fully | Multi-core research; GPU work only where measured faster; deep order books for the most important stocks |

## Scope

**In scope:**
- NSE cash-market stocks, traded intraday (MIS) and swing (CNC).
- 17 strategies.
- Research tools, backtesting, walk-forward, forward testing, replay and live trading.
- 15 broker adapters (orders and/or data) plus multi-account routing.
- Deep market depth: Fyers TBT 50 levels, Upstox 30 levels.
- Risk overlays and the trading desk GUI.

**Out of scope (decided):**
- Futures, options and every other derivative ([ADR-0004](../03-architecture/adr/ADR-0004-stocks-only.md)).
- Index and ETF strategies.
- Manual order entry in the desk.
- Pooling other people's broker accounts.
- Trading anyone else's money.

## Stakeholders

| Stakeholder | Interest |
|---|---|
| Owner and trader | Returns after costs, capital safety, time spent |
| Brokers (Fyers for orders; Upstox, Fyers and others for data) | API terms, rate limits, static-IP registration |
| Exchange and regulator (NSE, SEBI) | Retail-algo framework, order-rate thresholds, market protection |
| Family members with accounts | Their accounts are never used by this system ([compliance](../01-business/compliance.md)) |

## Success criteria

1. Every strategy in live use has passed the promotion gates in forward testing.
2. No order is ever sent from an unregistered IP, above a broker's rate limit, or by a dormant broker.
3. Every live or forward session can be reviewed afterwards: signals, fills, charts and P&L.
4. Documentation stays current with each release ([Documentation maintenance](../09-project/documentation-maintenance.md)).

## Constraints and assumptions

- **Regulation:** the SEBI retail-algo framework, mandatory since 1 April 2026: static IP, a 10 orders/second per-broker threshold for unregistered algos, and market protection.
- **Broker limits, from their documentation:**
  - Fyers: 200 calls/min (Standard plan); TBT limited to 15 symbols.
  - Upstox: `full_d30` limited to 50 instruments per connection, or per user, which is still unresolved.
- **Hardware:** a single Windows 11 machine. Intraday trading needs it on and online from 09:00 to 15:45 IST.
- **Assumption:** past and simulated results do not guarantee live results. Forward testing is required, and live size starts small.
