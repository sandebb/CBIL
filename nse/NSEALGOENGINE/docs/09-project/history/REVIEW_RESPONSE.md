> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Response to the code review, the multi-core design note and the Windows 11 tuning spec

> Since 3.0 nsealgo trades cash-market stocks only. Items below about futures, options, pairs or ETFs are kept as a record of the reviews; that code has been removed.

Every claim in both documents was checked against the code it targets, and against current exchange,
tax and broker rules (September 2026). The proposed replacement modules are written against a
different interface from this engine (`engine.oms.submit_order`, `engine.portfolio.calculate_kelly_size`,
`strategy.run(engine, date)`, `engine.run_forward_iteration` and several storage/history methods do not exist
here), so they could not be dropped in. Instead, each finding was judged on its merits: the valid ones are
implemented below, with tests.

## Accepted and implemented

| Finding | What was actually wrong here | Fix in 2.0 |
|---|---|---|
| Market orders need price protection (review: "synthetic price-protected limit order conversion") | Strategies never send MARKET/SL-M (entries and exits are marketable limits, stops are stop-limits), but the Zerodha adapter only sent `market_protection` when configured and the Kotak adapter hard-coded `"0"`. Since 1 Apr 2026 exchanges reject unprotected MARKET and SL-M algo orders. | The OMS turns any MARKET into a marketable LIMIT (±0.5% of bid/ask) and any SL-M into a stop-LIMIT (1% beyond trigger) for every broker. Zerodha defaults to `market_protection=-1` (automatic), with a clear error if the installed SDK lacks the parameter. |
| Overnight cash shorts (review: route shorts to NFO futures) | Strategies already short overnight only through futures (pairs trades both legs NRML), but no risk-layer rule stopped a delivery (CNC) sell beyond holdings. | `RiskManager.check` rejects any CNC SELL larger than the strategy's long holding, whatever the order's intent (entry, exit or stop). |
| Found while reviewing the order path: unfilled exits | `exit()` converts the protective stop into the exit limit. If price ran through that limit, the position was left with no stop. | OMS exit chaser: re-prices an unfilled exit after 5 s, 0.2% more aggressive each attempt, capped at 2%, with an alert after 5 attempts. |
| Cost tests "tautological" | Tests checked totals within ranges, not each charge. | `CostModel.breakdown()` itemises every charge; tests assert brokerage, STT (incl. 0.05% futures from 1 Apr 2026), exchange fees, stamp duty, SEBI fee, GST and DP charge exactly. |
| Vectorised OFI (design note) | The formula is correct and equals the engine's incremental form. | `ofi_vectorized` (alias `compute_ofi_vectorized`) for research on recorded ticks, plus a test proving it equals the incremental version. |
| BLAS oversubscription, per-worker in-memory storage with one batch merge, parallel walk-forward (design note) | Correct principles. | Implemented in `nsealgo.parallel` (details and differences below). |
| Function names used in the design note | — | `core/concurrency.py` (`discover_hardware`, `HardwareTopology`, `MultiCoreExecutor`), `run_parallel_backtest`, `parallel_perturbation`. |

## Already handled (no change needed)

- **Storage "missing table initialisation":** the schema (orders, fills, trades, equity, events, kv, regime, results) is created on open, with migrations.
- **Costs:** already had 0.025% intraday STT (sell), 0.1% delivery STT (both sides), 0.05% futures STT (Budget 2026), stamp duties, GST on brokerage + exchange + SEBI, plus DP charges and IPFT. The review's exchange fees (0.00325% equity, 0.0019% futures) are the pre-October-2024 NSE rates. Current: 0.00297% equity and 0.00173% futures, plus 0.0001% IPFT.
- **History "compound index (date, symbol)" and "Python 3.12 date serialisation":** the primary keys (key, d) and (key, ts) serve every query, and dates are stored as ISO strings (no deprecated sqlite adapters).
- **Feeds "generator re-instantiation":** feeds step through pre-sorted events by index.
- **Universe / events:** column lookup is already case-insensitive (NSE's `Symbol`, `Series`, `Industry`), and dates are parsed with `date.fromisoformat`.
- **Kelly sizing:** Bayesian quarter-Kelly with per-strategy priors, caps and automatic switch-off lives in `RiskManager`.
- **Risk halts:** the −1.5% daily loss limit and the 3-losses-in-a-row halt already exist, alongside the ES cap, drawdown breaker, kill switch and reconciliation.
- **Broker masters:** Zerodha uses the official SDK's `instruments()` (no CSV/JSON decoding issue); Dhan already uses the CDN CSV; Fyers already uses `NSE_CM.csv` / `NSE_FO.csv`.
- **ORB:** there is no `close > high` comparison. The range is the opening window's max/min, entries are stop orders at the range edges, and square-off is handled by the engine's 15:10/15:15/15:20 timers.
- **Momentum:** returns are compounded price ratios. Volatility scaling of the basket (`vol_target`, Barroso–Santa-Clara style) and EMA/time-stop exits already exist.
- **Other strategies:** pullback already requires close > 200-DMA; pairs already trades futures; the forward-test daemon, SQLite-based reports (bootstrap CIs, promotion gates) and CLI commands (login, smoke-test, fetch-history, demo and ten more) already exist.
- **"Statutory ₹25 lakh order cap":** ₹25 lakh is this project's own fat-finger guard (`risk.max_order_value`), not a regulation. Sizing keeps positions below it, so orders never need slicing. The real limits are F&O quantity-freeze limits (NSE revises them periodically) and broker-specific order caps, which vary by broker.

## Not adopted, and why

| Proposal | Problem |
|---|---|
| New `OrderManagementSystem.submit_order` | Books a fill as soon as the broker returns an order id, before anything has filled, so rejections, partials and unfilled limits would corrupt P&L. Its "overnight short to futures" rewrite appends `-FUT` to the symbol, which is not a broker symbol and ignores lot sizes. |
| New `Portfolio.record_fill` | Contains a syntax error (`self.daily_pnl += realized_net_net = ...`). Its Kelly prior treats every win rate as if it came from 50 trades. |
| New Zerodha master parser | Filters `sym.endswith("-EQ")`, which drops every NSE equity (Kite symbols have no `-EQ` suffix). `seg == "NFO"` misses `NFO-FUT`. |
| New Fyers / Dhan parsers | Fyers reads column 1 (company name) as the ticker and column 14 as tick size. Dhan reads the *detailed* CSV using the *compact* CSV's column names. |
| 21-day "momentum" | One-month returns sit in short-term-reversal territory; momentum evidence is on 6–12-month returns. |
| argparse CLI | Would drop ten existing commands. |
| Design-note parallel runner | A fresh engine per day-chunk loses positions at chunk boundaries: swing trades entered near the end never close, and re-entries that an open position would block become allowed. It also resets equity per chunk and lets every worker fit its own regime model from a different start, so regimes disagree between chunks. The 2.0 planner adds warm-up, run-off, entry attribution and one shared regime timeline; tests prove its trades are identical to a continuous run. |
| `physical = logical // 2`, default workers = logical threads | Wrong on CPUs without SMT, and ignores container CPU quotas and RAM. 2.0 measures physical cores, caps workers by quota and RAM, defaults to one process per physical core, and offers `use_smt` / `--workers 32` plus `nsealgo bench` to measure. |
| Setting OMP/MKL threads to 1 at import for the whole program | Also slows single-process work (faithful backtests, live). 2.0 pins BLAS to one thread only inside worker processes. |
| Design-note VPIN | Uses a logistic approximation of the normal CDF, assigns whole bars to buckets (so buckets are not equal-volume), loops in Python per bucket, and returns 0.0 ("not toxic") when data is short, which is unsafe for a veto filter. The 2.0 VPIN is exact (equal to the bucket-filling loop to 1e-14) and about 7× faster than that loop. |
| `multiprocessing.shared_memory` frames | Workers read the same SQLite file read-only through the OS page cache (memory-mapped), which gives the same sharing without pickling or copying data. |
| Benchmark table (45 → 1.8 minutes) | Not measured. Run `nsealgo bench` on your machine for real numbers. |

## Windows 11 performance-tuning spec (third document)

Measured where possible on the engine's own workload: minute-bar loads for 200 symbols × 375 bars per session.

| Recommendation | Verdict | Evidence and what 2.1 does |
|---|---|---|
| DuckDB + Parquet instead of SQLite ("20–50×") | Not adopted, measured | The real cost was pandas time-zone localisation, not SQLite. An exact fast IST conversion cut the per-session load from 613 ms to 155 ms with no new dependency. Plain PyArrow on Parquet: 30 ms; DuckDB over the same Parquet (1 thread, as it must be inside 16 workers): 41 ms, slower than PyArrow. After the fix, loading one new session is about 1% of per-session engine time, so a second store that must stay in sync with the live engine's writes isn't worth it. One SQLite file also suits Windows better: one file for Defender to scan, and WAL lets the forward test write while backtests read. |
| Polars instead of pandas | Not adopted | The hot paths are now incremental or small NumPy operations. Inside 16 worker processes, Polars' own thread pool would oversubscribe the CPU (about 32 threads per process). Rewriting eight strategies' pandas logic would carry risk for little gain. |
| Numba for path-dependent loops | Goal adopted, dependency not | The only remaining path-dependent numeric loop, the HMM forward filter, now runs inside the parallel fit jobs, so the sequential pass advances one step per day. No strategy uses a Kalman filter. |
| winloop event loop | Not applicable | No asyncio loop in the data path: the broker SDKs' WebSocket clients run on their own threads. |
| Memurai (Redis) for live state | Not adopted | In-process state is faster than localhost round trips. The kill switch (`nsealgo kill`), the 8-orders/s token bucket and broker reconciliation already exist, and only one process places real orders. |
| spawn ProcessPoolExecutor, 32 workers | Adapted | spawn is used on Windows. Default is one worker per physical core; `--workers 32` or `use_smt` to try SMT. Capped at 61 workers (Windows limit). One pool is reused across phases so worker start-up is paid once. |
| BLAS/OpenMP threads = 1 | Already | Pinned inside workers only; a single-process run keeps multi-threaded BLAS. |
| HIGH_PRIORITY_CLASS for workers | Changed | Workers run BELOW_NORMAL and the live engine ABOVE_NORMAL. HIGH for 16+ busy workers starves the desktop and the live engine's network threads without speeding up batch work. Workers also opt out of EcoQoS power throttling. |
| Ultimate Performance power plan | Optional | `setup.ps1 -PowerPlan`; measure with `nsealgo bench`. |
| Defender exclusions incl. `python.exe` | Changed | Prefer a trusted Dev Drive (asynchronous scanning). If you use exclusions, exclude data folders only. A `python.exe` process exclusion would stop scanning of every file any Python program opens, including pip downloads. |
| `if __name__ == "__main__"` guard | Already | Plus `freeze_support()` and a clear error for unguarded user scripts. |
| Day-chunk parallel runner | Not adopted | It has the same boundary problems as the design note's runner, and it passes `execution=None, data=None` to the Engine, which would fail. |
| `requirements-win.txt`, `python -m src.nsealgo.cli` | Replaced | `scripts/windows/setup.ps1` installs with `pip install -e ".[extras]"`; run the tool as `nsealgo ...` or `python -m nsealgo ...`. |

Windows issues found beyond the spec and fixed in 2.1:
- **Encodings:** every text file is now read and written as UTF-8. Before, `report.html` (which contains ₹) would have crashed on Windows' default code page.
- **Config and console:** config, universes and events accept a UTF-8 BOM (Notepad adds one), and console output is UTF-8 when redirected to a log.
- **Run directories:** they are cleared robustly even while antivirus briefly holds a file, with a clear error if another program has one open.
- **Sleep:** the PC can no longer sleep in the middle of a session.
- **Clock:** `nsealgo doctor` checks clock drift.
- **Deployment:** Task Scheduler deployment replaces the Linux-only systemd and cron files, and `tzdata` is declared for Windows.

## Statistical "first principles" documents (fourth round)

The four documents covered:
- skewness premium;
- OU mean reversion;
- OHLC-component Edgeworth probabilities;
- RMT, TDA, transfer entropy and MF-DFA.

They are reviewed item by item, with simulations and measured error rates, in [STRATEGY_RESEARCH.md](../../04-design/STRATEGY_RESEARCH.md). In short:
- **Built properly:** OU reversion is applied to factor residuals rather than prices, with RMT counting the factors. The skewness premium uses robust lottery measures, with futures-only shorts.
- **Replaced with better-founded measures:** the TDA crash warning becomes the absorption ratio plus MST length; the multifractal switch becomes variance-ratio gates.
- **Turned into measurement tools:** Edgeworth probabilities and transfer entropy.

## Sources for the rules above

- STT from 1 Apr 2026 (futures 0.05%, options 0.15%, cash unchanged): https://cleartax.in/s/securities-transaction-tax-stt and https://www.icicidirect.com/futures-and-options/articles/stt-changes-in-budget-2026-what-f-o-traders-need-to-know
- NSE transaction charges (equity 0.00297%, futures 0.00173%): https://aliceblueonline.com/calculators/brokerage-calculator and https://shoonya.com/pricing
- Market protection mandatory for API market/SL-M orders; 10 orders/s: https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/kite-connect-api-faqs and https://kite.trade/forum/discussion/15912/preparing-to-comply-with-sebis-retail-algo-rules-static-ip-ratelimits-order-types
- F&O quantity-freeze limits (revised periodically): https://www.5paisa.com/blog/nse-quantity-freeze-limits-index-derivatives
- Dev Drive and Defender performance mode: https://learn.microsoft.com/en-us/windows/dev-drive/
