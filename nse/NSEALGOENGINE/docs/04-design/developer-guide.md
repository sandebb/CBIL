> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Developer guide

## Set up

```
git clone <repo> nse-algo && cd nse-algo
py -3.12 -m venv .venv && .venv\Scripts\activate        # Windows (Python 3.12 if you want the GPU)
pip install -e .[dev,desk]                              # plus broker extras: .[fyers,upstox,...]
pytest -q                                               # all tests, offscreen GUI included
nsealgo demo                                            # offline end-to-end run on a synthetic market
nsealgo desk --demo                                     # the GUI on synthetic data
```

On Linux the GUI tests need `QT_QPA_PLATFORM=offscreen` (the tests set it). The GPU parity test runs only where PyTorch is installed.

## Repository layout

```
src/nsealgo/
  core/         engine, context, OMS, risk, portfolio, costs, models, storage, regime, fragility, crowding,
                book signals, journal, indicators, statistics
  brokers/      adapter contract and 15 adapters; paper exchange; transport (static IP); routing; multi-account
  data/         history store, feeds, tick mesh, depth overlay and sources (Fyers TBT, Upstox D30), universes,
                synthetic market
  strategies/   17 strategies and templates
  compute/      NumPy/PyTorch ops, batched kernels, CPU/GPU selection
  backtest/     runner (research/faithful), walk-forward
  forwardtest/  session loop, replay, reports, feed checks
  parallel/     hardware detection, process pool, planning, merging, benchmarks
  monitor/      engine snapshot and local HTTP service for the desk
  desk/         PySide6 GUI: windows, charts, ladders, clients (monitor, demo, review)
  cli.py config.py research.py system.py
config/         system.yaml, universes/*.csv, .env (secrets, not committed)
docs/           this documentation        scripts/  setup, docs generators     tests/  unit + integration
```

The [Module reference](module-reference.md) lists every module and its purpose, generated from the docstrings.

## Day-to-day workflow

1. **Branch:** one per change; keep changes small.
2. **Code:** follow the [coding standards](coding-standards.md); add or adjust tests in the same change.
3. **Check:** `python -m pyflakes src tests` and `pytest -q`. Both must be clean.
4. **Document:**
   - update the documents named in the [maintenance matrix](../09-project/documentation-maintenance.md);
   - run `python scripts/docs/build_all.py`;
   - write an ADR for any significant decision.
5. **Release:** follow the [release process](../09-project/release-process.md).

## Where to start reading the code

1. `core/models.py`: the vocabulary (Quote, Bar, OrderRequest, Order, …).
2. `core/engine.py`: `run_day` and the methods it calls ([Engine lifecycle](engine-lifecycle.md)).
3. `core/context.py`: everything a strategy can ask for or do.
4. `strategies/orb.py`: a complete intraday strategy.
5. `core/oms.py` and `brokers/paper.py`: how orders live and fill.
6. `forwardtest/runner.py`: how a live or forward session is assembled.

## Common tasks

| Task | Guide |
|---|---|
| Add a strategy | [Strategy developer guide](strategy-developer-guide.md) |
| Add a broker | [Broker adapter guide](broker-adapter-guide.md) |
| Add a config setting | Add the default to `config.py`, document it in `config/system.yaml` with a comment, then regenerate the [configuration reference](../05-reference/configuration-reference.md) |
| Add a CLI command | Add it in `cli.py` with a docstring, then regenerate the [CLI reference](../05-reference/cli-reference.md) |
| Add a desk window | `desk/windows.py`, `strategy.py` or `charts.py`; a test in `tests/unit/test_desk_gui.py`; a screenshot in `docs/assets/` |
| Add a monitor endpoint | `monitor/snapshot.py` + `server.py` + both clients; update [Monitor API](../05-reference/monitor-api.md) |
