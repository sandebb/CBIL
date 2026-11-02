> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Test strategy

## Objectives

Prove that nsealgo does what the [SRS](../02-requirements/srs.md) says, that every safety check fails closed, and that a change can't break any of that silently.

## Levels

| Level | What | How | Where |
|---|---|---|---|
| **Unit** | Pure logic: costs, risk, indicators, statistics, journal, routing, parsers, kernels | Fakes and synthetic inputs; no network | `tests/unit/` |
| **Protocol** | Broker wire formats | Frames built with the vendor's own schema (Fyers protobuf); fake WebSockets | `test_fyers_tbt.py`, `test_upstox_depth*.py`, `test_stream_parsers.py` |
| **Network** | Transport, proxy, keep-alive, egress check, retries | **Real** local HTTP server and forwarding proxy | `test_transport.py`, `test_monitor.py` |
| **Integration** | The engine end to end | The synthetic market; recorded sessions; forward session on a fast-forwarded clock | `tests/integration/` |
| **Parity** | Equivalent paths give identical results | Parallel vs single-process backtests; GPU vs NumPy kernels; batched vs per-window maths | `test_parallel_backtest.py`, `test_compute.py` |
| **GUI** | Windows, buttons, charts | PySide6 offscreen; assertions on widgets and chart items | `test_desk_gui.py` |
| **Configuration and docs** | The shipped config loads correctly; docs are fresh and links resolve | Loads `config/system.yaml`; regenerates the references | `test_order_routing.py`, `test_docs.py` |
| **Acceptance** | The real machine and real accounts | Manual scripts on the PX13 | [Acceptance tests](acceptance-tests.md) |

## Test data

- **Synthetic market** (`data/synthetic.py`): daily and minute bars with known structure (a cointegrated pair, events, an index). Recordings can be generated from bars with optional depth levels.
- **Recorded sessions:** quote JSONL files for replay tests.
- **Vendor artefacts:** the Fyers SDK's compiled protobuf schema; Dhan and Fyers layouts checked against the official SDKs.

## Principles

1. **Regression first:** every bug gets a test that fails before the fix (e.g. the Fyers `1:` tag prefix, the config entry swallowed by a comment).
2. **No internet, no accounts:** tests run anywhere. Real-account behaviour is covered by `nsealgo smoke-test` and the acceptance scripts.
3. **Determinism:** fixed seeds, simulated clocks, no timestamps in generated docs.
4. **Fail-closed checks have negative tests:** wrong IP, dormant broker, uncertain order, stale book.

## Quality gates (every change)

`pytest -q` all green; `python -m pyflakes src tests scripts` clean; `python scripts/docs/build_all.py` leaves no diff and no broken links.

## Known gaps

- **GPU paths** run only where PyTorch exists. On the PX13, `test_torch_matches_numpy_on_every_kernel` must pass.
- **Live broker behaviour** under real load is checked by acceptance tests, not automation.
- **Backtest journals** are planned for the next iteration.
