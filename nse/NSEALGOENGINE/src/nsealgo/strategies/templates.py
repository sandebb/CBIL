"""Skeletons for documented strategies: periodic long-only portfolios and per-instrument daily rules.

Both trade delivery (CNC) at the close (decided at 15:10 on the day's provisional bar, the same convention
as the other swing sleeves), protect every position with an ATR disaster stop, and re-underwrite holdings
after `max_hold` sessions. The time stop keeps holdings bounded, so research backtests can split periods
across cores exactly (the planner sizes warm-up and run-off from max_hold).
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

from datetime import date

import numpy as np
import pandas as pd

from ..core.indicators import atr
from ..core.models import Product, Side
from .base import Strategy

BASE = {"max_hold": 63, "disaster_atr": 4.0, "results_window": 3, "eval_time": "15:10"}


class RebalanceSleeve(Strategy):
    """Rank a universe on a schedule; hold the top_n; sell a holding when its rank falls beyond exit_rank."""
    horizon = "swing"
    style = "liquidity_taking"
    enabled_by_default = False
    lookback = 252
    default_params = {**BASE, "universe": "nifty100", "rebalance": "monthly", "top_n": 10, "exit_rank": 20,
                      "min_price": 20.0, "min_adv": 5e7}

    def candidates(self, ctx) -> list[str]:
        return ctx.universe(self.p["universe"])

    def data_needs(self, ctx):
        return {"daily": self.candidates(ctx), "daily_days": self.lookback + 60}

    def timers(self):
        return [self.p["eval_time"]]

    def rebalance_day(self, ctx) -> bool:
        prev = ctx.calendar.prev_trading_day(ctx.today)
        if self.p["rebalance"] == "weekly":
            return prev.isocalendar()[:2] != ctx.today.isocalendar()[:2]
        return prev.month != ctx.today.month                     # first session of the month

    def liquid_closes(self, ctx, keys, tail: int) -> pd.DataFrame | None:
        ctx.snapshot(keys)
        C = ctx.panel(keys, "close", tail=tail)
        if len(C) < tail + 1:
            return None
        C = C.loc[:, C.notna().all() & (C.iloc[-1] >= float(self.p["min_price"]))]
        if float(self.p["min_adv"]) > 0 and C.shape[1]:
            V = ctx.panel(list(C.columns), "volume", tail=20).reindex(index=C.index[-20:], columns=C.columns)
            C = C.loc[:, (C.iloc[-20:] * V).mean() >= float(self.p["min_adv"])]
        return C

    def scores(self, ctx) -> pd.Series | None:          # higher = more attractive
        raise NotImplementedError

    def on_timer(self, ctx, t):
        if t != self.p["eval_time"]:
            return
        book = ctx.state(self.name).setdefault("book", {})
        reb = self.rebalance_day(ctx)
        sc = self.scores(ctx) if reb else None
        ranks = sc.rank(ascending=False) if sc is not None else None
        self._manage(ctx, book, ranks)
        allowed, _, _ = ctx.posture(self.name)
        if sc is None or not allowed:
            return
        slots = int(self.p["top_n"]) - len(book)
        for key in sc.sort_values(ascending=False).index[: int(self.p["top_n"])]:
            if slots <= 0:
                break
            if key in book or ctx.position(self.name, key) or ctx.active_orders(self.name, key):
                continue
            sym, rw = key.split(":")[-1], int(self.p["results_window"])
            if rw and (ctx.events.had_results_within(sym, ctx.today, rw) or ctx.events.results_within_next(sym, ctx.today, rw)):
                continue
            d = ctx.daily(key, with_today=True, tail=60)
            if len(d) < 30:
                continue
            px, a = float(d["close"].iloc[-1]), float(atr(d, 14).iloc[-1])
            if not np.isfinite(a) or a <= 0:
                continue
            stop = px - float(self.p["disaster_atr"]) * a
            qty = ctx.size(self.name, key, px, stop)
            if qty > 0 and self.enter(ctx, key, Side.BUY, qty, product=Product.CNC, stop=stop, entry_ref=px,
                                      meta={"score": round(float(sc[key]), 4)}):
                book[key] = {"entry_date": ctx.today.isoformat()}
                slots -= 1

    def _manage(self, ctx, book: dict, ranks) -> None:
        for key, info in list(book.items()):
            if ctx.position(self.name, key) is None:
                if not ctx.active_orders(self.name, key):
                    book.pop(key, None)
                continue
            held = ctx.calendar.sessions_between(date.fromisoformat(info["entry_date"]), ctx.today)
            r = ranks.get(key) if ranks is not None else None
            rw = int(self.p["results_window"])
            if held >= int(self.p["max_hold"]):
                self.exit(ctx, key, "time stop (re-underwrite)")
            elif ranks is not None and (r is None or r > int(self.p["exit_rank"])):
                self.exit(ctx, key, f"rank {'-' if r is None else int(r)} beyond {self.p['exit_rank']}")
            elif rw and ctx.events.results_within_next(key.split(":")[-1], ctx.today, rw):
                self.exit(ctx, key, "results ahead")


class DailySignalSleeve(Strategy):
    """Per-instrument daily entry/exit rules, long only. Subclasses implement entry() -> stop price or None,
    and exit_reason() -> str or None; rank() orders same-day entries when slots are scarce."""
    horizon = "swing"
    style = "liquidity_taking"
    enabled_by_default = False
    tail = 260
    min_rows = 60
    default_params = {**BASE, "symbols": [], "universe": "nifty100", "max_positions": 5,
                      "results_window": 0}

    def keys(self, ctx) -> list[str]:
        return ctx.universe(self.p["universe"] if self.p.get("universe") else list(self.p["symbols"]))

    def data_needs(self, ctx):
        return {"daily": self.keys(ctx), "daily_days": self.tail + 60}

    def timers(self):
        return [self.p["eval_time"]]

    def entry(self, ctx, key, d) -> float | None:
        raise NotImplementedError

    def exit_reason(self, ctx, key, d, info) -> str | None:
        return None

    def rank(self, ctx, key, d) -> float:
        return 0.0

    def entry_info(self, ctx, key, d) -> dict:
        return {}

    def on_timer(self, ctx, t):
        if t != self.p["eval_time"]:
            return
        book = ctx.state(self.name).setdefault("book", {})
        keys = self.keys(ctx)
        ctx.snapshot(keys)
        for key, info in list(book.items()):
            if ctx.position(self.name, key) is None:
                if not ctx.active_orders(self.name, key):
                    book.pop(key, None)
                continue
            d = ctx.daily(key, with_today=True, tail=self.tail)
            held = ctx.calendar.sessions_between(date.fromisoformat(info["entry_date"]), ctx.today)
            reason = "time stop" if held >= int(self.p["max_hold"]) else self.exit_reason(ctx, key, d, info)
            if reason:
                self.exit(ctx, key, reason)
        allowed, _, _ = ctx.posture(self.name)
        if not allowed:
            return
        cands, rw = [], int(self.p["results_window"])
        for key in keys:
            if key in book or ctx.position(self.name, key) or ctx.active_orders(self.name, key):
                continue
            if rw and ctx.events.results_within_next(key.split(":")[-1], ctx.today, rw):
                continue
            d = ctx.daily(key, with_today=True, tail=self.tail)
            if len(d) < self.min_rows:
                continue
            stop = self.entry(ctx, key, d)
            if stop is not None and np.isfinite(stop) and stop < float(d["close"].iloc[-1]):
                cands.append((self.rank(ctx, key, d), key, d, float(stop)))
        slots = int(self.p["max_positions"]) - len(book)
        for _, key, d, stop in sorted(cands, key=lambda x: -x[0])[: max(slots, 0)]:
            px = float(d["close"].iloc[-1])
            qty = ctx.size(self.name, key, px, stop)
            if qty > 0 and self.enter(ctx, key, Side.BUY, qty, product=Product.CNC, stop=stop, entry_ref=px):
                book[key] = {"entry_date": ctx.today.isoformat(), "entry": px, **self.entry_info(ctx, key, d)}

    def disaster_stop(self, d) -> float:
        a = float(atr(d, 14).iloc[-1])
        return float(d["close"].iloc[-1]) - float(self.p["disaster_atr"]) * a if np.isfinite(a) and a > 0 else np.nan
