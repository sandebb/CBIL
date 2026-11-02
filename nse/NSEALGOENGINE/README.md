> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# nsealgo

A broker-agnostic, regime-aware automated trading system for NSE equities, with a backtester, a
forward-testing (paper-on-live-data) pipeline and live execution, all driven by **one engine**. The same
strategy code runs in every mode; only the clock, the data feed and the execution adapter change.

## Documentation

The full documentation suite is in **[docs/](docs/README.md)**: business case, compliance, requirements, architecture and decision records, developer guides, generated CLI/configuration/module references, test strategy and acceptance tests, operations runbook, and user and desk guides, with a start-here table for each role.

**New in 3.11:** the documentation suite. It has ten sections organised by role, 15 architecture decision records, and references generated from the code, kept current by `tests/unit/test_docs.py`.

**New in 3.10:** the strategy windows are saved. Every signal, fill and status change is written as it happens (forward, live and replay sessions; forward = live data with paper orders), and `nsealgo desk --review` charts any saved day. See [Trading desk](#trading-desk-gui).

**New in 3.9:** strategy windows in the desk: a trade map per strategy (entry → exit at % P&L, cumulative %), every signal with its order and fill times, and a chart per signal (signal and order times, BUY/SELL levels, where price met them, % P&L). See [Trading desk](#trading-desk-gui).

**New in 3.8:** a trading desk GUI (`nsealgo desk`): dashboard, tier-1 50-level depth ladders, market watch, kill switch; `--demo` runs it on synthetic data. See [Trading desk](#trading-desk-gui).

**New in 3.7:** choose the broker for intraday and delivery orders in config (`broker.orders`); Fyers is the default, and Shoonya is kept dormant. Fyers orders go through the static-IP proxy with the same fail-closed IP check. See [Choosing the order broker](#choosing-the-order-broker).

**New in 3.6:** Upstox 30-level depth (Upstox Plus) replaces Dhan's feed: up to 250 stocks over 5 websockets, automatically cut to 50 if Upstox enforces its limit per user. See [docs/03-architecture/PX13.md](docs/03-architecture/PX13.md).

**New in 3.5:** Fyers for the whole NSE universe (quotes for up to 5,000 stocks, or 2,500 with 5-level depth), Fyers TBT 50-level books for a rotating focus set of 15, and tiered depth sources (TBT, then the next tier, then 5 levels). See [docs/03-architecture/PX13.md](docs/03-architecture/PX13.md).

**New in 3.4:** trading on the order book. Multi-level order flow, depth imbalance and iceberg detection feed ORB, the scalper and the market maker wherever a deep book streams, and `nsealgo book-study` measures what each depth level is worth on your recordings. See [docs/03-architecture/PX13.md](docs/03-architecture/PX13.md).

**New in 3.3:** GPU compute for the Radeon 8060S (PyTorch on ROCm). Each calculation uses the GPU only after matching NumPy on your machine and measuring faster (`nsealgo gpu`; install with `setup.ps1 -Gpu`). Batched fragility maths is 3–4× faster even on the CPU. See [docs/03-architecture/PX13.md](docs/03-architecture/PX13.md).

**New in 3.2** (Dhan's feed replaced by Upstox in 3.6): Dhan 20-level depth from your own account (up to 250 stocks) merged onto Upstox quotes, with deep-book slots following your positions and candidates. See [docs/03-architecture/PX13.md](docs/03-architecture/PX13.md).

**New in 3.1:** PX13 part 1. Upstox 5/30-level depth; Shoonya orders through a static-IP proxy that refuses to trade from any other IP; pooled low-latency order connections; `nsealgo netcheck`. Plan for the GPU and depth-logic parts: [docs/03-architecture/PX13.md](docs/03-architecture/PX13.md).

**New in 3.0:** stocks only. Futures, options and other derivatives are gone, and so are the index/ETF sleeves (`pairs`, `noise_band`, `etf_rotation`, `index_mr`, `calendar_effects`). 17 intraday and swing strategies for NSE cash-market stocks remain. See [CHANGELOG.md](CHANGELOG.md).

**New in 2.5:** stocks are spread across several broker accounts through a sticky allocation table (`nsealgo accounts`). Each account has its own orders/s budget, so throughput adds up across brokers. See [docs/03-architecture/MICROSTRUCTURE.md](docs/03-architecture/MICROSTRUCTURE.md).

**New in 2.4:** `scalper` and `market_maker` (quote-driven, for paper testing, cost-gated) and multi-broker execution.

**New in 2.3:** documented strategies from books, papers and Indian forums, added as research sleeves:
- `turtle` and `supertrend`;
- `high52`, `low_vol` and `resid_momentum`;
- `compression_breakout` (NR7, inside day, narrow CPR) and `gap_fade`.

What's included, what isn't and why: [docs/05-reference/STRATEGY_CATALOGUE.md](docs/05-reference/STRATEGY_CATALOGUE.md).

**New in 2.2:** statistical research sleeves (`resid_reversion`, `skew_premium`), a market-fragility overlay and research commands (`anatomy`, `leadlag`, `fractal`) to measure ideas on your own data. See [docs/04-design/STRATEGY_RESEARCH.md](docs/04-design/STRATEGY_RESEARCH.md).

**New in 2.1:** Windows 11 is the reference platform: setup script, Task Scheduler deployment, keep-awake, process priorities and a runbook in [docs/07-operations/WINDOWS.md](docs/07-operations/WINDOWS.md).

**New in 2.0:** backtests, regime fits and robustness runs use every core (`nsealgo hardware`,
`nsealgo bench`); the engine does 3–7× less work per core; order safety for the exchanges' 2026 algo
rules. See [CHANGELOG.md](CHANGELOG.md), [docs/03-architecture/PERFORMANCE.md](docs/03-architecture/PERFORMANCE.md) and the item-by-item
review verdicts in [docs/09-project/history/REVIEW_RESPONSE.md](docs/09-project/history/REVIEW_RESPONSE.md).

> Not investment advice. SEBI's own study found 71% of individual intraday cash traders lost money
> (FY23). Treat everything here as a research tool until a
> strategy has passed the forward-test gates on your own account.

## How it fits together

```
 strategies (8) ── only see ──► StrategyContext (ctx): data, sizing, orders, state
                                        │
 feed ─► Engine: one loop for backtest / replay / forward / live
 (poll,      │  regime (HMM + 200-DMA) · crowding throttles · timers · end-of-day persistence
 stream,     ▼
 replay)  RiskManager (¼-Kelly with Bayesian shrinkage, caps, ES limit, breakers)
             ▼
          OMS (8 orders/s bucket, ≤20-char tags, stop→limit conversion, fill sync, reconciliation)
             ▼
          BrokerAdapter ── paper | zerodha | fyers | shoonya/flattrade/zebu | upstox | angelone | dhan
                           | icici | fivepaisa | kotak | aliceblue | groww   (+ yahoo, data only)

 nsealgo.parallel ── research backtests (strategy sleeves × period blocks), HMM fits and walk-forward
                     variants across all cores; merged into one report
```

| Mode | Clock | Market data | Execution |
|---|---|---|---|
| `backtest` | simulated | stored daily or 1-minute bars | PaperBroker |
| `replay` | simulated | a recorded day of live quotes | PaperBroker |
| `forwardtest` | real | your broker's live quotes (recorded) | PaperBroker |
| `live` | real | your broker's live quotes | your broker |

## Quick start (offline, 2 minutes)

Windows 11 (PowerShell, from the project folder):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
.\.venv\Scripts\Activate.ps1
nsealgo demo
```

Any OS:

```bash
python -m venv .venv && source .venv/bin/activate      # Python 3.10+
pip install -e ".[dev]"
nsealgo demo            # builds a synthetic market, runs daily + minute backtests and a quote-level replay
nsealgo hardware        # cores/threads/RAM detected and the worker counts that will be used
pytest -q               # 169 tests (+1 that needs PyTorch)
```

The demo's P&L means nothing: the synthetic generator injects stylised effects so every code path
fires. Open the three `report.html` files it prints to see the reporting.

## Connect your broker

1. Install only your broker's SDK (several SDKs pin conflicting versions of each other's dependencies):
   `pip install -e ".[zerodha]"` (extras: `zerodha fyers shoonya upstox angelone dhan icici fivepaisa aliceblue groww yahoo`;
   Kotak: `pip install "git+https://github.com/Kotak-Neo/Kotak-neo-api-v2.git"`).
2. Run `nsealgo doctor`. It flags two known traps: `fyers-apiv3` and Kotak's SDK install the obsolete PyPI
   package `asyncio`, and `upstox-python-sdk` installs `uuid`; both shadow Python's standard library.
   Fix with `pip uninstall -y asyncio uuid`. `growwapi` needs `pandas<3`.
3. `cp .env.example .env`, fill in your broker's keys, and set `broker.data` in `config/system.yaml`.
4. `nsealgo login <broker>` (daily; the session is cached for the day with 0600 permissions), then
   `nsealgo smoke-test <broker> --place-test-order` — logs in, pulls the instrument master, a quote, daily
   and minute history, the order book and positions, then places a 1-share limit 10% below the market,
   confirms it and cancels it.

| Broker | Adapter status | Daily login |
|---|---|---|
| Zerodha Kite | full, streaming depth | browser OAuth (redirect to `http://127.0.0.1:5000/` is captured automatically) |
| FYERS | full, streaming depth | browser OAuth |
| Shoonya (Finvasia) | full, streaming depth | TOTP, automatic |
| Flattrade, Zebu | beta (same Noren API) | set `master_url`; Flattrade uses a session token |
| Upstox | full, streaming depth | browser OAuth |
| Angel One | full, streaming depth | TOTP, automatic |
| Dhan | full, polling quotes | access token generated on web.dhan.co |
| ICICI Breeze | beta, streaming, cash equities only | browser session |
| 5paisa | beta, cash equities only, polling | TOTP |
| Kotak Neo | beta, streaming, no history API (pair it with another feed) | TOTP + MPIN |
| Alice Blue | beta, streaming | API key session |
| Groww | beta, polling | TOTP |

Adapters were written against SDK signatures introspected from the installed packages (kiteconnect 5.2.2,
fyers-apiv3 3.1.18, NorenRestApiPy 0.0.22, upstox-python-sdk 2.30.0, smartapi-python 1.5.5, dhanhq 2.2.0,
breeze-connect 1.0.69, py5paisa 0.7.21.2, Kotak Neo v2, pya3 1.0.30, growwapi 1.5.0). They have **not** been
exercised against live accounts from this environment; run `smoke-test` before anything else. Tick sizes
in the Upstox, Dhan and Angel masters are assumed to be in paise (`tick_in_paise: false` to override).

## Trading desk (GUI)

```
pip install -e .[desk]           # PySide6
nsealgo desk --demo              # try every window with synthetic data, no session needed
nsealgo desk                     # watch a running forward test (--mode live for live trading)
nsealgo desk --review            # chart saved days (latest by default; --date 2026-10-01; --mode live)
```

**Forward testing gives you every window with live data and no real orders:** `nsealgo forwardtest` trades live market data against the paper exchange, and the desk connects to it by default.

- **Main window:** status cards (equity, day P&L, exposure, regime, working orders, session link), and the Positions, Orders, Strategies and Alerts tabs. A coloured banner shows LIVE, FORWARD or DEMO.
- **Tier-1 depth · 50 levels:** the Fyers TBT stocks in live ladders, one tab per TBT connection (5 stocks each). Each ladder shows 50 levels a side with order counts and depth bars. Above it: total buy/sell quantity, spread, imbalance, order-flow z-score, data age, and iceberg/stale flags. You can switch to the Upstox 30-level tier, show cumulative depth, change the refresh rate (250 ms by default), or pause.
- **Market watch:** every streamed stock with its book signals. Sortable and filterable; double-click a row to open its own ladder window.
- **Strategy windows** (one per strategy, from the "Strategy windows" menu or by double-clicking a row on the Strategies tab):
  * **Trade map:** each trade is a bar from its entry to its exit, at its % profit or loss (dashed while open), with a cumulative % line below.
  * **Signal list:** signal time, price at the signal, when the order was sent, status, fill time and price, % P&L, and the exit reason. A signal that never filled shows what it would have made.
  * **A sub-window per signal:** double-click a signal, or press "Open all trade charts" and tile them. Each shows the scrip's price path with:
    - lines at the signal time, the order time, the exit signal and the exit;
    - horizontal BUY and SELL lines at the fill prices, and the stop;
    - dots where the price met those levels;
    - the entry and exit fills, and the % P&L.
  * Swing strategies chart daily bars; intraday strategies chart today's minute bars.
- **Saved days:** every forward, live and replay session writes its journal as it happens (`runs/<mode>/journal/<date>.jsonl`), together with the minute bars of every scrip that had a signal.
  * A restart mid-session reloads the day.
  * `nsealgo desk --review` opens any saved day in the same strategy windows, with a day picker. Depth, market watch and the kill switch need a running session.
- **Kill switch:** asks first, then sets the same switch as `nsealgo kill`.

The desk runs as its own process and reads the session through a read-only service on 127.0.0.1 that needs a per-session token. Closing or freezing the desk can't affect trading.

## Choosing the order broker

Forward tests always trade against the paper exchange. For `nsealgo live`, `config/system.yaml` decides which broker places which orders:

```yaml
broker:
  orders:
    intraday: fyers      # MIS orders
    delivery: fyers      # CNC orders (defaults to the intraday broker)
  brokers:
    shoonya: {..., orders: false}   # dormant: configured, but never sent an order
```

- **Two brokers:** if intraday and delivery name different brokers, orders are split by product, and modifications and cancellations go back to the broker holding the order.
- **Override:** `nsealgo live --broker X` sends everything to X for one run.
- **Dormant brokers:** a broker with `orders: false` can still log in and supply data, but any attempt to route orders to it stops before the session starts.
- **Static IP:** the order broker's `proxy` and `static_ip` settings apply (Fyers and Shoonya). With `static_ip` set, login is refused unless your requests leave from that IP.

## Forward testing on free live tick data

Free live market data (checked September 2026; `nsealgo feeds` prints the current table): FYERS, Shoonya,
Flattrade, Zebu, Angel One, Upstox, Kotak Neo, ICICI Breeze and Alice Blue. Paid: Zerodha (₹500/month for
Kite Connect with data), Dhan (₹499 + taxes/month) and Groww (₹499 + GST/month).

```bash
nsealgo feed-check --brokers shoonya,angelone,fyers --universe nifty50 --minutes 10   # during market hours
nsealgo forwardtest --data-broker shoonya,angelone,fyers                              # or set broker.data
```

List several brokers and the **tick mesh** merges them into one stream:

- instruments are sharded across feeds within each one's subscription cap, each on two feeds by default;
- the first copy of every print wins and repeats are dropped; stale prints are rejected because cumulative
  volume and exchange time never go backwards;
- order-book depth comes from one "depth leader" per instrument, so order-flow imbalance is never computed
  across two brokers' differently timed snapshots;
- a feed silent for 20 s during market hours is failed over to another feed with spare capacity, or to REST
  polling; a daily report (`runs/forward/report/feeds_<date>.json`) shows each feed's speed and reliability.

`feed-check` streams the same instruments from every listed broker and reports first-to-arrive share, lag
behind the fastest feed, exchange-to-receipt latency, duplicates, stale prints, stalls and cross-feed price
disagreements, so you can pick the best free feeds for your connection.

Practical notes. TOTP brokers (Shoonya, Angel One, Kotak, Zebu) log in automatically every morning, which
suits an unattended daemon; FYERS, Upstox and ICICI need a daily browser login (`nsealgo login fyers`), and
the daemon waits up to 20 minutes per feed before running the day without it. The static-IP rule covers
order placement (Zerodha and ICICI say so explicitly), so a paper forward test works from home broadband.
Broker feeds are change-driven snapshot streams, not the exchange's paid tick-by-tick order feed, and their
data is licensed for your personal use only.

## Data

- Universes: put NSE constituent CSVs in `config/universes/` (`nifty500.csv`, `nifty100.csv`, `nifty50.csv`).
  `fno.csv` is simply a list of liquid stocks (those with exchange-traded derivatives), used for stock selection only.
- Events: keep `config/events/results_calendar.csv` and `index_events.csv` current (they drive PEAD and
  forced-flow). The shipped index file has the September 2026 Nifty 50 change; verify it against NSE's release.
- History: `nsealgo fetch-history --days 800 --minute-days 20` fills `data/history.db` incrementally.
  Forward-test sessions append each day's bars automatically.

## From idea to live money

```bash
nsealgo backtest 2021-01-01 2026-09-25                         # swing strategies on daily bars
nsealgo backtest 2026-08-01 2026-09-25 --granularity minute --strategies orb,vwap_fade
nsealgo walkforward momentum 2021-01-01 2026-09-25             # ±20% parameter perturbation: robust or fragile?
nsealgo forwardtest --daemon --benchmark runs/backtest_daily_.../summary.json
nsealgo replay recordings/2026-09-29.jsonl.gz                  # re-run any recorded day deterministically
nsealgo live --broker zerodha --i-understand-the-risk          # only after the gates pass
nsealgo kill                                                    # emergency: block entries, flatten intraday
```

**Promotion gates (per strategy, shown on every report):** at least 100 trades and 20 sessions of forward
testing; the 90% bootstrap lower bound of expectancy above zero; drawdown no worse than 1.5× the backtest's;
no drift below the backtest's confidence band. A strategy whose upper bound is below zero after 30 trades
is marked for retirement. Promote at a quarter of normal risk.

The forward test uses a deliberately pessimistic simulated exchange: fills only on data arriving after the
order, resting limits need a trade-through, stop-limits gap like the real thing, fills capped at 25% of bar
volume, stop-losses cancelled when a stock enters the closing auction, orders after 15:15 fill only at the
closing price, and no delivery short selling. Every quote seen is recorded to `recordings/` so any day can
be replayed and debugged.

## Strategies

| Strategy | Horizon | Regime posture | Idea |
|---|---|---|---|
| `orb` | intraday | liquidity-taking | Stocks-in-play 5-min opening-range breakout on liquid stocks, RVOL-ranked, OFI-confirmed |
| `vwap_fade` | intraday (probation) | liquidity-providing | Quiet-day 2σ VWAP-band fade with a VPIN toxicity veto |
| `momentum` | swing | liquidity-taking | Top-decile momentum, volatility-contraction breakouts, comomentum throttle |
| `pead` | swing | liquidity-taking | Gap-and-hold results days, buy-stop over the day-0 high |
| `pullback` | swing (probation) | liquidity-providing | Panic pullbacks in uptrends, not news-driven; retires itself if thin |
| `forced_flow` | swing | liquidity-providing | Index inclusion/deletion flows and closing-auction imbalance fades |

Regime: a 3-state Gaussian HMM on Nifty 500 returns and India VIX level (sticky transitions, filtered
probabilities only, relabelled by variance, 2-day hysteresis), combined with the 200-DMA baseline by taking
the more cautious reading. Calm: everything on. Turbulent: liquidity-providing strategies off, new swing
longs at half size. Crisis: no new swing longs, intraday at half risk.

The closing-auction imbalance fade needs NSE's indicative auction data; wire your broker's field into
`BrokerAdapter.auction_snapshot` and it activates on heavy-flow days (dormant otherwise).

## Risk controls

¼-Kelly sizing with Bayesian-shrunk win rate and payoff (per-strategy priors), capped at 0.5% (intraday)
and 1% (swing) of equity; automatic switch-off when the posterior edge turns negative after 30 trades;
caps at 0.25% of 20-day traded value, 20% of equity per swing position, 2 names per sector, ₹25 lakh per
order (our fat-finger guard, not a regulation) and 3× intraday gross exposure; no delivery (CNC) sell beyond
holdings, so no overnight cash shorts; gap-aware sizing (worst 1% overnight gap ≤ 2× planned risk);
portfolio 97.5% expected-shortfall cap; −1.5% daily intraday loss limit and a three-losses-in-a-row halt;
correlated-drawdown breaker; stops placed off round numbers; kill switch; broker position reconciliation
every minute (live halts on mismatch).

## SEBI retail-algo checklist (framework mandatory since 1 April 2026)

- **Static IP:** register one with your broker and place orders only from it (a VPS works; see `deploy/`). Market data works from any IP.
- **Daily login:** log in with 2FA every day (`scripts/morning.sh`).
- **Order rate:** capped at 8 orders/s (the limit is 10 without an exchange-registered strategy).
- **Market orders:** unprotected MARKET and SL-M algo orders are rejected. nsealgo sends marketable LIMITs (±0.5%) and stop-LIMITs for every broker, and Zerodha also gets `market_protection=-1`; keep `kiteconnect` current.
- **Unfilled exits:** re-priced every 5 s, up to 2% (`oms.exit_chase_*`).
- **Algo ID:** set `algo_id` if your broker issues one.

## Using every core

`nsealgo backtest` spreads a backtest over one process per physical core (research mode):
- **Sleeves:** every strategy runs as an independent sleeve on constant capital, cut into period blocks with warm-up and run-off.
- **Regime:** the HMM regime timeline is fitted in parallel once and shared by all jobs.
- **Merging:** results are merged into one report, and the tests require the trades to be identical to a continuous run.
- **Faithful mode:** `--faithful` runs the single live-identical engine instead (compounding, Kelly learning, shared caps, breaker).
- **Walk-forward:** `walkforward` runs all parameter variants at once.
- **Measuring:** `nsealgo bench` measures the speedup on your machine.

Details, worker policy (SMT, RAM, containers, BLAS) and Windows notes: [docs/03-architecture/PERFORMANCE.md](docs/03-architecture/PERFORMANCE.md).

## Layout

```
src/nsealgo/core         engine, context, regime, crowding, risk, OMS, portfolio, costs, storage, indicators
src/nsealgo/brokers      adapter contract, paper exchange, 13 broker adapters, symbol master
src/nsealgo/strategies   the eight strategies
src/nsealgo/data         history store, feeds (poll/stream/record/replay), universes, events, synthetic market
src/nsealgo/backtest     backtest runner (research / faithful), parallel robustness tests
src/nsealgo/parallel     hardware detection, process/thread pools, job planner, worker, merge, bench
src/nsealgo/forwardtest  forward/live session loop, replay, reports and promotion gates
tests/unit               fast tests: indicators, costs (exact), risk, OMS safety, regime, pools, adapters, mesh
tests/integration        engine end-to-end, forward-test mesh, parallel == continuous backtests
docs/                    WINDOWS.md (runbook), PERFORMANCE.md (multi-core), REVIEW_RESPONSE.md (verdicts + sources)
config/                  system.yaml, holidays, universes, events
deploy/                  windows/ (Task Scheduler), Docker, systemd, cron
scripts/                 windows/ (setup.ps1, morning.ps1), morning.sh, parallel_backtest.py
```
