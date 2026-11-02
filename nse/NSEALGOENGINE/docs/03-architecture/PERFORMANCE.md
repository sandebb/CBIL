> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Using every core

Python threads can't run Python bytecode on two cores at once (the GIL), so 2.0 uses three tools, each
where it fits:

| Work | Tool | Where |
|---|---|---|
| Backtest jobs, HMM regime fits, walk-forward variants | **processes**, one per physical core | `nsealgo.parallel` |
| Broker history downloads, SQLite reads at session start (live/forward) | **threads** (they wait on network/disk) | `HistoryStore`, `Engine.prefetch_*` |
| Per-bar and per-day maths | **vectorised NumPy** and incremental state | indicators, regime filter, engine caches |

## 1. Make each core do less work (measured on one core)

Profiling showed the old engine recomputing the same things every bar or day.
- **Incremental intraday aggregates:** 5-minute bars and VWAP bands are built as bars arrive; the VWAP fade used to resample the whole day on every bar.
- **Incremental regime filter:** the forward filter advances one step per day instead of re-running over all history.
- **VPIN:** an exact closed form replaces the bucket-filling loop.
- **Cross-sectional panels:** momentum and pullback screen all symbols at once (dates × symbols) instead of symbol by symbol.
- **Bulk data loads:** SQL loads serve many symbols per query, full daily history is cached in memory, and a rolling minute-bar cache reads one new day instead of 14.

On the development sandbox (1 vCPU), with identical trade counts before and after:

| Run | Before | After |
|---|---|---|
| Daily backtest, 250 sessions, 40 stocks, all swing strategies | 59.6 s | 19.5 s (3.1×) |
| Minute backtest, 8 sessions, 3 intraday strategies | 71.1 s | 10.4 s (6.8×) |
| Minute-bar load, 200 symbols × 1 session (2.1) | 613 ms | 155 ms (4.0×) |

## 2. Spread independent work across processes

**Research mode** (default when more than one core is available) treats each strategy as an independent
*sleeve*: sized off constant capital, with the Kelly posterior frozen at its priors. Under those rules any
block of sessions can be simulated on its own core:
- **Intraday sleeves** are flat every night, so day blocks are exactly independent.
- **Swing blocks** start `warmup_sessions` early (trades entered there belong to the previous block and are dropped), and continue up to `runoff_sessions` after the block with no new entries, until their trades finish.
- **Regime and crowding states** come from one timeline computed up front, with the HMM fits themselves run in parallel. Every job therefore sees the same regime a continuous run would.
- **Merging:** jobs write to in-memory SQLite, the parent merges their trades once, and a mark-to-market equity curve is rebuilt from daily closes.

The test-suite runs the same backtest with 1 and 3 workers and requires **identical trades and final
equity** for both intraday and swing sleeves.

**Faithful mode** (`nsealgo backtest --faithful`) runs one engine with all strategies together, exactly like
live trading: compounding equity, Kelly learning, shared position/sector/ES caps and the
correlated-drawdown breaker. It is path-dependent, so it uses one core, but its regime timeline still
benefits from parallel fits. Use research mode to explore and faithful mode to confirm.

Walk-forward perturbation runs every parameter variant in its own process at the same time, sharing one
precomputed regime timeline.

## 3. Worker policy

`nsealgo hardware` prints what was detected and what will be used.
- **Default:** one process per **physical** core (16 on a 16-core/32-thread CPU). Numeric Python gains little from SMT siblings, and each extra process costs RAM.
- **Using the SMT threads:** `parallel.use_smt: true` or `--workers 32`. Measure first with `nsealgo bench --workers 16` versus `--workers 32`.
- **Caps:** container CPU quotas (Docker/cgroups), CPU affinity, and RAM (about 0.6 GB per worker) cap the count automatically. The `NSEALGO_WORKERS` environment variable overrides everything.
- **BLAS:** each worker's BLAS/OpenMP pool is pinned to 1 thread, set before the worker imports NumPy, plus threadpoolctl. 16 workers × 16 BLAS threads would put 256 threads on 16 cores. A single-process run keeps its multi-threaded BLAS.
- **Start method:** 'forkserver' on Linux (safe with threads and open SQLite handles), 'spawn' on Windows and macOS.

## 4. Measure on your machine

```bash
nsealgo hardware
nsealgo bench                      # 1 worker vs auto workers on a synthetic market; checks trades are identical
nsealgo bench --workers 32         # compare SMT
```

Speedup depends on the strategy mix: the slowest single job (usually the momentum sleeve, which screens
the whole universe) bounds the wall time. The sandbox this was built on has a single core, so no
multi-core figure is quoted here: `bench` gives you the real one.

## Windows notes

See [WINDOWS.md](../07-operations/WINDOWS.md) for the full runbook (Dev Drive, Defender, power, Task Scheduler).


- Worker processes re-import the main module, so calling nsealgo from your own script **must** use the guard shown below. `nsealgo ...` and `python -m nsealgo ...` are already guarded. An unguarded script now fails fast with an explanatory error instead of hanging.
- If process start-up looks slow, real-time antivirus scanning of the virtual environment is a common cause. Compare `nsealgo bench` before and after any exclusion your security policy allows.

```python
from datetime import date
from nsealgo.config import load_config
from nsealgo.backtest.runner import run_backtest

def main():
    cfg = load_config("config/system.yaml")
    s = run_backtest(cfg, start=date(2021, 1, 1), end=date(2026, 9, 25), workers="auto")
    print(s["overall"], s["perf"])

if __name__ == "__main__":      # required for multi-process runs on Windows/macOS (and forkserver)
    main()
```
