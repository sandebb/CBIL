"""6) Panic pullback in uptrends (probation; half size; liquidity-providing -> calm regime only).

Nifty-100 stock above its 200-DMA, RSI(2) < 5 and a 3-day drop >= 2.5 x ATR that is NOT news-driven
(no results within 3 sessions, no volume spike). Buy near the close, exit on the first close above
the 5-DMA or after 7 sessions; 3 x ATR disaster stop. Retires itself if the average net trade after
30 trades is below 0.9% (costs of ~0.3% round trip eat thin mean-reversion edges).
"""
from __future__ import annotations

from datetime import date

from ..core.indicators import atr_panel, rsi
from ..core.models import Product, Side
from .base import Strategy


class PanicPullback(Strategy):
    name = "pullback"
    horizon = "swing"
    style = "liquidity_providing"
    probation = True
    prior = (0.60, 0.9)
    default_params = {"universe": "nifty100", "rsi_max": 5.0, "drop_atr": 2.5, "drop_days": 3, "exit_sma": 5,
                      "max_hold": 7, "disaster_atr": 3.0, "news_rvol": 2.5, "results_window": 3,
                      "retire_min_trades": 30, "retire_min_avg": 0.009, "max_new_per_day": 3,
                      "eval_time": "15:10", "protect_time": "09:16"}

    def data_needs(self, ctx):
        return {"daily": ctx.universe(self.p["universe"]), "daily_days": 260}

    def timers(self):
        return [self.p["protect_time"], self.p["eval_time"]]

    def _retired(self, ctx) -> bool:
        t = ctx.trades(self.name)
        if len(t) < int(self.p["retire_min_trades"]):
            return False
        avg = float((t["pnl_net"] / (t["entry_price"] * t["qty"])).mean())
        if avg < float(self.p["retire_min_avg"]):
            ctx.disable(self.name, f"average net trade {avg:.2%} below {self.p['retire_min_avg']:.2%}")
            return True
        return False

    def on_timer(self, ctx, t):
        book = ctx.state(self.name).setdefault("pos", {})
        if t == self.p["protect_time"]:
            for pos in ctx.positions(self.name):
                if pos.key in book:
                    self.protect(ctx, pos.key, book[pos.key]["stop"])
            return
        if t != self.p["eval_time"] or self._retired(ctx):
            return
        for pos in list(ctx.positions(self.name)):
            info = book.get(pos.key)
            d = ctx.daily(pos.key, with_today=True, tail=30)
            if not info or len(d) < 10:
                continue
            held = ctx.calendar.sessions_between(date.fromisoformat(info["entry_date"]), ctx.today)
            if float(d["close"].iloc[-1]) > float(d["close"].iloc[-int(self.p["exit_sma"]):].mean()):
                self.exit(ctx, pos.key, "close above 5-DMA")
            elif held >= int(self.p["max_hold"]):
                self.exit(ctx, pos.key, "time stop")
        if not ctx.posture(self.name)[0]:
            return
        keys = ctx.universe(self.p["universe"])
        ctx.snapshot(keys)
        # vectorised screen over the whole universe (dates x symbols), then per-name checks for the few hits
        C, H, L, V = (ctx.panel(keys, f, tail=260) for f in ("close", "high", "low", "volume"))
        if len(C) < 210:
            return
        nd, last = int(self.p["drop_days"]), C.iloc[-1]
        sma200 = C.iloc[-201:-1].mean(skipna=False)
        rsi2 = rsi(C, 2).iloc[-1]
        a = atr_panel(H.iloc[:-1], L.iloc[:-1], C.iloc[:-1], 14).iloc[-1]
        drop = C.iloc[-1 - nd] - last
        v20 = V.iloc[-21:-1].mean()
        spike = (V.iloc[-3:] / v20.where(v20 > 0)).max()
        ok = (C.notna().sum() >= 210) & (last > sma200) & (rsi2 < self.p["rsi_max"]) & \
             (drop >= self.p["drop_atr"] * a) & ~(spike > self.p["news_rvol"])
        n_new = 0
        for key in [k for k in keys if bool(ok.get(k, False))]:
            if ctx.position(self.name, key) or ctx.active_orders(self.name, key):
                continue
            if ctx.events.had_results_within(key.split(":")[-1], ctx.today, int(self.p["results_window"])):
                continue
            px = float(last[key])
            stop = px - self.p["disaster_atr"] * float(a[key])
            if self.enter(ctx, key, Side.BUY, ctx.size(self.name, key, px, stop), product=Product.CNC, stop=stop,
                          entry_ref=px):
                n_new += 1
                if n_new >= int(self.p["max_new_per_day"]):
                    break

    def on_fill(self, ctx, fill, order):
        book = ctx.state(self.name).setdefault("pos", {})
        if order.req.intent == "entry" and ctx.position(self.name, fill.key):
            book[fill.key] = {"entry_date": ctx.today.isoformat(), "stop": order.req.meta["stop"]}
        if ctx.position(self.name, fill.key) is None:
            book.pop(fill.key, None)
        super().on_fill(ctx, fill, order)
