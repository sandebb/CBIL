> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Test plan (per release)

| | |
|---|---|
| **Scope** | Every change in the release (CHANGELOG), plus regression of the whole system |
| **Environments** | E1 Linux sandbox (automated suite, offscreen GUI); E2 PX13 Windows 11 with ROCm PyTorch and broker accounts |
| **Roles** | Tester runs the plan; developer fixes; owner signs off (see [RACI](../09-project/raci.md)) |

## Entry criteria

All changes merged; CHANGELOG entry written; version bumped.

## Activities

| # | Activity | Env. | Pass when |
|---|---|---|---|
| 1 | `pytest -q` (full suite) | E1 | All pass (the PyTorch test skips on E1) |
| 2 | `python -m pyflakes src tests scripts` | E1 | Clean |
| 3 | `python scripts/docs/build_all.py` | E1 | No changes to generated docs; all links resolve |
| 4 | `nsealgo demo` | E1 | Completes; reports written |
| 5 | `pytest -q` on the PX13 | E2 | All pass, **including** the PyTorch parity test |
| 6 | `nsealgo doctor`, `hardware`, `gpu --fresh` | E2 | No errors; GPU choices recorded |
| 7 | `nsealgo netcheck` | E2 | Egress IP matches `static_ip` |
| 8 | Acceptance tests relevant to the change ([list](acceptance-tests.md)) | E2 | All expected results seen |
| 9 | One forward-test session (full day) for changes touching engine, OMS, brokers or feeds | E2 | No errors in Alerts; journal and reports written |

## Exit criteria

Activities 1–9 pass. Open defects are documented with a workaround, or the release waits. The owner signs off in the release notes.

## Risks to the plan

Broker or market outages on the test day (reschedule activity 9). PyTorch or ROCm driver changes (re-run `nsealgo gpu --fresh`).
