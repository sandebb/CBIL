> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Glossary

| Term | Meaning |
|---|---|
| **Absorption / iceberg** | Heavy trading at a price level whose displayed size doesn't shrink and whose price holds: hidden supply (at the ask) or demand (at the bid). Scored as volume traded at the level ÷ average size shown there ([book signals](../04-design/module-reference.md#core)). |
| **ADR** | Architecture decision record ([index](../03-architecture/adr/README.md)). |
| **Backtest, research mode** | A backtest split into strategy sleeves × time blocks, run on all cores; results are identical to a single process. |
| **Backtest, faithful mode** | One engine stepping day by day exactly as live, at the cost of speed. |
| **CAS** | NSE closing auction session for F&O-eligible stocks; orders at the close go into it. |
| **CNC** | Delivery product: positions can be held overnight (swing). |
| **Depth / levels** | Order-book price levels per side: 5 (standard feeds), 30 (Upstox Plus `full_d30`), 50 (Fyers TBT). |
| **Depth focus** | The ranked list of stocks that deserve deep books: open positions first, then each strategy's candidates. Re-ranked every 60 s. |
| **Desk** | The PySide6 GUI (`nsealgo desk`). Read-only except the kill switch. |
| **Dormant broker** | A configured broker that may log in and supply data but is never sent orders (`orders: false`). |
| **Egress IP** | The IP address the broker sees. It must equal the registered static IP before orders are allowed. |
| **Fail closed** | When a safety check can't be confirmed, the system refuses to act rather than proceeding. |
| **Forward test** | Live market data with simulated (paper) orders. The step before live trading. |
| **Fragility overlay** | Market-structure risk from the absorption ratio and the minimum-spanning-tree length of stock correlations; can report, halve or block new swing entries. |
| **Journal** | The record of every signal, order, fill and status change, saved per day for review. |
| **Kill switch** | The `KILL` file in the run folder: blocks new entries and flattens intraday positions. Set by `nsealgo kill` or the desk. |
| **Microprice** | Size-weighted fair price from the best bid and ask. |
| **MIS** | Intraday product: positions are squared off the same day. |
| **Monitor** | The read-only HTTP service a running session exposes on 127.0.0.1 for the desk. |
| **OFI / MLOFI** | (Multi-level) order-flow imbalance: net buying pressure from changes in queue sizes and prices. |
| **OMS** | Order management system: rate budget, protection, exit chasing, reconciliation. |
| **OPS** | Orders per second. The exchanges' threshold for unregistered algos is 10 per broker. |
| **Paper exchange** | The simulated exchange: conservative fills, NSE costs, circuit limits, auctions. |
| **Promotion gates** | Minimum evidence before live use: 100 trades, 20 sessions, drawdown ≤ 1.5× backtest (`gates` config). |
| **Regime** | Market state from a 3-state hidden Markov model and the 200-day average; sets each strategy's posture. |
| **Replay** | Running a recorded session's quotes back through the full stack. |
| **Sleeve** | One strategy's slice of capital and positions. |
| **Static IP** | The fixed IP registered with the broker, as SEBI requires for API orders. Here, delivered through a StaticIP.in proxy. |
| **TBT** | Fyers tick-by-tick feed: 50-level depth for up to 15 symbols (3 connections × 5). |
| **Tier (depth)** | Depth sources stacked deepest first: TBT (50) → Upstox D30 (30) → 5-level streams. |
| **Token bucket** | The rate limiter behind order and API budgets. |
| **Universe** | A list of stocks (e.g. `nifty50`, `nifty500`, `fno`, the liquid stocks with derivatives, used only as a liquidity list). |
| **Walk-forward** | A robustness test: every parameter perturbed ±20%, all variants compared. |
| **Watch** | `feeds.watch`: stream more stocks than the strategies need (e.g. `all`). |
