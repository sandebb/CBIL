> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Strategy developer guide

## Anatomy

A strategy is a subclass of `strategies/base.Strategy`, registered in `strategies/__init__.REGISTRY`, and configured under `strategies:` in `config/system.yaml`.

| Member | Purpose |
|---|---|
| `name`, `horizon` (`intraday` / `swing`), `style` (`liquidity_taking` / `liquidity_providing`) | Identity; horizon picks products and charts; style sets the regime posture |
| `enabled_by_default`, `default_params` | Research strategies set `False`; `params` merge config over defaults |
| `data_needs(ctx)` | History to load before the session, e.g. `{"daily": keys, "daily_days": 250}` |
| `live_keys(ctx)` | Instruments to stream today |
| `depth_focus(ctx)` | Instruments that deserve deep books (default: intraday strategies' live keys) |
| `timers()` | `"HH:MM"` times for `on_timer` |
| `on_session_start`, `on_quote(q)`, `on_bar(bar)`, `on_timer(hm)`, `on_intraday_exit`, `on_session_end`, `on_fill` | Event hooks |
| `enter(ctx, key, side, qty, *, product, stop, entry_ref, …)` | Submit an entry (sized by you, checked by risk) |
| `exit(ctx, key, reason=…, price=…, at_close=…)`, `exit_all`, `protect(stop)`, `cancel_protect` | Exits and protective stops |

The **Context** (`ctx`) is the strategy's only window on the world:
- **Data:** `daily`, `panel`, `bars_df`, `quote`, `last`, `vwap`, `today_bar`, `minute_history`, `universe`, `sector`.
- **Microstructure:** `book` (deep-book signals or None), `ofi`, `has_depth`, `is_cas`, `auction`.
- **Risk:** `posture`, `regime`, `fragility`, `size`, `risk_amount`, `can_enter`.
- **Orders:** `submit`, `cancel`, `modify`, `order`, `active_orders`.
- **State:** `state(name)` (a persistent dict), `position`, `positions`, `stats`, `trades`, `log`.

Never read files, clocks or brokers directly; that is what keeps backtests and live trading identical.

## A minimal intraday strategy

```python
from ..core.models import Product, Side
from .base import Strategy


class FirstHourBreakout(Strategy):
    name, horizon, enabled_by_default = "first_hour", "intraday", False
    default_params = {"universe": "nifty50", "stop_pct": 0.006}

    def live_keys(self, ctx):
        return ctx.universe(self.p["universe"])

    def timers(self):
        return ["10:15"]

    def on_timer(self, ctx, hm):
        for key in self.live_keys(ctx):
            bars = ctx.bars_df(key)
            if len(bars) < 60 or ctx.position(self.name, key):
                continue
            high = bars["high"].iloc[:60].max()
            q = ctx.quote(key)
            ok, _, _ = ctx.posture(self.name)
            if ok and q and q.ltp > high:
                stop = q.ltp * (1 - self.p["stop_pct"])
                qty = ctx.size(self.name, key, q.ltp, stop)
                if qty > 0:
                    self.enter(ctx, key, Side.BUY, qty, product=Product.MIS, stop=stop, entry_ref=q.ltp)
```

Register it in `REGISTRY` and add `first_hour: {enabled: false}` to `config/system.yaml`.

## From idea to live

1. **Unit test** the logic on synthetic data (`data/synthetic.py`); see `tests/unit/test_documented_strategies.py`.
2. **Backtest:** `nsealgo backtest --strategies first_hour` (research mode is identical to faithful mode, and faster).
3. **Robustness:** `nsealgo walkforward --strategies first_hour` (±20% parameters).
4. **Forward test:** enable it, run `nsealgo forwardtest`, and watch it in the desk's strategy window.
5. **Promotion gates:** `nsealgo report` must pass the `gates` before it may go live.
6. **Document:** add a row to the [Strategy catalogue](../05-reference/STRATEGY_CATALOGUE.md), with its source and evidence.

## Rules that keep strategies honest

- **No look-ahead:** use `with_today` only for data that exists at decision time.
- **Size through the context:** always use `ctx.size`, so risk limits and posture apply.
- **Exits:** give every entry a stop (`stop=`), and close MIS positions in `on_intraday_exit` (the base class does this).
- **No overnight shorts:** in the cash market, short selling is intraday-only (MIS).
- **Cost gates:** for fast strategies (scalper, market maker), compare the expected move with the round-trip cost first.
