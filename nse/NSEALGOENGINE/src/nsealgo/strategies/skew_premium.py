"""10) Lottery (skewness) premium: cross-sectional, rebalanced weekly.

Investors overpay for lottery-like payoffs, so the most positively skewed stocks tend to earn less later
(Boyer, Mitton & Vorkink 2010; Bali, Cakici & Whitelaw 2011; Amaya et al. 2015). Lottery score = average
cross-sectional percentile of
  (a) idiosyncratic skewness of the last 60 daily log returns (market beta removed, so an index-wide move
      doesn't masquerade as stock skew), and
  (b) MAX: the mean of the 5 largest daily returns in the last 21 sessions, a sturdier lottery proxy than a
      60-observation skewness, which a single jump can dominate.
Long the least lottery-like decile (delivery); long only, because cash shorts can't be carried overnight.
Weekly rebalance on the first session of the week with hysteresis (a holding is sold only when it leaves the
outer 30%), ATR disaster stops, no entries around results, at most 20 sessions per holding.

Caveats: most of the published premium comes from the SHORT side in small, illiquid stocks, which Indian
retail accounts can't short overnight; the long-only version behaves much like a low-volatility tilt.
Treat it as a research sleeve until the forward test says otherwise.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

from datetime import date

import numpy as np
import pandas as pd

from ..core.indicators import atr
from ..core.models import Product, Side
from .base import Strategy


class LotterySkewPremium(Strategy):
    name = "skew_premium"
    horizon = "swing"
    style = "liquidity_taking"
    prior = (0.52, 1.2)
    enabled_by_default = False
    default_params = {"universe": "nifty500", "lookback": 60, "max_window": 21, "max_days": 5, "quantile": 0.10,
                      "exit_quantile": 0.30, "max_positions": 10, "disaster_atr": 4.0,
                      "max_hold": 20, "min_price": 50.0, "min_adv": 5e7, "results_window": 3,
                      "eval_time": "15:10"}

    def data_needs(self, ctx):
        return {"daily": ctx.universe(self.p["universe"]), "daily_days": int(self.p["lookback"]) + 60}

    def timers(self):
        return [self.p["eval_time"]]

    def rebalance_day(self, ctx) -> bool:
        prev = ctx.calendar.prev_trading_day(ctx.today)
        return prev.isocalendar()[:2] != ctx.today.isocalendar()[:2]

    def scores(self, ctx) -> pd.DataFrame | None:
        keys = ctx.universe(self.p["universe"])
        ctx.snapshot(keys)
        lb = int(self.p["lookback"])
        C = ctx.panel(keys, "close", tail=lb)
        if len(C) < lb + 1:
            return None
        C = C.loc[:, C.notna().all() & (C.iloc[-1] >= float(self.p["min_price"]))]
        V = ctx.panel(list(C.columns), "volume", tail=20).reindex(index=C.index[-20:], columns=C.columns)
        C = C.loc[:, (C.iloc[-20:] * V).mean() >= float(self.p["min_adv"])]
        if C.shape[1] < 20:
            return None
        R = np.log(C).diff().iloc[1:]
        mk = ctx.daily(ctx.e.cfg["regime"]["index_key"], with_today=True, tail=lb + 5)
        m = np.log(mk["close"]).diff().reindex(R.index) if len(mk) else None
        Rc = R - R.mean()
        if m is not None and m.notna().all() and float(m.var()) > 0:
            mc = m - m.mean()
            beta = Rc.mul(mc, axis=0).sum() / float((mc * mc).sum())
            resid = Rc - np.outer(mc.to_numpy(), beta.to_numpy())
        else:
            resid = Rc                                          # no index history: total skewness
        skew = resid.skew()
        mx = pd.Series(np.sort(C.pct_change().iloc[-int(self.p["max_window"]):].to_numpy(), axis=0)
                       [-int(self.p["max_days"]):].mean(axis=0), index=C.columns)
        score = ((skew.rank(pct=True) + mx.rank(pct=True)) / 2).rank(pct=True)
        return pd.DataFrame({"score": score, "skew": skew, "max": mx})

    def on_timer(self, ctx, t):
        if t != self.p["eval_time"]:
            return
        book = ctx.state(self.name).setdefault("book", {})
        rebalance = self.rebalance_day(ctx)
        sc = self.scores(ctx) if rebalance else None
        self._manage(ctx, book, sc)
        allowed, _, _ = ctx.posture(self.name)
        if not rebalance or sc is None or not allowed:
            return
        slots = int(self.p["max_positions"]) - len(book)
        busy = {v["und"] for v in book.values()}
        q = float(self.p["quantile"])
        for side, picks in [(Side.BUY, sc[sc["score"] <= q].sort_values("score"))]:
            for und in picks.index:
                if slots <= 0:
                    return
                sym = und.split(":")[-1]
                if und in busy or ctx.events.had_results_within(sym, ctx.today, int(self.p["results_window"])) \
                        or ctx.events.results_within_next(sym, ctx.today, int(self.p["results_window"])):
                    continue
                d = ctx.daily(und, with_today=True, tail=60)
                if len(d) < 30:
                    continue
                a = float(atr(d, 14).iloc[-1])
                key, product, px = und, Product.CNC, float(d["close"].iloc[-1])
                stop = px - float(self.p["disaster_atr"]) * a
                qty = ctx.size(self.name, key, px, stop)
                if qty > 0 and self.enter(ctx, key, side, qty, product=product, stop=stop, entry_ref=px,
                                          meta={"score": round(float(picks.loc[und, "score"]), 3)}):
                    book[key] = {"und": und, "side": "long", "entry_date": ctx.today.isoformat()}
                    busy.add(und)
                    slots -= 1

    def _manage(self, ctx, book: dict, sc) -> None:
        eq_ = float(self.p["exit_quantile"])
        for key, info in list(book.items()):
            if ctx.position(self.name, key) is None:
                if not ctx.active_orders(self.name, key):
                    book.pop(key, None)
                continue
            held = ctx.calendar.sessions_between(date.fromisoformat(info["entry_date"]), ctx.today)
            score = sc["score"].get(info["und"]) if sc is not None else None
            reason = None
            if held >= int(self.p["max_hold"]):
                reason = "time stop"
            elif score is not None and info["side"] == "long" and score > eq_:
                reason = f"no longer low-lottery (score {score:.2f})"
            elif ctx.events.results_within_next(info["und"].split(":")[-1], ctx.today, int(self.p["results_window"])):
                reason = "results ahead"
            if reason:
                self.exit(ctx, key, reason)
