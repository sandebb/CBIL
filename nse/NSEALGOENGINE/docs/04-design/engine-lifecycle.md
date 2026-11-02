> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Engine lifecycle

## A trading day (`Engine.run_day`)

```
_begin_day(day)
  ├─ calendar check (holidays, special sessions)
  ├─ reset book signals; open the day's journal (reload after a restart)
  ├─ regime, fragility, crowding timelines → each strategy's posture
  ├─ data each strategy needs (data_needs) and its on_session_start
  ├─ subscriptions: strategies' live_keys + feeds.watch; timers; depth focus
loop (until the session ends)
  ├─ feed.poll → _on_market(quotes, bars): quotes, bars, book signals, on_quote / on_bar
  ├─ _fire_timers(now): on_timer(hm) for due timers
  ├─ OMS sync: order statuses and fills → _on_fill → portfolio, journal, strategy.on_fill
  └─ _housekeeping(now): exit chasing, depth focus (60 s), journal save (5 min), reconciliation
intraday exit time → on_intraday_exit: MIS positions closed
_end_day(day)
  ├─ final OMS sync; paper end of day; expire day orders; on_session_end
  ├─ persist (forward, live); save the journal and bars; equity point; crowding breaker
```

In backtests the clock is simulated and the "feed" replays stored bars. In live and forward sessions, `forwardtest/runner.run_session_loop` drives `run_day` for each trading day.

## Order states

```
          ctx.submit ──► risk check ──fail──► (not created; journal: BLOCKED)
                              │ pass
                              ▼
                             NEW ──send──► OPEN ──► PARTIAL ──► FILLED
                              │             │  └────────────────────┘
               broker refuses │             ├──► CANCELLED (strategy, OMS, end of day)
                              ▼             └──► TRIGGER_PENDING (stop orders) ──► OPEN …
                           REJECTED
     timeout / Fyers 201 / −99 ──► UNKNOWN ──(order book, by tag)──► OPEN | FILLED | REJECTED …
```

- **Protection:** MARKET becomes a marketable LIMIT; SL-M becomes SL, before any broker sees it.
- **Rate budget:** a token bucket at ≤10/s per broker counts new orders, modifications and cancellations.
- **Exits** skip the entry risk checks; unfilled exits are re-priced every 5 s, up to 2%, with an alert after 5 attempts.
- **Self-trade guard:** an order that would cross our own resting order is refused.
- **Uncertain placement** never becomes REJECTED; it stays UNKNOWN until the book says otherwise ([ADR-0013](../03-architecture/adr/ADR-0013-uncertain-orders.md)).

## Fills and positions

`_on_fill(fill, order)` updates the journal, then the portfolio (average price, realized P&L, costs), then the strategy (`on_fill`). Closing a position records a trade in `state.db`. In live mode, broker positions are reconciled with the portfolio; a mismatch halts trading when `oms.halt_on_mismatch` is on.

## Kill switch

The `KILL` file in the run folder (from `nsealgo kill` or the desk) is checked by the risk manager. New entries are blocked and intraday positions are flattened at the next loop. `nsealgo resume` removes the file; restart the process to clear an in-memory halt.
