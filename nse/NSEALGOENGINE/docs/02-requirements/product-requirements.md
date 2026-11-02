> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Product requirements (PRD)

## Personas

| Persona | Goals | Pain points |
|---|---|---|
| **Trader (owner)** | Profitable, disciplined trading; see why each trade happened | Emotional decisions; no time to watch 2,000 stocks |
| **Researcher (owner)** | Test ideas quickly and honestly | Look-ahead bias, ignored costs, slow backtests |
| **Operator (owner)** | Sessions that start, run and stop reliably | Logins, outages, broker limits, laptop power |
| **Maintainer (owner, future collaborator)** | Change one part without breaking another | Hidden coupling, stale docs |

## Goals and non-goals

**Goals:**
- Research, validate and run intraday and swing stock strategies end to end.
- Make every decision observable.
- Stay within regulatory and broker limits automatically.

**Non-goals:**
- Derivatives.
- Trading for others.
- Manual order entry.
- Mobile apps.
- Running on more than one machine.

## Epics and user stories

Priorities: **M** must, **S** should, **C** could. Requirement IDs refer to the [SRS](srs.md).

| Epic | Story | Pri. | SRS |
|---|---|---|---|
| Research | As a researcher I can backtest a strategy on stored history, with NSE costs, using all cores | M | FR-010–013 |
| | …and check robustness by perturbing parameters ±20% | M | FR-014 |
| | …and study market structure: bar anatomy, lead-lag, fractal measures, book signals | S | FR-015 |
| Forward test | As a trader I can run strategies on live data with simulated orders | M | FR-020–023 |
| | …and record sessions to replay later | S | FR-024 |
| Live trading | As a trader I choose which broker places intraday and delivery orders | M | FR-030 |
| | …and keep a broker configured but unable to trade (dormant) | M | FR-031 |
| | …and orders leave only from my registered static IP | M | FR-032 |
| | …and stay within each broker's order-rate and API limits | M | FR-033–034 |
| | …and unacknowledged orders are confirmed, never assumed rejected | M | FR-035 |
| Market data | As a trader I stream the whole NSE universe and give deep books to my most important stocks | S | FR-040–044 |
| Risk | As a trader every entry is sized and limited, and I can stop everything instantly | M | FR-050–054 |
| Desk | As a trader I see status, positions, orders and strategies live | M | FR-060 |
| | …and 50-level ladders for tier-1 stocks | S | FR-061 |
| | …and a window per strategy with a chart per signal (signal and order times, buy and sell levels, % P&L) | S | FR-062–063 |
| | …and can review any saved day | S | FR-064 |
| Operations | As an operator I can install, check and schedule the system on Windows 11 | M | FR-070–073 |
| Compute | As a researcher heavy maths uses the GPU only when it is correct and faster | C | FR-080 |

## Acceptance

A story is done when its SRS requirements are implemented, verified by the tests listed in the [traceability matrix](../06-testing/traceability.md), and documented.
