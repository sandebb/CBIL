> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Scalping, market making and multiple brokers

Two quote-driven strategies for **paper testing**, and a multi-broker execution layer. Read the limits first:
they decide whether these can ever make money on a retail NSE account.

## Order-rate limits and multiple brokers

The exchanges' threshold is 10 orders per second per exchange segment, and each broker enforces it on the
orders it carries. Above it, an algo must be registered with the exchange through that broker.

nsealgo gives every broker account its own budget:
- **Per-account setting:** `accounts.<name>.orders_per_second`, default 8.
- **Cap:** at most 10, unless that account's algo is registered (`registered_algo: true`).
- **What counts:** new orders, modifications and cancellations.
- **Combined throughput:** the sum of the accounts' budgets. Two unregistered accounts at 8/s give 16/s.

The exchanges can revise the threshold with notice, so keep your broker's written confirmation of how it
applies to you.

## Multiple brokers: the stock allocation table

`broker.execution: multi` puts several accounts behind one interface (`brokers/multi.py`).
- **Each stock trades at exactly one account:** the first time a stock is traded, it is allocated to the account with the most spare capacity (stocks per orders/s budget) and stays there. Every strategy's orders for it go to that account. The table lives in `.cache/multi_state.json` and survives restarts.
- **Pins:** keep a stock at a chosen account with `routing.pins` in config, or `nsealgo accounts --pin RELIANCE=main`.
- **Positions never move:** exits, stops and additions go to the account that holds the position. `nsealgo accounts --rebalance` re-spreads only stocks with no open position or order that aren't pinned.
- **Outages:**
  * If an account is unreachable, a new entry in a stock with no open position or order there, and not pinned, is moved to another account.
  * Before moving it, the router asks the first account, by the order's tag, whether it received the order, so nothing is ever sent twice.
  * A rejection (margin, price band) is the broker's answer and is never re-routed.
  * Stocks with open positions wait for their broker.
- **One view:** orders, positions and funds of all accounts are merged for the OMS, risk checks and reconciliation.

```yaml
broker:
  execution: multi
accounts:
  main:   {broker: zerodha, orders_per_second: 8}      # per-broker budget (max 10 unless registered_algo: true)
  second: {broker: fyers,   orders_per_second: 8}
  third:  {broker: shoonya, orders_per_second: 8}      # adapter settings: broker.brokers.<name>, or settings:
routing:
  pins: {RELIANCE: main, HDFCBANK: second}             # optional: fixed homes for chosen stocks
  reassign_idle: true                                  # move idle stocks off an unreachable broker
  retry_seconds: 60
```

```
nsealgo accounts                        # the table: stocks per account, pins, open positions
nsealgo accounts --pin INFY=third       # fix a stock's account (refused while it has an open position)
nsealgo accounts --rebalance            # re-spread idle, unpinned stocks by capacity
```

Each account needs its own static IP registration with its broker, daily login and algo tagging.

## `scalper`: order-flow imbalance

Order-flow imbalance (Cont, Kukanov & Stoikov 2014) and queue imbalance predict the next few ticks.
- **Entry:** when the OFI summed over the last `window` quotes is `ofi_z` standard deviations from zero and the best-bid/ask size imbalance agrees, it buys or sells with a marketable limit.
- **Exits:** `target_ticks`, `stop_ticks` and a `hold_seconds` time exit.
- **Cost gate:** a trade is skipped unless the target in rupees is at least `cost_multiple` × the round-trip costs, which include STT, exchange fees, GST, stamp duty and brokerage.

The gate compares rupees, not ticks. On a ₹4,000 stock with a ₹0.10 tick, 8 ticks (₹0.80) can't pay about ₹1.50 per share in costs; on a ₹5,600 stock with a ₹0.50 tick, 8 ticks (₹4.00) can. The replay tests check both cases.

## `market_maker`: inventory-skewed quoting

Passive two-sided quotes around the microprice (Avellaneda & Stoikov 2008).
- **Reservation price:** `fair − q·γ·σ²_h`, where q is inventory in quote units, so a long book quotes lower to shed inventory.
- **Half-spread:** `max(min_edge_ticks·tick, cost_multiple·cost/2, γ·σ²_h/2)`, so the captured spread always pays round-trip costs. When the market's own spread is too narrow for that, it doesn't quote at all.

Safeguards:
- **Inventory:** inventory is capped (`max_inventory_value`), quoting stops at `stop` (14:45), and inventory is flattened at `flatten` (15:00).
- **Losses:** a daily `max_loss` halts a symbol.
- **Stale data:** quotes are pulled if data goes stale (`stale_seconds`, via the engine heartbeat).
- **No layering or spoofing:** one resting quote per side, a minimum quote life, re-quotes only when the price moves by `requote_ticks`, and a per-minute message budget.
- **No self-trades:** our bid is always below our ask, and the OMS blocks any order that would trade against another of our own resting orders, whichever strategy placed it.

## What to expect

- **Costs:** intraday STT is 0.025% of the sell value, and one tick on a ₹1,000 stock is 0.005% of the price. On liquid large caps both strategies will mostly refuse to trade. Where spreads are wide enough to pay costs, stocks are less liquid and inventory risk is higher.
- **Speed:** free broker feeds are about one-second snapshots and orders travel over the internet. Professional market makers react faster, so passive fills tend to come just before the price moves against you.
- **Paper realism:** the paper exchange fills a resting quote only when the market trades *through* it, so paper results aren't flattered. Minute-bar backtests can't evaluate either strategy; use the forward test, which runs on live quotes, or `nsealgo replay` of a recorded day (`forwardtest --record` saves one).
