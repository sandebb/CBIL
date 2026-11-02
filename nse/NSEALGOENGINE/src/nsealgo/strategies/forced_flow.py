"""8) Forced-flow harvesting around index events and the NSE closing auction (CAS).

- Inclusions: buy 20-40 sessions before the expected announcement, sell INTO the closing auction on
  the eve of the effective date (when passive funds must buy).
- Deletions: buy in the closing auction on the eve of the effective date at a discount to the
  15:00-15:15 VWAP reference, hold 5-20 sessions for the post-deletion rebound.
- Closing-auction imbalance fade (heavy-flow days only): if NSE's indicative auction price shows a
  large uninformed SELL imbalance, bid below the reference; exit next morning. Needs a data broker
  that exposes auction indicative data (wire it in BrokerAdapter.auction_snapshot); dormant otherwise.
"""
from __future__ import annotations

from datetime import date

from ..core.indicators import atr
from ..core.models import OrderType, Product, Side, eq
from .base import Strategy


class ForcedFlowHarvest(Strategy):
    name = "forced_flow"
    horizon = "swing"
    style = "liquidity_providing"
    prior = (0.55, 1.2)
    default_params = {"pre_window": [20, 40], "max_names": 4, "deletion_hold": 10, "deletion_discount": 0.005,
                      "cas_fade": True, "cas_min_discount": 0.01, "cas_universe": "fno", "cas_max": 3,
                      "disaster_atr": 3.0, "exit_band": 0.97, "eval_time": "15:10", "cas_time": "15:21",
                      "morning_time": "09:20", "protect_time": "09:16"}

    def _event_keys(self, ctx):
        return sorted({eq(r["symbol"]) for r in ctx.events.index_rows()})

    def data_needs(self, ctx):
        return {"daily": self._event_keys(ctx), "daily_days": 120}

    def live_keys(self, ctx):
        return self._event_keys(ctx)

    def timers(self):
        return [self.p["protect_time"], self.p["morning_time"], self.p["eval_time"], self.p["cas_time"]]

    def _atr(self, ctx, key) -> float:
        d = ctx.daily(key, with_today=True, tail=90)
        return float(atr(d, 14).iloc[-1]) if len(d) > 20 else 0.0

    def _ref(self, ctx, key) -> float | None:
        """Closing-auction reference proxy: VWAP of 15:00-15:15 (NSE's reference), else typical price."""
        bars = ctx.bars_df(key)
        if len(bars):
            w = bars.between_time("15:00", "15:14")
            if len(w) and w["volume"].sum() > 0:
                tp = (w["high"] + w["low"] + w["close"]) / 3
                return float((tp * w["volume"]).sum() / w["volume"].sum())
        tb = ctx.today_bar(key)
        if tb:
            return (tb["high"] + tb["low"] + tb["close"]) / 3
        return ctx.last(key)

    def on_timer(self, ctx, t):
        book = ctx.state(self.name).setdefault("pos", {})
        for key in list(book):
            if not ctx.position(self.name, key) and not ctx.active_orders(self.name, key) and book[key].get("filled"):
                book.pop(key)
        if t == self.p["protect_time"]:
            for pos in ctx.positions(self.name):
                if book.get(pos.key, {}).get("stop"):
                    self.protect(ctx, pos.key, book[pos.key]["stop"])
        elif t == self.p["morning_time"]:
            for pos in list(ctx.positions(self.name)):
                info = book.get(pos.key, {})
                if info.get("kind") == "cas" and info.get("entry_date") != ctx.today.isoformat():
                    self.exit(ctx, pos.key, "auction-fade next-morning exit")
        elif t == self.p["eval_time"]:
            self._inclusions(ctx, book)
            for pos in list(ctx.positions(self.name)):
                info = book.get(pos.key, {})
                if info.get("kind") == "deletion" and ctx.calendar.sessions_between(
                        date.fromisoformat(info["entry_date"]), ctx.today) >= int(self.p["deletion_hold"]):
                    self.exit(ctx, pos.key, "deletion rebound hold complete")
        elif t == self.p["cas_time"]:
            self._auction(ctx, book)

    def _inclusions(self, ctx, book):
        allowed = ctx.posture(self.name)[0]
        held = sum(1 for v in book.values() if v.get("kind") == "inclusion")
        lo, hi = self.p["pre_window"]
        for r in ctx.events.index_rows("ADD"):
            key = eq(r["symbol"])
            if r["status"] == "rejected":
                if ctx.position(self.name, key):
                    self.exit(ctx, key, "inclusion rejected")
                continue
            if (not allowed or r["announce"] is None or held >= int(self.p["max_names"])
                    or ctx.position(self.name, key) or ctx.active_orders(self.name, key) or key in book):
                continue
            if not (lo <= ctx.calendar.sessions_between(ctx.today, r["announce"]) <= hi):
                continue
            last, a = ctx.last(key), self._atr(ctx, key)
            if not last or a <= 0:
                continue
            stop = last - self.p["disaster_atr"] * a
            if self.enter(ctx, key, Side.BUY, ctx.size(self.name, key, last, stop), product=Product.CNC, stop=stop,
                          entry_ref=last, meta={"kind": "inclusion"}):
                book[key] = {"kind": "inclusion", "eve": r["eve"].isoformat(), "entry_date": ctx.today.isoformat(),
                             "stop": stop}
                held += 1

    def _auction(self, ctx, book):
        today = ctx.today.isoformat()
        for key, info in list(book.items()):
            if info.get("kind") == "inclusion" and info.get("eve") == today and ctx.position(self.name, key):
                ref = self._ref(ctx, key) or ctx.last(key)
                self.exit(ctx, key, "sell into inclusion closing auction", price=ref * self.p["exit_band"], at_close=True)
        allowed = ctx.posture(self.name)[0]
        if not allowed:
            return
        for r in ctx.events.index_rows("DELETE"):
            key = eq(r["symbol"])
            if r["eve"] != ctx.today or r["status"] == "rejected" or ctx.position(self.name, key) or key in book:
                continue
            ref, a = self._ref(ctx, key), self._atr(ctx, key)
            if not ref or a <= 0:
                continue
            limit = ref * (1 - self.p["deletion_discount"])
            stop = limit - self.p["disaster_atr"] * a
            if self.enter(ctx, key, Side.BUY, ctx.size(self.name, key, limit, stop), product=Product.CNC, stop=stop,
                          entry_ref=limit, order_type=OrderType.LIMIT, price=limit, at_close=True,
                          meta={"kind": "deletion"}):
                book[key] = {"kind": "deletion", "entry_date": today, "stop": stop}
        if not self.p["cas_fade"] or not ctx.events.is_heavy_flow_day(ctx.today):
            return
        n = 0
        for key in ctx.universe(self.p["cas_universe"]):
            snap = ctx.auction(key)
            if snap is None or key in book or ctx.position(self.name, key):
                continue
            ref = self._ref(ctx, key)
            if (snap.imbalance_side == "SELL" and ref and snap.indicative_price <= ref * (1 - self.p["cas_min_discount"])):
                a = self._atr(ctx, key)
                stop = snap.indicative_price - self.p["disaster_atr"] * a
                if self.enter(ctx, key, Side.BUY, ctx.size(self.name, key, snap.indicative_price, stop),
                              product=Product.CNC, stop=stop, entry_ref=snap.indicative_price,
                              order_type=OrderType.LIMIT, price=snap.indicative_price, at_close=True,
                              meta={"kind": "cas"}):
                    book[key] = {"kind": "cas", "entry_date": today, "stop": stop}
                    n += 1
                    if n >= int(self.p["cas_max"]):
                        break

    def on_fill(self, ctx, fill, order):
        book = ctx.state(self.name).setdefault("pos", {})
        if order.req.intent == "entry" and fill.key in book:
            book[fill.key]["filled"] = True
        super().on_fill(ctx, fill, order)
        if ctx.position(self.name, fill.key) is None and order.req.intent != "entry":
            book.pop(fill.key, None)
