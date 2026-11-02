> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Coding standards

## Language and style

- Python ≥ 3.10 (3.12 on the PX13). Use `from __future__ import annotations` and type hints on public functions.
- **Lint-clean:** `python -m pyflakes src tests` must report nothing beyond the two known optional-import notes.
- Names describe intent: `round_trip_cost`, not `rtc`. Constants are in `UPPER_CASE` with units in the name or a comment.
- Write comments and docstrings in plain language: say *why*, cite sources (papers, vendor doc pages), and give units.

## Behaviour

| Rule | Why |
|---|---|
| Times are IST and timezone-aware; inject the clock (`ctx.now`, `engine.clock`) | Backtests, replays and live trading must agree |
| Money in INR; prices in rupees; quantities as integers | No unit confusion with paise-based APIs (Fyers TBT sends paise) |
| Fail closed on safety checks | Refusing to trade is recoverable; a wrong trade isn't |
| `BrokerError` = refusal; `OrderUncertain` = maybe sent | Never retry an order that might exist |
| No bare `except:`. Catch narrowly, log what was caught, and continue only when safe | Silent failures hide bugs |
| Strategies touch the world only through `ctx` | Keeps every mode identical |
| Configuration over code: defaults in `config.py`, comments in `system.yaml` | Behaviour changes without edits; generated docs stay true |
| Thread handoffs use locks or copies; the engine itself is single-threaded | Feed, monitor and keep-alive threads must not race the engine |

## Tests

Every change ships with tests:
- **Unit:** pure logic.
- **Integration:** engine paths, using the synthetic market.
- **GUI:** offscreen.
- **Network:** use real local sockets rather than mocks.

Bugs get a regression test that fails before the fix. Tests must not need the internet or a broker account.

## Documentation

Update the documents listed for your change in the [maintenance matrix](../09-project/documentation-maintenance.md); regenerate references (`scripts/docs/build_all.py`); add an ADR for any architectural decision; add a CHANGELOG entry.
