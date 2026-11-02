"""9) Residual mean reversion: statistical arbitrage after Avellaneda & Lee (2010).

Each stock's daily return is split into the part explained by the market's eigen-portfolios (as many factors
as the Marchenko-Pastur test finds above the noise bound of a 252-session correlation matrix) and an
idiosyncratic residual. The cumulative residual over the last 60 sessions is fitted as an Ornstein-Uhlenbeck
process. A stock is tradable only when that residual is mean-reverting by a Dickey-Fuller test and fast
(half-life 1-10 sessions); the s-score is its distance from equilibrium in equilibrium standard deviations:
  s < -1.25 -> buy (delivery); exit when s > -0.50
Long only: overnight short selling isn't possible in the cash market. Plus a time stop at 2.5 half-lives,
an ATR disaster stop and a results blackout.
Liquidity-providing: calm regime only, liquidated in a crisis.

Why not "OU on price levels": prices are close to random walks, and an OLS half-life on a 90-day window
passes about half of pure random walks (tests/unit/test_stats.py); Dickey-Fuller holds that to about 5%.
Residuals of a factor model are the series the OU model is meant for.
Caveats: long only and unhedged, so positions keep some market beta; published returns of this family weakened through the 2000s as it got crowded (e.g. the August 2007 quant unwind); at
5% significance across ~200 names a few "stationary" residuals a day are still chance - the s-score
threshold and the forward test are the real filter.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import math
from datetime import date

import numpy as np
import pandas as pd

from ..core.indicators import atr
from ..core.models import Product, Side
from ..core.stats import ar1_fit, eigen_factor_returns, factor_residuals, mp_clean
from .base import Strategy


class ResidualReversion(Strategy):
    name = "resid_reversion"
    horizon = "swing"
    style = "liquidity_providing"
    prior = (0.55, 1.1)
    enabled_by_default = False
    default_params = {"universe": "fno", "factor_window": 252, "ou_window": 60, "max_factors": 15,
                      "s_entry": 1.25, "s_exit_long": 0.50, "min_half_life": 1.0,
                      "max_half_life": 10.0, "require_df": True, "hold_half_lives": 2.5, "max_positions": 8,
                      "max_new_per_day": 3, "disaster_atr": 4.0, "min_price": 50.0,
                      "min_adv": 1e8, "results_window": 3, "eval_time": "15:10"}

    def data_needs(self, ctx):
        return {"daily": ctx.universe(self.p["universe"]), "daily_days": int(self.p["factor_window"]) + 80}

    def timers(self):
        return [self.p["eval_time"]]

    def scores(self, ctx) -> pd.DataFrame | None:
        keys = ctx.universe(self.p["universe"])
        ctx.snapshot(keys)
        fw, ow = int(self.p["factor_window"]), int(self.p["ou_window"])
        C = ctx.panel(keys, "close", tail=fw)                   # fw past sessions + today
        if len(C) < fw:
            return None
        C = C.loc[:, C.notna().all() & (C.iloc[-1] >= float(self.p["min_price"]))]
        if C.shape[1] < 20:
            return None
        V = ctx.panel(list(C.columns), "volume", tail=20)
        adv = (C.iloc[-20:] * V.reindex(index=C.index[-20:], columns=C.columns)).mean()
        R = np.diff(np.log(C.to_numpy(dtype=float)), axis=0)
        mp = mp_clean(R)
        k = int(min(max(mp["n_signal"], 1), int(self.p["max_factors"])))
        E, _ = factor_residuals(R[-ow:], eigen_factor_returns(R, R[-ow:], k))
        fit = ar1_fit(np.cumsum(E, axis=0))
        with np.errstate(invalid="ignore", divide="ignore"):
            s = (0.0 - fit["mu"]) / fit["sigma_eq"]          # cumulative residual ends at 0 by construction
        df = pd.DataFrame({"s": s, "half_life": fit["half_life"], "stationary": fit["stationary"], "adv": adv.to_numpy()},
                          index=C.columns)
        ok = df["half_life"].between(float(self.p["min_half_life"]), float(self.p["max_half_life"])) & np.isfinite(df["s"])
        if self.p["require_df"]:
            ok &= df["stationary"]
        df["tradable"] = ok & (df["adv"] >= float(self.p["min_adv"]))
        df.attrs.update(factors=k, names=C.shape[1])
        return df

    def _blackout(self, ctx, und: str) -> bool:
        sym, n = und.split(":")[-1], int(self.p["results_window"])
        return ctx.events.had_results_within(sym, ctx.today, n) or ctx.events.results_within_next(sym, ctx.today, n)

    def on_timer(self, ctx, t):
        if t != self.p["eval_time"]:
            return
        allowed, _, liquidate = ctx.posture(self.name)
        book = ctx.state(self.name).setdefault("book", {})
        sc = self.scores(ctx)
        self._manage(ctx, book, sc, liquidate)
        if not allowed or sc is None:
            return
        busy = {v["und"] for v in book.values()}
        slots = min(int(self.p["max_positions"]) - len(book), int(self.p["max_new_per_day"]))
        cands = sc[sc["tradable"] & (sc["s"] <= -float(self.p["s_entry"]))]
        for und in cands["s"].sort_values().index:                  # most stretched below equilibrium first
            if slots <= 0:
                break
            if und in busy or self._blackout(ctx, und):
                continue
            row = cands.loc[und]
            d = ctx.daily(und, with_today=True, tail=60)
            if len(d) < 30:
                continue
            a = float(atr(d, 14).iloc[-1])
            key, side, product = und, Side.BUY, Product.CNC
            px = float(d["close"].iloc[-1])
            stop = px - float(self.p["disaster_atr"]) * a
            qty = ctx.size(self.name, key, px, stop)
            if qty <= 0:
                continue
            if self.enter(ctx, key, side, qty, product=product, stop=stop, entry_ref=px,
                          meta={"s": round(float(row["s"]), 2), "hl": round(float(row["half_life"]), 1)}):
                book[key] = {"und": und, "side": "long", "entry_date": ctx.today.isoformat(),
                             "hl": float(row["half_life"]), "s": round(float(row["s"]), 2)}
                busy.add(und)
                slots -= 1

    def _manage(self, ctx, book: dict, sc, liquidate: bool) -> None:
        for key, info in list(book.items()):
            pos = ctx.position(self.name, key)
            if pos is None:
                if not ctx.active_orders(self.name, key):
                    book.pop(key, None)                    # exited, or the entry never filled
                continue
            held = ctx.calendar.sessions_between(date.fromisoformat(info["entry_date"]), ctx.today)
            s = sc["s"].get(info["und"]) if sc is not None else None
            reason = None
            if liquidate:
                reason = "crisis regime"
            elif s is None or not math.isfinite(s):
                reason = "residual model unavailable"
            elif info["side"] == "long" and s > -float(self.p["s_exit_long"]):
                reason = f"residual reverted (s={s:.2f})"
            elif held >= math.ceil(float(self.p["hold_half_lives"]) * info["hl"]):
                reason = "time stop"
            elif ctx.events.results_within_next(info["und"].split(":")[-1], ctx.today, int(self.p["results_window"])):
                reason = "results ahead"
            if reason:
                self.exit(ctx, key, reason)
