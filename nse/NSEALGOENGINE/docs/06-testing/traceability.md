> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Traceability matrix

Requirements ([SRS](../02-requirements/srs.md)) → main implementation → tests. Update this table when a requirement or test changes.

| Requirement | Implementation | Tests |
|---|---|---|
| FR-010–013 Backtest, costs, fills | `backtest/runner.py`, `core/costs.py`, `brokers/paper.py` | [test_engine_e2e](../../tests/integration/test_engine_e2e.py), [test_orders_costs](../../tests/unit/test_orders_costs.py), [test_risk_costs](../../tests/unit/test_risk_costs.py), [test_paper_broker](../../tests/unit/test_paper_broker.py) |
| FR-011 / NFR-01 Parallel parity | `parallel/*` | [test_parallel_backtest](../../tests/integration/test_parallel_backtest.py), [test_parallel_units](../../tests/unit/test_parallel_units.py) |
| FR-014 Walk-forward | `backtest/walkforward.py` | [test_engine_e2e](../../tests/integration/test_engine_e2e.py) |
| FR-015 Research tools | `research.py`, `core/stats.py` | [test_stats](../../tests/unit/test_stats.py), [test_book](../../tests/unit/test_book.py) |
| FR-020–023 Forward test, mesh, reports | `forwardtest/*`, `data/mesh.py` | [test_forward_mesh_e2e](../../tests/integration/test_forward_mesh_e2e.py), [test_mesh](../../tests/unit/test_mesh.py) |
| FR-024 Record and replay | `data/feeds.py`, `forwardtest/runner.py` | [test_microstructure_replay](../../tests/integration/test_microstructure_replay.py) |
| FR-030–031 Order broker per product; dormant | `brokers/routing.py` | [test_order_routing](../../tests/unit/test_order_routing.py) |
| FR-032 Static IP fail closed | `brokers/transport.py`, `noren.py`, `fyers.py` | [test_transport](../../tests/unit/test_transport.py), [test_order_routing](../../tests/unit/test_order_routing.py) |
| FR-033 Order-rate budgets | `core/oms.py`, `brokers/multi.py` | [test_microstructure_units](../../tests/unit/test_microstructure_units.py), [test_multi_broker](../../tests/unit/test_multi_broker.py) |
| FR-034 API call budget | `brokers/base.py` (`_budget`), `fyers.py` | [test_fyers_docs](../../tests/unit/test_fyers_docs.py) |
| FR-035 Uncertain orders | `brokers/base.OrderUncertain`, `fyers.py`, `core/oms.py` | [test_fyers_docs](../../tests/unit/test_fyers_docs.py), [test_order_routing](../../tests/unit/test_order_routing.py), [test_multi_broker](../../tests/unit/test_multi_broker.py) |
| FR-036 Order protection | `core/oms.py` | [test_microstructure_units](../../tests/unit/test_microstructure_units.py) |
| FR-037 Multi-account allocation | `brokers/multi.py` | [test_multi_broker](../../tests/unit/test_multi_broker.py) |
| FR-040 Fyers universe stream | `brokers/fyers.py` | [test_fyers_tbt](../../tests/unit/test_fyers_tbt.py), [test_stream_parsers](../../tests/unit/test_stream_parsers.py) |
| FR-041 Fyers TBT 50 levels | `data/fyers_tbt.py` | [test_fyers_tbt](../../tests/unit/test_fyers_tbt.py) |
| FR-042 Upstox 30 levels | `data/upstox_depth.py`, `brokers/upstox.py` | [test_upstox_depth_feed](../../tests/unit/test_upstox_depth_feed.py), [test_upstox_depth](../../tests/unit/test_upstox_depth.py) |
| FR-043 Tiers and focus | `data/depth.py`, `core/engine.py` | [test_depth](../../tests/unit/test_depth.py), [test_forward_mesh_e2e](../../tests/integration/test_forward_mesh_e2e.py) |
| FR-044 Book signals | `core/book.py`, `compute/kernels.py` | [test_book](../../tests/unit/test_book.py), [test_compute](../../tests/unit/test_compute.py) |
| FR-050–051 Sizing, overlays | `core/risk.py`, `core/regime.py`, `core/fragility.py`, `core/crowding.py` | [test_risk_costs](../../tests/unit/test_risk_costs.py), [test_regime](../../tests/unit/test_regime.py), [test_stats](../../tests/unit/test_stats.py) |
| FR-052 Kill switch | `core/risk.py`, `monitor/snapshot.py`, desk | [test_monitor](../../tests/unit/test_monitor.py), [test_desk_gui](../../tests/unit/test_desk_gui.py) |
| FR-053–054 Exits, reconciliation | `core/oms.py`, `core/engine.py` | [test_microstructure_units](../../tests/unit/test_microstructure_units.py), [test_engine_e2e](../../tests/integration/test_engine_e2e.py) |
| FR-060–065 Desk | `desk/*`, `monitor/*` | [test_desk_gui](../../tests/unit/test_desk_gui.py), [test_monitor](../../tests/unit/test_monitor.py) |
| FR-066 Journal | `core/journal.py`, `core/engine.py` | [test_journal](../../tests/unit/test_journal.py), [test_microstructure_replay](../../tests/integration/test_microstructure_replay.py) |
| FR-070–073 Operations | `cli.py`, `system.py`, `scripts/windows/*` | [test_windows_support](../../tests/unit/test_windows_support.py); [AT-01–05](acceptance-tests.md) |
| FR-080 Measured GPU | `compute/*` | [test_compute](../../tests/unit/test_compute.py) |
| Strategies | `strategies/*` | [test_documented_strategies](../../tests/unit/test_documented_strategies.py), [test_documented_strategies_e2e](../../tests/integration/test_documented_strategies_e2e.py), [test_research_strategies](../../tests/integration/test_research_strategies.py), [test_indicators](../../tests/unit/test_indicators.py) |
| NFR-07 Docs current | `scripts/docs/*` | [test_docs](../../tests/unit/test_docs.py) |
