"""Strategy base class with order helpers shared by all strategies.

Contract: strategies never talk to brokers. They read data and place orders only through `ctx`
(core.context.Context), keep JSON-serialisable state in ctx.state(name), and receive callbacks.
"""
from __future__ import annotations

from ..core.crowding import avoid_round_level
from ..core.indicators import round_tick
from ..core.models import OrderRequest, OrderType, Product, Side


class Strategy:
    name = "base"
    horizon = "intraday"             # intraday | swing
    style = "liquidity_taking"       # liquidity_taking | liquidity_providing (regime posture)
    probation = False                # probation strategies trade at half size
    prior = (0.45, 1.5)              # Bayesian prior (win rate, avg win / avg loss) for Kelly shrinkage
    enabled_by_default = True        # research strategies set False: they run only when config enables them
    default_params: dict = {}

    def __init__(self, params: dict | None = None):
        self.p = {**self.default_params, **(params or {})}
        self._subscribed: set[str] = set()

    # ---- hooks ---------------------------------------------------------------------------------
    def data_needs(self, ctx) -> dict:
        return {}

    def depth_focus(self, ctx) -> list[str]:
        """Instruments that deserve a deep order book now, best first (intraday strategies: their candidates)."""
        return list(self.live_keys(ctx)) if self.horizon == "intraday" else []

    def live_keys(self, ctx) -> list[str]:
        return []

    def timers(self) -> list[str]:
        return []

    def on_session_start(self, ctx) -> None:
        pass

    def on_quote(self, ctx, q) -> None:
        pass

    def on_bar(self, ctx, bar) -> None:
        pass

    def on_timer(self, ctx, hm: str) -> None:
        pass

    def on_session_end(self, ctx) -> None:
        pass

    def on_intraday_exit(self, ctx) -> None:
        for o in ctx.active_orders(self.name, intent="entry"):
            ctx.cancel(o.client_id)
        self.exit_all(ctx, "intraday time exit")

    def on_fill(self, ctx, fill, order) -> None:
        pos = ctx.position(self.name, fill.key)
        if order.req.intent == "entry" and pos is not None and order.req.meta.get("stop") is not None:
            self.protect(ctx, fill.key, order.req.meta["stop"])
        elif pos is None:
            self.stops(ctx).pop(fill.key, None)
            for o in ctx.active_orders(self.name, fill.key):
                if o.req.intent in ("stop", "exit"):
                    ctx.cancel(o.client_id)

    # ---- helpers -------------------------------------------------------------------------------
    def stops(self, ctx) -> dict:
        return ctx.state(self.name).setdefault("stops", {})

    def tick(self, ctx, key: str) -> float:
        return ctx.instrument(key).tick_size or 0.05

    def marketable_price(self, ctx, key: str, side: Side, aggress: float | None = None) -> float | None:
        a = float(self.p.get("aggress", 0.002) if aggress is None else aggress)
        q = ctx.quote(key)
        last = ctx.last(key)
        ref = ((q.ask if side == Side.BUY else q.bid) if q else None) or last
        if not ref:
            return None
        px = ref * (1 + a) if side == Side.BUY else ref * (1 - a)
        return round_tick(px, self.tick(ctx, key), "up" if side == Side.BUY else "down")

    def enter(self, ctx, key: str, side: Side, qty: int, *, product: Product, stop: float | None, entry_ref: float,
              order_type: OrderType = OrderType.LIMIT, price: float | None = None, trigger: float | None = None,
              at_close: bool = False, meta: dict | None = None):
        if qty <= 0 or not ctx.can_enter():
            return None
        if order_type == OrderType.LIMIT and price is None:
            price = self.marketable_price(ctx, key, side)
            if price is None:
                return None
        m = {"stop": stop, "risk_ps": abs(entry_ref - stop) if stop is not None else 0.0, **(meta or {})}
        return ctx.submit(OrderRequest(key=key, side=side, qty=int(qty), order_type=order_type, product=product,
                                       price=price, trigger=trigger, strategy=self.name, intent="entry",
                                       at_close=at_close, meta=m))

    def protect(self, ctx, key: str, stop: float):
        """(Re)place a stop-limit protecting the whole position; limit sits 1% beyond the trigger."""
        pos = ctx.position(self.name, key)
        if pos is None or not stop:
            return None
        self.cancel_protect(ctx, key)
        side = Side.SELL if pos.qty > 0 else Side.BUY
        t = self.tick(ctx, key)
        trig = round_tick(stop, t, "down" if side == Side.SELL else "up")
        lim = round_tick(stop * (0.99 if side == Side.SELL else 1.01), t, "down" if side == Side.SELL else "up")
        o = ctx.submit(OrderRequest(key=key, side=side, qty=abs(pos.qty), order_type=OrderType.SL,
                                    product=Product(pos.product), price=lim, trigger=trig, strategy=self.name,
                                    intent="stop", meta={"stop": stop}))
        if o is not None:
            self.stops(ctx)[key] = o.client_id
        return o

    def cancel_protect(self, ctx, key: str) -> None:
        cid = self.stops(ctx).pop(key, None)
        if cid:
            ctx.cancel(cid)

    def exit(self, ctx, key: str, reason: str = "", price: float | None = None, at_close: bool = False):
        for o in ctx.active_orders(self.name, key, intent="entry"):   # a half-filled entry must not add shares
            ctx.cancel(o.client_id)                                    # after (or while) the position is closed
        pos = ctx.position(self.name, key)
        if pos is None:
            return None
        side = Side.SELL if pos.qty > 0 else Side.BUY
        px = price if price is not None else self.marketable_price(ctx, key, side)
        if px is None:
            return None
        cid = self.stops(ctx).get(key)
        o = ctx.order(cid)
        if o is not None and o.is_active and o.filled_qty == 0 and o.req.qty == abs(pos.qty):
            if ctx.modify(cid, order_type=OrderType.LIMIT, price=px):   # convert the stop: one order, no double exit
                o.req.intent = "exit"
                o.req.meta["reason"] = reason
                self.stops(ctx).pop(key, None)
                return o
        self.cancel_protect(ctx, key)
        if ctx.active_orders(self.name, key, intent="exit"):
            return None
        return ctx.submit(OrderRequest(key=key, side=side, qty=abs(pos.qty), order_type=OrderType.LIMIT,
                                       product=Product(pos.product), price=px, strategy=self.name, intent="exit",
                                       at_close=at_close, meta={"reason": reason}))

    def exit_all(self, ctx, reason: str = "") -> None:
        for pos in list(ctx.positions(self.name)):
            self.exit(ctx, pos.key, reason)

    def round_level(self, ctx, key: str, price: float, direction: int) -> float:
        c = ctx.e.cfg.get("crowding", {})
        return avoid_round_level(price, self.tick(ctx, key), direction, tuple(c.get("round_levels", (100, 50, 10))),
                                 int(c.get("round_zone_ticks", 3)))
