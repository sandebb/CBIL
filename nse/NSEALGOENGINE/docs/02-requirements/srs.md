> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Software requirements specification (SRS)

Each requirement has a stable ID. Verification is listed in the [traceability matrix](../06-testing/traceability.md). Do not renumber; retire IDs with a note instead.

## Functional requirements

### Research and backtesting
- **FR-010** Backtests run strategies through the same engine as live trading, on stored daily and minute history.
- **FR-011** Research mode splits work into strategy sleeves × time blocks across processes, and produces the same trades and equity as a single process.
- **FR-012** Costs include brokerage, STT, exchange, SEBI, stamp duty, GST and DP charges at current rates.
- **FR-013** Fills are conservative: no fill at a better price than the market allows; circuit limits and auctions respected.
- **FR-014** Walk-forward perturbs each parameter by ±20% and reports stability.
- **FR-015** Research commands: `anatomy`, `leadlag`, `fractal`, `book-study`.

### Forward testing and replay
- **FR-020** Forward tests use live data and the paper exchange; no order reaches a broker.
- **FR-021** Sessions start, run and stop on the trading calendar, and survive restarts within a day.
- **FR-022** A tick mesh merges several free feeds, dropping duplicates and failing over stale feeds.
- **FR-023** Reports are produced with bootstrap confidence intervals and promotion-gate verdicts.
- **FR-024** Sessions can be recorded and replayed at quote level.

### Live execution
- **FR-030** `broker.orders.intraday` and `.delivery` choose the order broker per product; `--broker` overrides both.
- **FR-031** A broker with `orders: false` is refused as an order broker by every path.
- **FR-032** With `static_ip` set, login and orders proceed only when the egress IP equals it (fail closed).
- **FR-033** Order rates stay ≤10/s per broker unless `registered_algo` is set; multi-account budgets are per broker.
- **FR-034** Broker API calls stay within documented limits, e.g. Fyers at 90% of 10/s and 200/min.
- **FR-035** An order whose outcome is unknown (timeout, Fyers 201 or −99) is confirmed from the order book by tag before any retry.
- **FR-036** MARKET and SL-M orders are converted to protected LIMIT and SL orders.
- **FR-037** Multi-account execution keeps each stock at one account; positions never move between accounts.

### Market data and depth
- **FR-040** Fyers streams up to 5,000 subscriptions; with 5-level depth, 2,500 stocks.
- **FR-041** Fyers TBT gives 50-level books for up to 15 stocks (3 connections × 5); a book is used only after its snapshot.
- **FR-042** Upstox `full_d30` gives 30-level books; a per-user cap is detected and capacity adjusted.
- **FR-043** Depth tiers merge the deepest fresh, uncrossed book onto quotes; focus is re-ranked every 60 s.
- **FR-044** Book signals: multi-level OFI z-score, imbalance, microprice, pressure, absorption (icebergs).

### Risk and safety
- **FR-050** Position size from fractional Kelly with Bayesian shrinkage, within per-trade, exposure, sector and loss limits.
- **FR-051** Regime, fragility and crowding overlays adjust or block entries per strategy style.
- **FR-052** The kill switch (CLI or desk) blocks new entries and flattens intraday positions; `resume` clears it.
- **FR-053** Exits bypass entry checks and are chased until filled.
- **FR-054** Positions are reconciled with the broker; mismatches halt live trading when configured.

### Desk and review
- **FR-060** The desk's main window shows mode, equity, P&L, exposure, regime, positions, orders, strategies and alerts.
- **FR-061** The tier-1 depth window shows 50-level ladders per TBT connection, with order counts, depth bars and signals.
- **FR-062** A window per strategy shows a trade map (entry → exit at % P&L) and its signals.
- **FR-063** A chart per signal shows the price path, signal, order and exit times, buy, sell and stop levels, where price met them, and the % P&L.
- **FR-064** Saved days open in review mode with a day picker, with no session needed.
- **FR-065** The desk reads the session through a read-only service on 127.0.0.1 with a token; its only action is the kill switch.

### Journal
- **FR-066** Every signal, order, fill and status change is saved as it happens, per day; a restart reloads the day.

### Operations
- **FR-070** `doctor`, `hardware`, `gpu`, `netcheck`, `feeds` and `brokers` report environment and connectivity.
- **FR-071** `scripts/windows/setup.ps1` installs on Windows 11 (`-Gpu` for ROCm PyTorch).
- **FR-072** Scheduled tasks start sessions before the market opens (`deploy/windows/register-tasks.ps1`).
- **FR-073** `login` caches the day's session token with restricted permissions.

### Compute
- **FR-080** Each kernel uses the GPU only after matching NumPy on the machine and measuring over 10% faster; errors fall back to the CPU.

## Non-functional requirements

| ID | Requirement |
|---|---|
| **NFR-01 Correctness** | Parallel and single-process backtests are identical; GPU results match NumPy within tolerance. |
| **NFR-02 Safety** | Every safety check fails closed: IP, dormant broker, uncertain orders. |
| **NFR-03 Latency** | Order transport keeps a warm, kept-alive connection with timeouts; quote handling is batched. |
| **NFR-04 Reliability** | A session survives feed and broker blips (failover, reconnects) and restarts (journal and OMS state). |
| **NFR-05 Security** | Secrets stay out of code; the monitor is local-only and token-guarded; the desk is a separate process. |
| **NFR-06 Usability** | The desk is readable in a dark theme, with Indian number formatting and IST times. |
| **NFR-07 Maintainability** | Lint-clean; tests on every change; documentation generated where it would drift. |
| **NFR-08 Portability** | Windows 11 is the reference platform; Linux runs everything, including the offscreen GUI tests. |
| **NFR-09 Compliance** | See [Compliance](../01-business/compliance.md). |
