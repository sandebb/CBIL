"""4) Momentum leaders with volatility-contraction breakouts (swing, long-only, delivery).

Universe ranked by the NSE-momentum-index composite (6m and 12m risk-adjusted returns); from the top
decile buy names within 10% of the 52-week high, in a 50>200 DMA uptrend, whose ATR has contracted,
on a 20-day closing breakout with >=1.5x volume. Stop at the contraction low (skip if >8% away).
Exit on a close below the 20-EMA or if no +1R progress in 10 sessions. Risk is throttled when
momentum is crowded (comomentum) and scaled by the momentum basket's realised volatility.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import math
from datetime import date

import pandas as pd

from ..core.indicators import atr, ema, momentum_scores
from ..core.models import Product, Side
from .base import Strategy


class MomentumLeaders(Strategy):
    name = "momentum"
    horizon = "swing"
    style = "liquidity_taking"
    prior = (0.40, 2.5)
    default_params = {"universe": "nifty500", "top_frac": 0.10, "near_high": 0.10, "atr_fast": 10, "atr_slow": 50,
                      "contraction": 0.7, "breakout_days": 20, "vol_mult": 1.5, "max_stop_pct": 0.08,
                      "exit_ema": 20, "time_stop": 10, "vol_target": 0.15, "max_new_per_day": 3,
                      "eval_time": "15:10", "protect_time": "09:16"}

    def data_needs(self, ctx):
        return {"daily": ctx.universe(self.p["universe"]), "daily_days": 420}

    def timers(self):
        return [self.p["protect_time"], self.p["eval_time"]]

    def on_timer(self, ctx, t):
        book = ctx.state(self.name).setdefault("pos", {})
        if t == self.p["protect_time"]:
            for pos in ctx.positions(self.name):
                if pos.key in book:
                    self.protect(ctx, pos.key, book[pos.key]["stop"])
        elif t == self.p["eval_time"]:
            self._exits(ctx, book)
            self._entries(ctx)

    def _exits(self, ctx, book):
        for pos in list(ctx.positions(self.name)):
            info = book.get(pos.key)
            if not info:
                continue
            held = ctx.calendar.sessions_between(date.fromisoformat(info["entry_date"]), ctx.today)
            d = ctx.daily(pos.key, with_today=True, tail=max(120, held + 5))
            if len(d) < 30:
                continue
            last = float(d["close"].iloc[-1])
            if last < float(ema(d["close"], int(self.p["exit_ema"])).iloc[-1]):
                self.exit(ctx, pos.key, "close below 20-EMA")
            elif held >= int(self.p["time_stop"]) and float(d["high"].iloc[-held:].max()) < info["entry"] + info["risk"]:
                self.exit(ctx, pos.key, "no +1R progress")

    def _vol_scale(self, closes: pd.DataFrame, top) -> float:
        basket = closes[list(top)].pct_change().mean(axis=1).iloc[-126:]
        rv = float(basket.std() * math.sqrt(252))
        return min(1.0, float(self.p["vol_target"]) / rv) if rv > 0 else 1.0

    def _entries(self, ctx):
        allowed, _, _ = ctx.posture(self.name)
        if not allowed:
            return
        keys = ctx.universe(self.p["universe"])
        ctx.snapshot(keys)
        closes = ctx.panel(keys, "close", tail=252)            # 252 past sessions + today, all symbols at once
        closes = closes.loc[:, closes.notna().all()]
        if closes.shape[1] < 10 or len(closes) < 253:
            return
        scores = momentum_scores(closes)
        top = list(scores.index[: max(1, math.ceil(len(scores) * float(self.p["top_frac"])))])
        mult = ctx.crowding.momentum_multiplier() * self._vol_scale(closes, top)
        n_new = 0
        for key in top:
            if ctx.position(self.name, key) or ctx.active_orders(self.name, key):
                continue
            d = ctx.daily(key, with_today=True, tail=320)
            if len(d) < 260:
                continue
            c, last = d["close"], float(d["close"].iloc[-1])
            if last < (1 - self.p["near_high"]) * float(d["high"].iloc[-252:].max()):
                continue
            s50, s200 = float(c.iloc[-50:].mean()), float(c.iloc[-200:].mean())
            if not (last > s50 > s200):
                continue
            hist = d.iloc[:-1]
            if not float(atr(hist, int(self.p["atr_fast"])).iloc[-1]) <= self.p["contraction"] * float(
                    atr(hist, int(self.p["atr_slow"])).iloc[-1]):
                continue
            n = int(self.p["breakout_days"])
            if last <= float(d["high"].iloc[-(n + 1):-1].max()):
                continue
            if float(d["volume"].iloc[-1]) < self.p["vol_mult"] * float(d["volume"].iloc[-51:-1].mean()) * ctx.volume_fraction():
                continue
            stop = self.round_level(ctx, key, float(d["low"].iloc[-10:].min()), -1)
            if stop <= 0 or (last - stop) / last > self.p["max_stop_pct"] or last <= stop:
                continue
            qty = ctx.size(self.name, key, last, stop, mult=mult)
            if self.enter(ctx, key, Side.BUY, qty, product=Product.CNC, stop=stop, entry_ref=last,
                          meta={"score": round(float(scores[key]), 3)}):
                n_new += 1
                if n_new >= int(self.p["max_new_per_day"]):
                    break

    def on_fill(self, ctx, fill, order):
        book = ctx.state(self.name).setdefault("pos", {})
        if order.req.intent == "entry" and ctx.position(self.name, fill.key):
            book[fill.key] = {"entry_date": ctx.today.isoformat(), "entry": float(fill.price),
                              "stop": order.req.meta["stop"], "risk": max(float(fill.price) - order.req.meta["stop"], 0.01)}
        if ctx.position(self.name, fill.key) is None:
            book.pop(fill.key, None)
        super().on_fill(ctx, fill, order)
