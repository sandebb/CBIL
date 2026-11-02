> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# User guide

nsealgo trades NSE stocks for you: intraday (MIS, closed by the end of the day) and swing (CNC, held for days to weeks). You decide which strategies run, how much risk each may take, and when anything goes live. The system does the watching, sizing, ordering and record-keeping.

## The path every strategy takes

```
idea ─► backtest ─► walk-forward ─► forward test (live data, paper orders) ─► promotion gates ─► live, small
```

| Step | Command | You look at |
|---|---|---|
| Backtest | `nsealgo backtest --strategies orb` | The report: return after costs, drawdown, trades |
| Robustness | `nsealgo walkforward --strategies orb` | Does it survive ±20% parameter changes? |
| Forward test | `nsealgo forwardtest` | The desk during the day; the report afterwards |
| Gates | `nsealgo report runs/forward` | Pass: ≥100 trades, ≥20 sessions, drawdown ≤ 1.5× backtest |
| Live | `nsealgo live --i-understand-the-risk` | Start with one strategy at small size |

The strategies, their sources and their evidence are in the [Strategy catalogue](../05-reference/STRATEGY_CATALOGUE.md). Turn them on or off under `strategies:` in `config/system.yaml`.

## A normal trading day

1. **Before 09:00:** the scheduled task starts the session (or run `nsealgo forwardtest` yourself). Log in when prompted (`nsealgo login fyers`).
2. **During the session:** watch with `nsealgo desk`; see the [Desk guide](desk-guide.md).
3. **If something looks wrong:** press **KILL SWITCH** in the desk, or run `nsealgo kill`. No new entries are made and intraday positions are closed.
4. **After the close:** read the report, and go back over any day with `nsealgo desk --review`.

## Where your orders go

- **Forward tests never send orders to a broker.**
- **Live orders go to the broker named in `broker.orders`:** Fyers for both intraday and delivery by default. To use another broker, change that setting.
- **Shoonya is configured but dormant:** it cannot place orders until you set `orders: true` for it.
- **Every live order leaves from your registered static IP,** or nothing is sent at all.

## Money and risk settings you control

| Setting | Meaning |
|---|---|
| `capital` | The capital the risk model sizes against |
| `risk.*` | Per-trade risk, daily loss limit, exposure and sector caps |
| `strategies.<name>.*` | Each strategy's parameters and on/off |
| `gates.*` | What counts as proof before going live |

Every setting is listed in the [Configuration reference](../05-reference/configuration-reference.md).

## Questions

**Why no futures or options?** A deliberate choice: stocks only ([ADR-0004](../03-architecture/adr/ADR-0004-stocks-only.md)).

**Why are swing strategies long-only?** Holding a short overnight in the cash market isn't possible.

**Why did the forward test fill worse than I expected?** The paper exchange is conservative on purpose: it never fills better than the market allows.

**Does the GPU make trading faster?** No. It speeds up research and universe-wide calculations, and only where `nsealgo gpu` measured it faster.
