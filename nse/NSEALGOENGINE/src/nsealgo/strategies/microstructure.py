"""Quote-driven microstructure strategies for paper testing: a scalper and a market maker.

Read before enabling:
- Both need quote-level data: a forward test on live feeds, or `nsealgo replay` of a recorded day.
  Minute-bar backtests cannot evaluate them.
- Costs dominate. On a Rs 1,000 stock one tick (Rs 0.05) is 0.005% of the price, while intraday STT
  alone is 0.025% of the sell value. Both strategies refuse trades whose edge doesn't clear round-trip
  costs, so on liquid large caps they will often not trade at all. That is the honest answer, not a bug.
- Free broker feeds are roughly 1-second snapshots, and orders travel over the internet. Professional
  market makers see and react faster, so passive fills tend to arrive just before the price moves
  against you (adverse selection). The paper exchange only fills a resting quote when the market trades
  THROUGH it, so paper results aren't flattered.
- Compliance built in:
  * one resting quote per side, a minimum quote life and a message budget (no layering, no flickering
    quotes);
  * our bid always below our ask, and the OMS blocks any order that would trade against another of our
    own resting orders (no self-trades);
  * every new, modified or cancelled order counts against the client-level order-rate governor.

scalper: order-flow imbalance (Cont, Kukanov & Stoikov 2014) and queue imbalance predict the next few
  ticks.
  * entry: when the OFI summed over the last `window` quotes is `ofi_z` standard deviations from zero and
    the book imbalance agrees, take the move with a marketable limit;
  * exits: target `target_ticks`, stop `stop_ticks`, time exit after `hold_seconds`;
  * cost gate: the target must be at least `cost_multiple` x the round-trip costs, otherwise no trade.
market_maker: two-sided passive quotes around the microprice, skewed by inventory (Avellaneda & Stoikov
  2008).
  * reservation price r = fair - q * gamma * var_h, where q is inventory in quote units and var_h is the
    mid-price variance over `horizon_seconds`;
  * half-spread = max(min_edge, cost floor, gamma * var_h / 2);
  * inventory is capped, quoting stops at `stop`, inventory is flattened at `flatten`, a daily loss limit
    halts a symbol, and quotes are pulled when data goes stale.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import math
from collections import deque
from datetime import timedelta

import numpy as np

from ..core.bars import BarBuilder
from ..core.clock import hm
from ..core.models import OrderRequest, OrderType, Product, Side
from .base import Strategy


def round_trip_cost(ctx, key: str, px: float, qty: int) -> float:
    """Rupees per share to buy and sell qty at px intraday: brokerage and every statutory charge."""
    c, q = ctx.e.costs, max(int(qty), 1)
    return (c.breakdown(product="MIS", side="BUY", price=px, qty=q)["total"]
            + c.breakdown(product="MIS", side="SELL", price=px, qty=q)["total"]) / q


class OrderFlowScalper(Strategy):
    name = "scalper"
    horizon = "intraday"
    style = "liquidity_taking"
    prior = (0.52, 1.0)
    enabled_by_default = False
    default_params = {"symbols": ["NSE:SBIN", "NSE:ITC", "NSE:TATASTEEL"], "window": 12, "history": 300, "ofi_z": 2.5, "use_book": True,
                      "max_absorb": 3.0,
                      "imbalance": 0.3, "max_spread_ticks": 2, "target_ticks": 8, "stop_ticks": 5,
                      "cost_multiple": 1.5, "hold_seconds": 180, "cooldown_seconds": 60, "max_trades": 20,
                      "start": "09:25", "stop": "14:45"}

    def _keys(self, ctx):
        return ctx.universe(list(self.p["symbols"]))

    def data_needs(self, ctx):
        return {"daily": self._keys(ctx), "daily_days": 30}

    def live_keys(self, ctx):
        return self._keys(ctx)

    def on_session_start(self, ctx):
        self._prev, self._ofi, self._last, self._open = {}, {}, {}, {}
        st = ctx.state(self.name)
        st["trades"], st["cost_blocked"] = 0, 0

    def on_quote(self, ctx, q):
        if not hasattr(self, "_prev"):
            self.on_session_start(ctx)
        if not (q.bid and q.ask and q.bid_qty and q.ask_qty) or q.ask <= q.bid:
            return
        prev, self._prev[q.key] = self._prev.get(q.key), q
        hist = self._ofi.setdefault(q.key, deque(maxlen=int(self.p["history"])))
        if prev is not None:
            hist.append(BarBuilder.ofi_increment(prev, q))
        pos = ctx.position(self.name, q.key)
        if pos is not None:
            self._manage(ctx, q, pos)
            return
        if ctx.active_orders(self.name, q.key) or not (hm(self.p["start"]) <= q.ts.time() <= hm(self.p["stop"])):
            return
        st, tick, w = ctx.state(self.name), self.tick(ctx, q.key), int(self.p["window"])
        last = self._last.get(q.key)
        if st.get("trades", 0) >= int(self.p["max_trades"]) or (
                last is not None and (q.ts - last).total_seconds() < float(self.p["cooldown_seconds"])):
            return
        if q.ask - q.bid > int(self.p["max_spread_ticks"]) * tick + 1e-9 or len(hist) < max(3 * w, 30):
            return
        arr = np.asarray(hist, float)
        sd = float(arr.std())
        if sd <= 0 or not ctx.posture(self.name)[0]:
            return
        z = float(arr[-w:].sum()) / (sd * math.sqrt(w))
        imb = (q.bid_qty - q.ask_qty) / (q.bid_qty + q.ask_qty)
        b = ctx.book(q.key) if self.p.get("use_book", True) else None
        if b is not None:
            z, imb = b.ofi_z, b.imbalance                              # multi-level flow, depth-weighted imbalance
            st["book_seen"] = st.get("book_seen", 0) + 1
        if z >= float(self.p["ofi_z"]) and imb >= float(self.p["imbalance"]):
            side, px = Side.BUY, float(q.ask)
        elif z <= -float(self.p["ofi_z"]) and imb <= -float(self.p["imbalance"]):
            side, px = Side.SELL, float(q.bid)
        else:
            return
        if b is not None and (b.absorb_ask if side == Side.BUY else b.absorb_bid) >= float(self.p["max_absorb"]):
            st["absorb_blocked"] = st.get("absorb_blocked", 0) + 1     # hidden size where we'd trade into it
            return
        sgn = 1 if side == Side.BUY else -1
        stop = px - sgn * int(self.p["stop_ticks"]) * tick
        qty = ctx.size(self.name, q.key, px, stop)
        if qty <= 0:
            return
        dist = int(self.p["target_ticks"]) * tick
        if dist < float(self.p["cost_multiple"]) * round_trip_cost(ctx, q.key, px, qty):
            st["cost_blocked"] = st.get("cost_blocked", 0) + 1         # the move can't pay for the trade
            return
        if self.enter(ctx, q.key, side, qty, product=Product.MIS, stop=stop, entry_ref=px, price=px,
                      meta={"ofi_z": round(z, 2), "imbalance": round(imb, 2), "book": b is not None}):
            st["trades"] = st.get("trades", 0) + 1
            self._last[q.key] = q.ts
            self._open[q.key] = {"target": px + sgn * dist,
                                 "deadline": q.ts + timedelta(seconds=float(self.p["hold_seconds"]))}

    def _manage(self, ctx, q, pos) -> None:
        info = self._open.get(q.key)
        if not info or ctx.active_orders(self.name, q.key, intent="exit"):
            return
        if (pos.qty > 0 and q.bid >= info["target"]) or (pos.qty < 0 and q.ask <= info["target"]):
            self.exit(ctx, q.key, "target")
        elif q.ts >= info["deadline"]:
            self.exit(ctx, q.key, "time exit")
        else:
            return
        self._open.pop(q.key, None)


class InventoryMarketMaker(Strategy):
    name = "market_maker"
    horizon = "intraday"
    style = "liquidity_providing"
    prior = (0.55, 1.0)
    enabled_by_default = False
    default_params = {"symbols": ["NSE:SBIN"], "quote_value": 50000.0, "max_inventory_value": 200000.0,
                      "gamma": 0.5, "horizon_seconds": 60.0, "vol_window": 120, "min_edge_ticks": 1,
                      "cost_multiple": 1.2, "use_book": True, "absorb_threshold": 3.0,
                      "absorb_skew_ticks": 1, "requote_ticks": 1, "min_quote_life": 3.0, "max_msgs_per_min": 20,
                      "stale_seconds": 10.0, "max_loss": 3000.0, "start": "09:20", "stop": "14:45",
                      "flatten": "15:00"}

    def _keys(self, ctx):
        return ctx.universe(list(self.p["symbols"]))

    def data_needs(self, ctx):
        return {"daily": self._keys(ctx), "daily_days": 30}

    def live_keys(self, ctx):
        return self._keys(ctx)

    def timers(self):
        return [self.p["stop"], self.p["flatten"]]

    def on_session_start(self, ctx):
        self._mid, self._msgs, self._cash, self._halted, self._seen = {}, deque(), {}, set(), {}
        ctx.state(self.name)["max_inventory"] = 0

    def quotes(self, fair: float, var_h: float, inv_units: float, tick: float, cost_ps: float):
        """Avellaneda-Stoikov reservation price +/- half-spread, with a floor that pays round-trip costs."""
        g = float(self.p["gamma"])
        r = fair - inv_units * g * var_h
        h = max(int(self.p["min_edge_ticks"]) * tick, float(self.p["cost_multiple"]) * cost_ps / 2, g * var_h / 2)
        return r - h, r + h

    def _mm_orders(self, ctx, key=None):
        return [o for o in ctx.active_orders(self.name, key) if o.req.meta.get("mm")]

    def _pull(self, ctx, key=None) -> None:
        for o in self._mm_orders(ctx, key):
            ctx.cancel(o.client_id)

    def on_timer(self, ctx, t):
        if not hasattr(self, "_mid"):
            self.on_session_start(ctx)
        if t == self.p["stop"]:
            self._pull(ctx)
        elif t == self.p["flatten"]:
            self._pull(ctx)
            for p in list(ctx.positions(self.name)):
                self.exit(ctx, p.key, "flatten inventory")

    def on_heartbeat(self, ctx, now) -> None:
        for key, ts in list(getattr(self, "_seen", {}).items()):
            if (now - ts).total_seconds() > float(self.p["stale_seconds"]) and self._mm_orders(ctx, key):
                self._pull(ctx, key)                                # never leave quotes out on stale data

    def on_fill(self, ctx, fill, order):
        sgn = 1 if fill.side == Side.BUY else -1
        self._cash[fill.key] = self._cash.get(fill.key, 0.0) - sgn * fill.qty * fill.price - fill.costs
        pos = ctx.position(self.name, fill.key)
        st = ctx.state(self.name)
        st["max_inventory"] = max(int(st.get("max_inventory", 0)), abs(pos.qty) if pos else 0)
        super().on_fill(ctx, fill, order)

    def _budget(self, now) -> bool:
        while self._msgs and (now - self._msgs[0]).total_seconds() > 60:
            self._msgs.popleft()
        return len(self._msgs) < int(self.p["max_msgs_per_min"])

    def on_quote(self, ctx, q):
        if not hasattr(self, "_mid"):
            self.on_session_start(ctx)
        key, now = q.key, q.ts
        if not (q.bid and q.ask) or q.ask <= q.bid or key in self._halted:
            return
        self._seen[key] = now
        if not (hm(self.p["start"]) <= now.time() < hm(self.p["stop"])):
            return
        mids = self._mid.setdefault(key, deque(maxlen=int(self.p["vol_window"])))
        mid = (q.bid + q.ask) / 2
        mids.append((now, mid))
        if len(mids) < 20:
            return
        pos = ctx.position(self.name, key)
        inv = pos.qty if pos else 0
        if self._cash.get(key, 0.0) + inv * mid < -float(self.p["max_loss"]):
            self._halted.add(key)                                       # daily loss limit: stop this symbol
            self._pull(ctx, key)
            if inv:
                self.exit(ctx, key, "loss limit")
            return
        if not ctx.posture(self.name)[0]:
            self._pull(ctx, key)
            return
        tick = self.tick(ctx, key)
        unit = max(1, int(float(self.p["quote_value"]) / mid))
        max_units = max(1, int(float(self.p["max_inventory_value"]) / (unit * mid)))
        ts = np.array([(t - mids[0][0]).total_seconds() for t, _ in mids])
        m = np.array([x for _, x in mids])
        step = max(float(np.diff(ts).mean()), 0.5)
        var_h = float(np.diff(m).var()) * float(self.p["horizon_seconds"]) / step
        fair = (q.bid * q.ask_qty + q.ask * q.bid_qty) / (q.bid_qty + q.ask_qty) if (q.bid_qty and q.ask_qty) else mid
        b = ctx.book(key) if self.p.get("use_book", True) else None
        if b is not None:                                               # lean away from hidden size (icebergs)
            thr, skew = float(self.p["absorb_threshold"]), float(self.p["absorb_skew_ticks"]) * tick
            fair += (skew if b.absorb_bid >= thr else 0.0) - (skew if b.absorb_ask >= thr else 0.0)
        cost_ps = round_trip_cost(ctx, key, mid, unit)
        b, a = self.quotes(fair, var_h, inv / unit, tick, cost_ps)
        bid_px = min(math.floor(b / tick) * tick, q.ask - tick)               # passive only: never marketable
        ask_px = max(math.ceil(a / tick) * tick, q.bid + tick)
        if ask_px - bid_px < float(self.p["cost_multiple"]) * cost_ps or bid_px <= 0:
            self._pull(ctx, key)                                        # the spread we'd capture can't pay costs
            return
        want = {Side.BUY: round(bid_px, 2) if inv / unit < max_units else None,
                Side.SELL: round(ask_px, 2) if inv / unit > -max_units else None}
        live = {o.req.side: o for o in self._mm_orders(ctx, key)}
        for side, px in want.items():
            o = live.get(side)
            if px is None:
                if o is not None:
                    ctx.cancel(o.client_id)                             # inventory cap: stop adding
                continue
            if o is None:
                if not self._budget(now):
                    continue
                reduce = (side == Side.SELL and inv > 0) or (side == Side.BUY and inv < 0)
                qty = min(unit, abs(inv)) if reduce else unit
                req = OrderRequest(key=key, side=side, qty=qty, order_type=OrderType.LIMIT, product=Product.MIS,
                                   price=px, strategy=self.name, intent="exit" if reduce else "entry",
                                   meta={"mm": True})
                if ctx.submit(req) is not None:
                    self._msgs.append(now)
            elif abs((o.req.price or 0) - px) >= int(self.p["requote_ticks"]) * tick - 1e-9 \
                    and (now - (o.updated or o.created)).total_seconds() >= float(self.p["min_quote_life"]) \
                    and self._budget(now):
                if ctx.e.oms.modify(o.client_id, price=px):
                    self._msgs.append(now)
