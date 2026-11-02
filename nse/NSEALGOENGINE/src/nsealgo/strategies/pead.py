"""5) Post-earnings-announcement drift on gap-and-hold results days (swing, long-only).

Day-0 (first session able to react to results): return >= 2 x ATR%, volume >= 3x its 50-day mean,
close in the top 30% of the day's range. Then a buy-stop above the day-0 high for up to 3 sessions,
stop at the day-0 low, exit on a close below the 20-EMA or after 60 sessions. Gap-aware sizing
(the worst 1% overnight gap may cost at most 2x planned risk) is enforced by the risk manager.
"""
from __future__ import annotations

from datetime import date

from ..core.indicators import atr, ema
from ..core.models import OrderType, Product, Side, eq
from .base import Strategy


class EarningsGapDrift(Strategy):
    name = "pead"
    horizon = "swing"
    style = "liquidity_taking"
    prior = (0.45, 2.0)
    default_params = {"universe": "nifty500", "atr_mult": 2.0, "vol_mult": 3.0, "close_top": 0.3,
                      "trigger_sessions": 3, "exit_ema": 20, "max_hold": 60, "stop_ticks": 2, "entry_slip": 0.01,
                      "scan_time": "15:40", "place_time": "09:16", "exit_time": "15:10"}

    def data_needs(self, ctx):
        return {"daily": ctx.universe(self.p["universe"]), "daily_days": 120}

    def live_keys(self, ctx):
        return list(ctx.state(self.name).get("watch", {}))

    def timers(self):
        return [self.p["place_time"], self.p["exit_time"], self.p["scan_time"]]

    def on_timer(self, ctx, t):
        st = ctx.state(self.name)
        watch, book = st.setdefault("watch", {}), st.setdefault("pos", {})
        if t == self.p["scan_time"]:
            self._scan(ctx, watch)
        elif t == self.p["place_time"]:
            for pos in ctx.positions(self.name):
                if pos.key in book:
                    self.protect(ctx, pos.key, book[pos.key]["stop"])
            self._place(ctx, watch)
        elif t == self.p["exit_time"]:
            for pos in list(ctx.positions(self.name)):
                d, info = ctx.daily(pos.key, with_today=True, tail=120), book.get(pos.key)
                if not info or len(d) < 25:
                    continue
                held = ctx.calendar.sessions_between(date.fromisoformat(info["entry_date"]), ctx.today)
                if float(d["close"].iloc[-1]) < float(ema(d["close"], int(self.p["exit_ema"])).iloc[-1]):
                    self.exit(ctx, pos.key, "close below 20-EMA")
                elif held >= int(self.p["max_hold"]):
                    self.exit(ctx, pos.key, "max holding period")

    def _scan(self, ctx, watch):
        universe = set(ctx.universe(self.p["universe"]))
        keys = [eq(s) for s in ctx.events.day0_symbols(ctx.today) if eq(s) in universe]
        ctx.snapshot(keys)
        for key in keys:
            d = ctx.daily(key, with_today=True, tail=120)
            if len(d) < 60:
                continue
            t, prev = d.iloc[-1], d.iloc[-2]
            a = float(atr(d.iloc[:-1], 14).iloc[-1])
            rng = float(t["high"] - t["low"])
            if (t["close"] / prev["close"] - 1) < self.p["atr_mult"] * a / prev["close"]:
                continue
            if t["volume"] < self.p["vol_mult"] * float(d["volume"].iloc[-51:-1].mean()):
                continue
            if rng <= 0 or (t["close"] - t["low"]) / rng < 1 - self.p["close_top"]:
                continue
            watch[key] = {"high": float(t["high"]), "low": float(t["low"]), "left": int(self.p["trigger_sessions"])}
            ctx.log(self.name, f"day-0 gap-and-hold on {key}: armed for {self.p['trigger_sessions']} sessions")

    def _place(self, ctx, watch):
        allowed = ctx.posture(self.name)[0]
        for key, w in list(watch.items()):
            if w["left"] <= 0 or ctx.position(self.name, key):
                watch.pop(key, None)
                continue
            w["left"] -= 1
            if not allowed:
                continue
            tick = self.tick(ctx, key)
            trig = self.round_level(ctx, key, w["high"] + tick, +1)
            stop = self.round_level(ctx, key, w["low"] - int(self.p["stop_ticks"]) * tick, -1)
            qty = ctx.size(self.name, key, trig, stop)
            self.enter(ctx, key, Side.BUY, qty, product=Product.CNC, stop=stop, entry_ref=trig,
                       order_type=OrderType.SL, price=trig * (1 + self.p["entry_slip"]), trigger=trig)

    def on_fill(self, ctx, fill, order):
        st = ctx.state(self.name)
        book = st.setdefault("pos", {})
        if order.req.intent == "entry" and ctx.position(self.name, fill.key):
            book[fill.key] = {"entry_date": ctx.today.isoformat(), "stop": order.req.meta["stop"]}
            st.setdefault("watch", {}).pop(fill.key, None)
        if ctx.position(self.name, fill.key) is None:
            book.pop(fill.key, None)
        super().on_fill(ctx, fill, order)
