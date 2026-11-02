"""Cross-sectional factor portfolios from the academic literature (long-only, monthly, delivery).

- high52: 52-week-high momentum. Stocks trading close to their 52-week high keep outperforming, because
  investors anchor on the high and under-react to news (George & Hwang 2004).
- low_vol: the low-volatility anomaly. The least volatile stocks have earned higher risk-adjusted returns
  than theory predicts (Ang, Hodrick, Xing & Zhang 2006; Blitz & van Vliet 2007; Baker, Bradley & Wurgler
  2011). NSE's Nifty100 Low Volatility 30 index uses the same one-year-volatility ranking.
- resid_momentum: momentum measured on returns with the market component removed (Blitz, Huij & Martens
  2011). It crashes less than plain momentum. The paper used Fama-French residuals from monthly data; this
  uses a market-model regression on 18 months of daily data, and the 12-minus-1-month residual return
  divided by its volatility.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import numpy as np
import pandas as pd

from .templates import RebalanceSleeve


class High52Momentum(RebalanceSleeve):
    name = "high52"
    prior = (0.52, 1.3)
    lookback = 252
    default_params = {**RebalanceSleeve.default_params, "universe": "nifty500", "top_n": 12, "exit_rank": 40}

    def scores(self, ctx):
        C = self.liquid_closes(ctx, self.candidates(ctx), self.lookback)
        if C is None or C.shape[1] < 20:
            return None
        H = ctx.panel(list(C.columns), "high", tail=self.lookback).reindex(index=C.index, columns=C.columns)
        return (C.iloc[-1] / H.max()).dropna()


class LowVolatility(RebalanceSleeve):
    name = "low_vol"
    prior = (0.55, 1.0)
    lookback = 252
    default_params = {**RebalanceSleeve.default_params, "universe": "nifty100", "top_n": 15, "exit_rank": 30}

    def scores(self, ctx):
        C = self.liquid_closes(ctx, self.candidates(ctx), self.lookback)
        if C is None or C.shape[1] < 20:
            return None
        return -np.log(C).diff().iloc[1:].std()


class ResidualMomentum(RebalanceSleeve):
    name = "resid_momentum"
    prior = (0.53, 1.2)
    lookback = 378
    default_params = {**RebalanceSleeve.default_params, "universe": "nifty500", "top_n": 12, "exit_rank": 40,
                      "formation": 252, "skip": 21}

    def scores(self, ctx):
        C = self.liquid_closes(ctx, self.candidates(ctx), self.lookback)
        if C is None or C.shape[1] < 20:
            return None
        R = np.log(C).diff().iloc[1:]
        ik = ctx.e.cfg["regime"]["index_key"]
        ctx.snapshot([ik])                                  # today's index bar, like the stocks' (else NaN below)
        mk = ctx.daily(ik, with_today=True, tail=self.lookback + 5)
        if not len(mk):
            return None
        m = np.log(mk["close"]).diff().reindex(R.index)
        if m.isna().any():
            return None
        X = np.column_stack([np.ones(len(m)), m.to_numpy()])
        B = np.linalg.lstsq(X, R.to_numpy(), rcond=None)[0]
        E = pd.DataFrame(R.to_numpy() - X @ B, index=R.index, columns=R.columns)
        F = E.iloc[-int(self.p["formation"]):-int(self.p["skip"])]
        sd = F.std().replace(0, np.nan)
        return (F.sum() / sd).dropna()
