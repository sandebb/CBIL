> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Changelog

## Unreleased (22 Oct 2026): research - interdependence with market, sectors, other traders

- **`scripts/research/overnight_interdependency.py`** -> `runs/research_interdep/report.txt`. Top-5 gap = market +16.5
  bps + sector +3.5 + own +26.4; the market gap explains 53% of the night-to-night spread (US S&P overnight corr +0.40).
  Beyond the ML: closing at the day high predicts a smaller gap (-13.6 bps, t -3.9), the sector's move today a bigger
  one (t +2.1); 'sector theme' picks realise 81% of the predicted gap vs 69% for lone names. Same-sector picks do not
  move together more than different-sector ones. Not used in the trade yet.

## Unreleased (21 Oct 2026): daily 1-minute archive top-up

- **`run_minute_archive.cmd`** + **`deploy/windows/register_minute_archive_task.ps1`** -> task
  `NSEALGOENGINE-MinuteArchive`, Mon-Fri 19:05: `nsealgo fetch-minute-archive --unattended` (new flag: uses the day's
  Fyers token, never a browser login), then `nsealgo liquidity-table`. Until now the archive was only topped up by
  hand (last run 17 Oct -> bars to 16 Oct). Log `runs/logs/minute_archive_task.txt`.

## Unreleased (19 Oct 2026): overnight trade - no Rs 50 floor, untradable stocks blocked

- **`size-overnight`**: the Rs 50 price floor is gone (`--min-price`, default 0). New check `tradable_check`: stocks in
  trade-for-trade series BE / BZ (from the at-close snapshot's Fyers symbol, else NSEDATAENGINE's fresh rebuild) and
  every stock on NSE's ASM (long / short term, any stage) or GSM list (NSEDATAENGINE `data_ref/surveillance.parquet`,
  newest snapshot <= 5 days old) are left out - they cannot be bought tonight and sold tomorrow morning. If the list
  cannot be checked, the Rs 50 floor comes back for that night. The output lists what was left out and its rank.
- Backtest (`overnight_price_floor_backtest.py --exclude-t2t`, 658 nights, OPTIMIZED top 5, Rs 5 lakh, BE/BZ removed):
  sell at open Rs 1,260 (floor 50) -> Rs 1,416 (none); 75%-12:00 + 09:07 check Rs 1,553 -> Rs 1,735 (t 3.3). ASM / GSM
  could not be backtested (NSE keeps no history; snapshots only since 17 Oct 2026).

## Unreleased (19 Oct 2026): research - the Rs 50 price floor

- **`scripts/research/overnight_price_floor_backtest.py`** (+ `MIN_PRICE` in overnight_sizing_backtest.py, default 50):
  floor Rs 50 / Rs 20 / none, same trade. OPTIMIZED top 5, Rs 5 lakh: sell at open Rs 1,241 / 1,316 / 1,389 a night;
  75%-12:00 + 09:07 check Rs 1,533 / 1,637 / 1,675 (no floor vs Rs 50: +Rs 142, t 2.4). Stocks below Rs 50 take ~5%
  of the money. Not adopted yet - the live floor (MIN_PRICE in nsealgo.sizing.overnight) is still Rs 50.

## Unreleased (19 Oct 2026): research - buy only what the ML expects to close above its open

- **`scripts/research/overnight_filter_backtest.py`**: buy filter P50 predicted close > P50 predicted open (the actual
  open is unknown at 15:40), with and without refilling from ranks up to 40, sold at the open / 75%-12:00 always /
  75%-12:00 + 09:07 check. Only ~8% of the top 5 pass; 68-84% of nights end up in cash; OPTIMIZED top 5 at Rs 5 lakh
  falls to Rs 37-255 a night vs Rs 1,237 now (t -4 to -5). Rejected; the switched exit rule (no buy filter) stays.

## Unreleased (19 Oct 2026): overnight trade - new morning sale rule (`exit-overnight`)

- **`nsealgo exit-overnight`** (run ~09:06:30): per stock bought the evening before (`runs/overnight_sizing_<day>.csv`,
  or `--positions` with the real fills), the indicative open from the pre-open recorder (or `--price SYM=PX`): if the
  ML's P50 predicted close is above the open -> LIMIT SELL at open + 75% of the way to the P50 predicted high (rounded up
  to 0.05) from 09:15, rest at market at 12:00; else SELL in the pre-open auction. Writes `runs/overnight_exit_<day>.csv`
  and `runs/overnight_exit_orders_<day>.csv`. Places NO orders. `size-overnight` now also prints each stock's "hold if
  the open is below" price for the next morning. Module `nsealgo.sizing.overnight_exit`; 3 unit tests.

## Unreleased (19 Oct 2026): overnight trade - sell at a target instead of at the open (research)

- **`scripts/research/overnight_exit_backtest.py`**: same buys and sizing as the sizing backtest; the sale is a limit at
  open + f x (ML P50 predicted high - open), f = 0.25 / 0.5 / 0.75 / 1.0, with what is unsold sold at 12:00 / 15:15 /
  the close (1-minute bars, trade-through by a tick, at most 10% of the volume at the target). Result: every target
  rule earns LESS than selling at the open (OPTIMIZED top 5, Rs 5 lakh: Rs 1,237 -> Rs 867 a night at the midpoint) -
  stocks that never reach the target fade ~3% and cost more than the winners gain. Selling at the open stays the rule.
  Output `runs/overnight_exit.txt` / `_summary.csv` / `_nightly.csv`; cache `runs/exit_bars/`.
- **Gated exits** (same script): wait for the target only when the ML's P50 close is above the open (the ML open, or the
  actual auction price at 09:07), otherwise sell at the open. All 8 gated rules beat selling at the open (OPTIMIZED top 5,
  Rs 5 lakh: +Rs 58 to +Rs 292 a night; best '75% to high, else at 12:00 [ML close > actual open]' Rs 1,237 -> Rs 1,529)
  but none is statistically sure (t <= 1.5) and most of the gain is 2025 - needs a forward check before any change.

## Unreleased (18 Oct 2026): overnight trade sizing by volume and rank

- **`nsealgo size-overnight --capital 500000 --top 5`**: splits the capital among YOUR top N (5 / 9 / 18) - not
  equally: by each stock's price (whole shares, leftovers topped up), traded volume (expected post-close session and
  morning auction), volatility and expected move; prints the equal split of the same stocks next to it (fills,
  slippage, expected profit). Backtest, Rs 5 lakh in the top 5: Rs 707 -> Rs 1,269 a night after charges.
- **`nsealgo size-overnight --capital N`** (no --top): tonight's rupees and shares per stock for the overnight trade (buy in the
  15:40-16:00 post-close session at the close, sell in the next pre-open auction) from the 15:37 forecast. Each stock is
  capped by the part of its expected post-close session we can fill (50%) and of the next morning's auction we sell
  into (10%); the money goes where the expected move (from the predicted gap) minus costs and our own push on the
  opening price is highest (water-filling, `nsealgo.sizing.overnight`); what the sessions cannot take stays in cash.
  Writes `runs/overnight_sizing_<day>.csv` and `runs/overnight_orders_<day>.csv` to review - places NO orders.
- **`nsealgo liquidity-table`**: how big both sessions usually are per stock (`data/liquidity_table.parquet`), from the
  1-minute archive (daily volume minus the 1-minute volumes - Fyers' 09:15 bar held the auction Nov 2023 - Sep 2025),
  the pre-open recorder and the at-close snapshots. size-overnight rebuilds it when it is a week old.
- `scripts/research/overnight_sizing_backtest.py`: equal top 1 / 5 / 9 / 18 vs volume-capped vs the model, Rs 1 lakh ->
  Rs 1 crore -> `runs/overnight_sizing.txt`, `runs/ML_overnight_sizing.xlsx`, `runs/overnight_edge.json` (the edge
  model size-overnight uses; re-run monthly).
- Tests: `tests/unit/test_overnight_sizing.py`.

## Unreleased (16-17 Oct 2026): Shoonya orders, partial fills

- **Orders go to Shoonya, Fyers is data only**: `broker.orders` intraday and delivery = shoonya; Shoonya stays
  dormant (`orders: false`) until `scripts/windows/setup_shoonya.ps1 -TestOrder -Enable` passes.
- **Partly filled intraday entries**: an MIS LIMIT entry still open after `oms.entry_ttl_seconds` (90) is cancelled;
  the shares bought are kept and protected (the stop is re-sized on every fill). `oms.min_partial_fraction` (0 = off)
  sells a partial entry below that share of the intended size. `Strategy.exit` cancels a still-open entry first, so
  a half-filled entry can't add shares after the exit.
- **Intraday time rules**: no intraday (MIS) entries before `session.intraday_entry_start` (09:30) or from
  `session.no_new_intraday` (14:30) - enforced for every strategy at order submission (it was configured but not
  enforced); swing / CNC entries, exits and stops are exempt. Intraday exits start 14:50, everything is flattened at
  14:55, so all intraday trades are closed by 15:00.
- **Separate books** (`risk.books`, 50/50 by default): swing and intraday size, exposure and loss limits are
  measured against each book's own capital; new swing entries stop when the swing book's capital is in use.
- **Sizing from the stop**, now also capped by the broker's free cash / margin (live; `risk.mis_margin_frac`) and,
  for intraday, by `risk.depth_share` (25%) of the quantity at the best price. Shoonya reports free funds.
- **Daily loss limit and kill switch per book**: intraday -3% of the intraday book -> halt + flatten; swing -2% of
  the swing book -> no new swing entries that day (stops stay). `nsealgo kill --book intraday|swing|all`.
- **Per-broker budgets**: `accounts.<name>.capital` caps open positions per account in the multi-broker.
- **Fyers TBT recorder** (scalping research step 1): every 50-level depth message to parquet in NSEDATAENGINE\tbt
  (`paths.tbt_dir`), with feed / send / local receive times, ltq and vtt_diff. `nsealgo tbt-record --symbols ...`
  records without strategies; `feeds.depth_overlay.tbt_record: true` records during forward / live runs;
  `nsealgo tbt-check` reports message counts, how often ltq / vtt_diff come and latency percentiles.
- Fyers login uses today's token from NSEUIENGINE's 08:00 task (no browser login needed). fyers-apiv3 pinned
  >= 3.1.19 (older versions corrupt TBT depth diffs); pyarrow added to the fyers extra.
- Tests: `tests/unit/test_books.py`, `tests/unit/test_partial_entries.py`, `tests/unit/test_intraday_time_rules.py`; shipped-config routing
  test updated for Shoonya.

## 3.11.0 (October 2026): documentation suite

- **`docs/` reorganised by role** into ten sections:
  * 00 overview, 01 business, 02 requirements, 03 architecture, 04 design;
  * 05 reference, 06 testing, 07 operations, 08 user, 09 project.
  
  `docs/README.md` maps who should read what.
- **New documents:**
  * charter, system overview, glossary;
  * business case, compliance mapping, risk register;
  * PRD and SRS with requirement IDs;
  * architecture, data and security architecture, and 15 ADRs;
  * developer guide, engine lifecycle, strategy and broker-adapter guides, coding standards;
  * Monitor API;
  * test strategy, test plan, acceptance tests, traceability matrix;
  * installation, runbook, monitoring, backup, troubleshooting;
  * user and desk guides;
  * project plan, RACI, release process, documentation maintenance.
- **Generated references** (`scripts/docs/build_all.py`): CLI, configuration, module reference, test catalogue. `tests/unit/test_docs.py` fails when they are stale, a link is broken, or a document is missing from the index.
- **Existing documents moved** into the hierarchy:
  * `docs/03-architecture/`: PX13, MICROSTRUCTURE, PERFORMANCE;
  * `docs/04-design/`: STRATEGY_RESEARCH;
  * `docs/05-reference/`: STRATEGY_CATALOGUE, FYERS_API_NOTES;
  * `docs/07-operations/`: WINDOWS;
  * `docs/09-project/history/`: REVIEW_RESPONSE.
  
  References in the README, config and code were updated.
- **Fixed:** `nsealgo netcheck` defaulted to Shoonya, which has been dormant since 3.7. It now defaults to your intraday order broker.
- **Tests:** 169, plus 1 that needs PyTorch.

## 3.10.0 (October 2026): saved strategy windows

- **The signal journal is saved as it happens** (config `journal`):
  * signals, fills and status changes are appended to `runs/<mode>/journal/<date>.jsonl`, for forward, live and replay sessions;
  * the minute bars of every scrip with a signal go to `<date>.bars.json`, every 5 minutes and at day end;
  * a restart on the same day reloads the file first, and a half-written last line after a crash is skipped.
- **`nsealgo desk --review [--date YYYY-MM-DD] [--mode forward|live]`** opens saved days in the strategy windows:
  * with a day picker, no session needed;
  * swing strategies chart daily history;
  * depth, market watch and the kill switch are disabled, and look it.
- **Forward testing** (live data, paper orders) gets all of this; tests check that a forward session writes its day.
- **Demo data:** varies with its seed, so demo days differ.
- **Tests:** 163 in total.

## 3.9.0 (October 2026): strategy windows

- **New `core/journal.py`:** a signal journal following every signal's life.
  * It records the strategy's decision (time, price, stop, reason), when the order was sent, its status, and its fills (time, average price).
  * Entry and exit fills are paired into trades with % P&L, long and short; open trades are marked to the latest price.
  * Signals that never filled keep their signal price, for "would have" P&L.
  * It is hooked in at `Context.submit` and `Engine._on_fill`, and reset each trading day. Replay summaries report journal counts.
- **Monitor:** new endpoints `/api/strategy` (signals, trades, summary) and `/api/series` (today's minute bars, or daily bars for swing strategies).
- **Desk: a window per strategy,** with:
  * a trade map (entry → exit bars at % P&L, plus a cumulative %);
  * the signal list;
  * a sub-window per signal (open one, or all, tiled or cascaded) charting the scrip with signal, order and exit lines, BUY/SELL/STOP levels, the points where price met them, the fills, and the % P&L.
  
  Opened from the "Strategy windows" menu or the Strategies tab. Desk now also needs pyqtgraph.
- **Demo mode:** generates consistent signals, fills and charts for every strategy.
- **Tests:** 161 in total.

## 3.8.0 (October 2026): the trading desk

- **`nsealgo desk`** (PySide6; `pip install -e .[desk]`) opens three kinds of window:
  * **Main window:** a status banner and cards, plus Positions, Orders, Strategies and Alerts tabs. Buttons open the other windows; the kill switch asks first.
  * **Tier-1 depth:** Fyers TBT stocks as live 50-level ladders, one tab per TBT connection (5 stocks each).
    - Each ladder shows order counts, depth bars (or cumulative depth), and total buy/sell quantity.
    - Spread, imbalance, order-flow z-score and data age sit above it, with iceberg and stale flags.
    - The tier selector covers the other tiers (e.g. Upstox 30 levels); refresh is 250 ms–1 s, with pause.
  * **Market watch:** every streamed stock with its book signals, sortable and filterable; double-click opens a ladder.
- **`--demo`:** runs everything on realistic synthetic data.
- **`nsealgo.monitor`:** a read-only service started with forward and live sessions.
  * It runs on 127.0.0.1 only, with a per-session token kept in `runs/<mode>/monitor.json`.
  * Its only write action is the kill switch.
  * The desk is a separate process, so it can't stall trading.
- **Depth sources:** report their tiers grouped by connection; Fyers TBT also reports total buy/sell quantity.
- **Tests:** 156 in total, including offscreen GUI tests.

## 3.7.0 (October 2026): choose the order broker; Fyers for intraday, Shoonya dormant

- **`broker.orders: {intraday, delivery}`** chooses the live order broker per product.
  * `nsealgo live --broker X` overrides both, and `broker.execution: multi` still works.
  * If the two differ, a `ProductRouter` splits orders and sends modifications and cancellations back to the right broker.
  * Brokers already logged in as data sources are reused.
- **Dormant brokers:** `brokers.<name>.orders: false` keeps a broker configured for login and data, but refuses it as an order broker, including via `--broker` and in multi-account setups. Shoonya ships dormant.
- **Fyers order traffic now uses the transport.** The SDK's own `requests.Session`, which had no timeouts, is replaced by the pooled connection with the static-IP proxy. With `static_ip` set, login is refused unless requests leave from it.
- **Fixed: Fyers code -99.** The SDK turns timeouts and connection errors into `{"s": "error", "code": -99}`, so an order that may have reached Fyers looked rejected. It's now checked in the order book by tag, or left uncertain for the next sync, as with code 201.
- **Fixed: the `fyers` entry in `config/system.yaml` loaded as empty since 3.5.** A comment edit had swallowed its credentials mapping. It is a proper block now, and a test loads the shipped config to catch this.
- **Tests:** 148 in total.

## 3.6.1 (October 2026): Fyers adapter audited against the docs with Python samples

Fixed:
- **Order tags.** Fyers returns your tag as `1:<tag>` (and `2:...` for its own). The prefix wasn't stripped, so the OMS could never confirm an order by its tag after a timeout, and the multi-broker duplicate check couldn't either.
- **Code 201 (placed, not acknowledged).** Either outcome was wrong before:
  * with `s: error`, the order was marked REJECTED although it might be live;
  * with no id, it became UNKNOWN but could never be matched, because of the tag prefix.
  
  Now the order book is checked by tag at once; if the order isn't there yet, the new `OrderUncertain` (not a `BrokerError`) leaves it UNKNOWN for the next sync to confirm.
- **Account-wide rate limit.** Fyers allows 10 calls/s and 200/min across all endpoints (Prime: 600/min), and blocks the user for the day after more than 3 per-minute violations. Calls were only spaced per endpoint. A global budget now holds every call at 90%; any adapter can set `api_limits`. The holdings call is also counted now.

Improved:
- **T1 holdings:** counted once whichever field carries them.
- **History:** candles still forming (the current minute, or today's daily candle before 15:30) are dropped.
- **Data socket:** `reconnect_retry=50` (the documented maximum), and SDK logs go to the cache folder instead of the working directory.

Also:
- `docs/FYERS_API_NOTES.md` was rewritten against the 187-page export, with page numbers.
- **Tests:** 141 in total.

## 3.6.0 (October 2026): Upstox 30-level depth replaces Dhan

- **Removed:** Dhan's 20-level depth feed. The Dhan broker adapter stays for orders and ordinary data.
- **New `data/upstox_depth.py`:** a 30-level (`full_d30`, Upstox Plus) overlay source.
  * up to 5 websockets of 50 stocks: 250 in all, per Upstox's Websocket Plus announcement;
  * stocks rotate with the focus list.
- **Per-user cap detection:** an Upstox staff answer says 50 per user. If a websocket receives nothing for 60 s while another streams, it is retired, capacity locks to what Upstox serves, and the reason is logged.
- **Tiers:** `source: fyers_tbt,upstox` gives 50 levels (TBT) to the top 15, then 30 levels (Upstox) to the next 250 or 50.
- **Tests:** 135 in total.

## 3.5.1 (October 2026): Fyers checked against its official documentation

- **TBT now follows Fyers' documented limits:** up to 3 connections per app per user, with 5 symbols each (15 in all). 3.5.0 put all 15 symbols on one connection, which Fyers would refuse beyond the fifth.
- **Book levels:** each level is placed by its `num` field (0–49), as the docs define it.
- **Fyers data stream capacity:** follows the docs' 5,000 data subscriptions on one socket. With `depth: depth5` it's 2,500 stocks, since quotes and depth are separate subscriptions.
- **Corrected claims:**
  * TBT is no longer described as a paid add-on; the docs don't say.
  * NSE equity coverage is now confirmed from the docs, and dated 23 Jun 2025.
- **New `docs/FYERS_API_NOTES.md`:** every Fyers fact nsealgo relies on, with page references.
- **Tests:** 134 in total.

## 3.5.0 (October 2026): PX13 part 4, Fyers universe and TBT

- **Fyers TBT 50-level depth** (`data/fyers_tbt.py`), with the protocol taken from Fyers' official SDK:
  * URL lookup, the `APPID:TOKEN` header, and a `ping` every 10 s;
  * subscriptions in channels of 5, then resumed;
  * books rebuilt from a snapshot plus field-level diffs exactly as the SDK applies them, never used before a snapshot;
  * Fyers' refusal messages kept;
  * reconnects resubscribe;
  * a symbol cap (15 by default).
- **`TieredDepth`:** several overlay sources in depth order (`source: fyers_tbt,dhan`). Each takes the next slice of the focus list, and quotes get the deepest fresh book.
- **Fyers universe stream:**
  * the default capacity is now 5,000 symbols (the SDK's limit);
  * `depth: depth5` streams 5 levels with order counts and merges them onto quotes.
- **`feeds.watch`:** `all`, or a universe name, streams more than the strategies need, giving universe-wide book signals.
- **`nsealgo book-study`:** accepts many recordings and studies them together in memory.
- **Tests:** 132 in total. TBT frames are built with Fyers' own protobuf schema.

## 3.4.0 (September 2026): PX13 part 3, trading on the order book

- **`core/book.py`:** per-stock deep-book signals, computed on every quote batch through the CPU/GPU compute layer and exposed as `ctx.book(key)`:
  * multi-level order-flow z-score;
  * depth-weighted imbalance, microprice edge and ±10 bps pressure;
  * iceberg (absorption) detection on both sides.
- **Strategies:** each falls back to its previous top-of-book logic when there's no deep book.
  * `orb`: multi-level flow must agree with the breakout, and no iceberg may sit against it.
  * `scalper`: uses multi-level flow and imbalance, and won't trade into an iceberg.
  * `market_maker`: leans its fair price away from icebergs.
- **`nsealgo book-study`:** measures each signal's predictive power by depth level and horizon, on your own recordings.
- **Synthetic recordings:** can carry depth levels, so the whole chain (recording → engine → signals → strategy) is tested end to end.
- **Tests:** 125 in total, plus one that needs PyTorch.

## 3.3.0 (September 2026): PX13 part 2, GPU compute

- **`nsealgo.compute`:**
  * kernels are written once against NumPy/PyTorch ops;
  * `Compute` picks CPU or GPU per kernel and size: the result must match NumPy, then the GPU must be over 10% faster;
  * decisions are cached per machine signature, and any GPU error falls back to the CPU;
  * research worker processes always use the CPU.
- **Kernels:**
  * batched covariance eigenvalues and batched Prim minimum spanning trees (fragility);
  * order-book features (microprice, weighted imbalance, ±bps depth, pressure);
  * multi-level order-flow imbalance.
- **Fragility series:** windows are batched by their valid stock set. Results are identical to the per-window loop, and 2.9–4.1× faster on the CPU alone.
- **Setup and config:**
  * `nsealgo gpu` shows the device, plus agreement, timing and the choice made per kernel;
  * `setup.ps1 -Gpu` installs AMD's ROCm 7.2.1 PyTorch wheels (Python 3.12);
  * new config section: `compute` (device, gpu_dtype).
- **Tests:** 119 run here, plus a PyTorch-vs-NumPy test that runs wherever PyTorch is installed.

## 3.2.0 (September 2026): PX13 part 1.5, deeper books

- **Dhan 20-level depth** (`data/depth.py`), from your own Dhan account:
  * up to 5 connections × 50 instruments (250 stocks);
  * subscribe with code 23 and unsubscribe with code 24; reconnects resubscribe and back off;
  * the binary parser matches Dhan's documented layout and official SDK;
  * levels carry order counts.
- **`DepthOverlayFeed`:** merges the Dhan book onto main-feed quotes only when it is deeper, fresh and uncrossed.
- **Depth focus:** every `feeds.depth_focus_seconds`, deep-book slots follow open positions and then intraday candidates (round-robin across strategies).
  * Upstox rotates its 50 `full_d30` slots; Dhan re-subscribes its 250.
  * Strategies can override `depth_focus(ctx)`.
- **Tests:** 112 in total. The forward-mode session test checks that the engine re-ranks focus every minute.

## 3.1.0 (September 2026): PX13 part 1, Upstox depth and Shoonya via static IP

- **Upstox feed:** every depth level is parsed into `Quote.bids` / `Quote.asks`.
  * `depth.mode: full_d30` streams up to 50 chosen NSE stocks with 30 levels (Upstox Plus); the rest stream in `full` (5 levels).
  * Recordings keep `feeds.record_depth` levels (default 5), through one shared JSON format.
- **Shoonya orders through a static-IP proxy** (`proxy`, `static_ip`): with `static_ip` set, login and orders refuse to run unless requests leave from that IP (fail closed).
- **New `brokers/transport.py`:**
  * a pooled keep-alive connection with TCP_NODELAY and SO_KEEPALIVE, proxied sockets included;
  * warm-up at login and a keep-alive ping;
  * timeouts on every call (the Noren SDK had none) and no automatic retries;
  * per-endpoint latency statistics;
  * the Noren SDK's HTTP calls routed through it.
- **`nsealgo netcheck`:** egress IP versus the static IP, and new-connection versus kept-alive latency on your line.
- **Config:** `broker.data: upstox`, with proxy and static-IP settings for Shoonya.
- **Plan:** `docs/PX13.md` covers parts 2 (GPU) and 3 (depth logic).
- **Tests:** 105 in total. Transport tests use a real local server and forwarding proxy.

## 3.0.0 (September 2026): stocks only

- **Removed:** futures, options and every other derivative:
  - futures instruments in all 11 broker symbol masters, and the carry-forward (NRML) product;
  - front-month futures lookup, lot rounding, expiry exits, and futures cost rates;
  - synthetic futures data.
- **Removed:** strategies that aren't intraday or swing trading of stocks:
  - `pairs`, which needs overnight shorts through futures;
  - `noise_band`, `etf_rotation`, `index_mr` and `calendar_effects`, which trade indices or ETFs;
  - the `pairs-scan` research command, and ETF-specific cost rates.
- **Changed:**
  - `resid_reversion` and `skew_premium` are long-only;
  - `supertrend`, `gap_fade`, `scalper` and `market_maker` default to stocks;
  - the `fno` universe is now just a CSV list of liquid stocks.
- **Unchanged:** the NIFTY index and India VIX are still read as data for the regime model, and never traded.
- **Fixed:** `resid_momentum` never scored anything because it didn't request today's index bar. The old "6 of 8 sleeves trade" test let that hide; the new test requires 4 of the 5 daily stock sleeves to trade, and it now passes with `resid_momentum` included.
- **17 strategies; 95 tests.**

## 2.5.0 (September 2026): stocks split across brokers

- **Sticky allocation table** (`.cache/multi_state.json`):
  - each stock trades at one broker account, for every strategy, and futures follow their underlying;
  - new stocks go to the account with the most spare capacity;
  - pins come from config or `nsealgo accounts --pin`;
  - `--rebalance` moves only idle, unpinned stocks.
- **Per-broker order-rate budgets:** `accounts.<name>.orders_per_second`, capped at 10 unless that account's algo is registered. The OMS's overall governor is their sum. This replaces 2.4's single budget across all accounts.
- **Outages:** only stocks without open positions or orders are moved off an unreachable broker, after confirming the order never arrived there. Rejections are never re-routed; positions never move.
- **Tests:** 96 in total.

## 2.4.0 (September 2026): scalping, market making, multiple brokers

- **`scalper`:** order-flow-imbalance scalper with a rupee cost gate.
- **`market_maker`:** Avellaneda-Stoikov inventory-skewed quoting with a cost floor, inventory cap, loss limit, stale-data pull, quote-life and message budgets.
- Both are quote-driven, for paper testing, and off by default.
- **Multi-broker execution** (`broker.execution: multi`):
  - per-strategy routing and sticky position ownership that survives restarts;
  - failover of new entries only after the first account confirms it never received the order;
  - rejections are never re-routed;
  - merged orders, positions and funds.
- **Order-rate governor:** one client-level budget across all accounts, now counting modifications and cancellations. It is capped at the exchanges' 10 orders/s unless `oms.registered_algo` is set.
- **Self-trade guard:** the OMS blocks orders that would trade against our own resting orders.
- **Engine heartbeat:** a hook for strategies, used by the market maker to pull stale quotes.
- **Tests:** 94 in total.

## 2.3.0 (September 2026): documented strategies

- **Ten research sleeves from books, papers and forums** (off by default; sources and deviations in `docs/STRATEGY_CATALOGUE.md`):
  - `etf_rotation`: Antonacci dual momentum;
  - `turtle`: Donchian/Turtle Systems 1 and 2;
  - `index_mr`: Connors RSI(2) and Double 7s;
  - `supertrend`;
  - `calendar_effects`: turn of the month, pre-holiday, Halloween filter;
  - `high52`, `low_vol` and `resid_momentum`;
  - `compression_breakout`: NR7, inside day, NR4+ID, narrow CPR;
  - `gap_fade`.
- **Templates** `RebalanceSleeve` and `DailySignalSleeve`: a new documented rule takes about 20 lines.
- **New indicators:** `supertrend()` and `cpr_width()`.
- **Parallel planner:** a sleeve's warm-up and run-off grow with its `max_hold`, so long-holding sleeves stay exact in parallel research backtests. Tested: identical trades and equity with 1 and 3 workers.
- **Tests:** 84 in total.

## 2.2.0 (September 2026): statistical strategies and research tools

Changes from the four "first principles" documents. Each idea was checked against the maths and simulations; details and reasons are in `docs/STRATEGY_RESEARCH.md`.

- **New research sleeves** (off by default):
  - `resid_reversion`: Avellaneda-Lee residual reversion on eigen-portfolio factors counted by Marchenko-Pastur, with a Dickey-Fuller-gated OU half-life and s-scores. This replaces "OU on price", which admits 24% of pure random walks as trades.
  - `skew_premium`: lottery/skewness premium from idiosyncratic skew plus MAX, rebalanced weekly with hysteresis; shorts through futures only, off by default.
- **Market-fragility overlay** (`fragility:`): the absorption-ratio shift, with MST length (the H0 persistence of the proposed TDA) reported alongside. The default is `report`; `halve` and `block` act on new swing entries.
- **Variance-ratio gates:** `orb: trend_filter` and `vwap_fade: reversion_filter`, both off by default. They replace the per-morning multifractal switch, which is estimation noise on 135 bars and crashed in the proposed code.
- **Research commands:**
  - `nsealgo anatomy`: bar-component distributions, with Edgeworth estimates marked invalid where the expansion breaks;
  - `nsealgo pairs-scan`: training/holdout cointegration, with the count expected by chance and `--any-sector`;
  - `nsealgo leadlag`: shuffle-corrected transfer entropy with a Bonferroni significance level;
  - `nsealgo fractal`: variance ratios, DFA and MF-DFA, and how noisy a single morning is.
- **Paper exchange fix:** daily replays reveal every symbol's bar at 15:10, so universe screens get closing fills.
- **Tests:** 76 in total, including simulations of the rejected rules' false-positive rates and parallel == continuous runs with the research sleeves.

## 2.1.0 (September 2026): Windows 11 as the reference platform

- **Windows integration** (`nsealgo.system`):
  - keep-awake during sessions and backtests;
  - EcoQoS power-throttling opt-out;
  - below-normal backtest workers and an above-normal live engine;
  - robust run-directory clean-up;
  - UTF-8 console output.
- **Pools:** one worker pool reused across regime fits, backtest jobs and walk-forward variants (spawn start-up paid once). Workers ignore Ctrl+C and the parent shuts the pool down cleanly. Capped at 61 workers on Windows.
- **Speed:** exact fast IST localisation plus sorted-slice splitting make bulk minute loads about 4× faster (613 ms → 155 ms for 200 symbols per session). The regime forward filter runs inside the parallel fit jobs.
- **Encodings:** UTF-8 for every text file the system reads or writes, and BOM-tolerant config, universe, event and holiday files.
- **Deployment and docs:**
  - `scripts/windows/setup.ps1`, `scripts/windows/morning.ps1` and `deploy/windows/register-tasks.ps1` (Task Scheduler, wake-to-run);
  - `nsealgo doctor` gains Windows notes and a clock-drift check;
  - `docs/WINDOWS.md` is the Windows runbook, and `docs/REVIEW_RESPONSE.md` gives verdicts on the Windows tuning spec.
- **Dependencies:** `tzdata` is declared for Windows.

## 2.0.0 (September 2026)

### Multi-core
- New `nsealgo.parallel` package, covering:
  - hardware detection (physical cores, SMT, affinity, container quotas, RAM, BLAS);
  - process/thread pools (forkserver/spawn, single-threaded BLAS in workers, ordered results, failure reports);
  - a job planner (strategy sleeves × period blocks with warm-up and run-off), worker, and merger with a mark-to-market equity rebuild.
- Research backtests run across all cores and are verified identical to a continuous run.
- `--faithful` keeps the single-engine, live-identical path.
- Regime timeline: HMM fits run in parallel, then an incremental forward filter reproduces day-by-day updates exactly.
- Parallel walk-forward variants; threaded history downloads (`fetch-history --threads`) and session-start prefetch in live/forward.
- New commands: `nsealgo hardware`, `nsealgo bench`, plus `--workers` / `--split` / `--faithful` flags; `python -m nsealgo`.
- Compatibility names from the multi-core design note: `core/concurrency.py`, `run_parallel_backtest`, `parallel_perturbation`, `compute_ofi_vectorized`.

### Speed (single core)
- Incremental 5-minute bars and VWAP-band snapshots, incremental regime filter, exact vectorised VPIN, cross-sectional panels for momentum and pullback screens, bulk SQL loads, rolling minute cache, thread-local read-only SQLite connections.
- Daily backtest 3.1× and minute backtest 6.8× faster on one core, with the same trade counts.

### Safety and correctness
- MARKET and SL-M orders are sent as protected LIMIT / stop-LIMIT orders for every broker. Zerodha defaults to `market_protection=-1`, and Kotak no longer sends an unprotected value.
- Risk layer blocks delivery (CNC) sells beyond holdings: no overnight cash shorts.
- The OMS re-prices unfilled exit limits (exit chaser), so a converted stop can't leave a position unprotected.
- Daily backtests now see today's bar for every symbol a strategy snapshots. Previously, symbols the feed wasn't streaming were scored on the previous close.
- `CostModel.breakdown()` itemises every statutory charge, with exact tests.

### Project
- Tests split into `tests/unit` and `tests/integration`.
- `docs/REVIEW_RESPONSE.md` gives item-by-item verdicts on the code review and design note; `docs/PERFORMANCE.md` documents the multi-core design.
- Dependencies: `psutil`, `threadpoolctl`.
