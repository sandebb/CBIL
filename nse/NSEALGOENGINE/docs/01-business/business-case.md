> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Business case

## Problem

Discretionary trading is slow to test, hard to repeat and easy to break under pressure. The owner wants a systematic approach in which every rule is written down, tested on history, proven on live data without risk, and then executed exactly, within SEBI's retail-algo rules.

## Options considered

| Option | For | Against | Decision |
|---|---|---|---|
| A. Keep trading manually | No build effort | Untestable, emotional, can't scale to hundreds of stocks | Rejected |
| B. Broker or third-party strategy platforms | Quick to start | Limited strategy logic, opaque fills and costs, little research depth, vendor lock-in | Rejected |
| **C. Build nsealgo** | Full control and transparency; research on the live engine; any broker; uses the PX13 | Owner's time; responsibility for correctness and compliance | **Chosen** |

## Costs

| Cost | Nature | Notes |
|---|---|---|
| Hardware | One-off | ASUS ProArt PX13, already purchased |
| Static IP | Recurring | StaticIP.in proxy, needed for any API orders |
| Market data | Recurring, optional | Upstox Plus (30-level depth), Fyers TBT entitlement if charged, Kite data if used. Subscribe only after `nsealgo book-study` shows deep data adds value |
| Trading costs | Per trade | Brokerage, STT, exchange, SEBI, stamp and GST, all modelled in backtests (`core/costs.py`) |
| Owner's time | Ongoing | Research, monitoring and maintenance |

## Benefits

- **Discipline:** rules run the same way every time, sized by a risk model, under hard limits.
- **Evidence before money:** a strategy reaches live capital only after its backtest, walk-forward and forward test agree.
- **Reach:** the whole NSE universe is streamed and scored, which no manual process can match.
- **Insight:** every signal, fill and outcome is charted and kept for review.

## Expected value, honestly

Most short-term trading loses money: SEBI found 71% of individual intraday cash traders lost money in FY23. Published edges shrink after costs and once others trade them. nsealgo therefore treats every strategy as unproven until the promotion gates pass on live data, and starts live trading with small size. A system that only stops bad strategies from reaching live money still pays for itself in losses avoided.

## Decision gates

| Gate | Condition | Decision maker |
|---|---|---|
| G1 Research | Backtest after costs beats doing nothing; walk-forward stable within ±20% parameters | Owner |
| G2 Forward test | Promotion gates on live data: ≥100 trades, ≥20 sessions, drawdown ≤ 1.5× backtest | Owner |
| G3 Live, small | Start at minimum size, one strategy at a time | Owner |
| G4 Scale | Live results consistent with the forward test over at least a month | Owner |
| Stop | Live drawdown beyond the forward-test envelope, or any compliance incident | Owner (kill switch) |

## Measures

Net return after costs; maximum drawdown; Sharpe ratio; hit rate and average win/loss; cost per trade; session uptime; compliance incidents (target: zero).
