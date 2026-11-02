> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Statistical strategies and research tools (2.2)

This covers four proposals: the "first principles" note, Reco2 (bar-component skewness), Reco3 (RMT, TDA, transfer entropy, MF-DFA) and Reco4 (how those map onto the engine). Every idea was checked against the maths, simulated where a claim could be tested, and then either built properly, turned into a measurement tool, or declined.

> **Note on "heuristic" strategies.** The existing strategies are not chart folklore. Each implements a documented return effect with a published statistical basis:
> - `orb`: intraday momentum;
> - `vwap_fade`: short-horizon reversion with an order-flow toxicity veto;
> - `momentum`: time-series and cross-sectional momentum;
> - `pullback`: short-term reversal.
>
> The new work adds distribution-based and factor-based tools *alongside* them. Nothing replaces the forward-test gates, which decide promotion for every strategy.

## New strategies (research sleeves: off until you enable them)

Both are `enabled_by_default = False`. Enable one in `config/system.yaml` (`strategies: {skew_premium: {enabled: true}}`), or run it explicitly with `nsealgo backtest --strategies skew_premium ...`.

### `resid_reversion`: residual mean reversion (Avellaneda & Lee 2010)

The first document proposed fitting an Ornstein-Uhlenbeck process to each stock's *price* and trading when the OLS half-life is short. That fails a simple test. On 2,000 simulated pure random walks of 90 sessions, the proposed rule behaves like this:

| Check | Share of random walks |
|---|---|
| OLS half-life ≤ 15 days (the proposed "proof" of reversion) | 53.9% |
| ...and \|z\| > 2 with the proposed scaling (would trade) | 24.3% |
| Dickey-Fuller test at 5% | 5.2% |

So across 500 stocks, the proposal would open roughly 120 noise positions a day. The scaling is also wrong: it divides by the one-step residual SD instead of the equilibrium SD, σ/√(1−b²), which inflates z. The first two figures are reproduced in `tests/unit/test_stats.py`.

What `resid_reversion` does instead:
1. **Factors.** Each stock's daily return is regressed on the market's eigen-portfolios. The number of factors is whatever the Marchenko-Pastur test finds above the noise bound of a 252-session correlation matrix, capped at 15.
2. **Residual model.** The cumulative *residual* over 60 sessions is fitted as AR(1)/OU.
3. **Filters.** A name trades only if the Dickey-Fuller test rejects a unit root at 5% and the half-life is 1–10 sessions.
4. **Signal.** The s-score is −m/σ_eq. Buy at s < −1.25 and exit at s > −0.5. Long only: overnight short selling isn't possible in the cash market.
5. **Protection.** Time stop at 2.5 half-lives, an ATR disaster stop and a results blackout.
6. **Regime.** It trades in calm regimes only, and liquidates in a crisis.

On the synthetic market, which has no genuine residual reversion by construction:

| Filter | Name-days admitted |
|---|---|
| Half-life filter alone | 78% |
| Dickey-Fuller | 6% |
| ...and \|s\| ≥ 1.25 (enters a trade) | 15 trades in 120 sessions |

The price of that protection is power: on a true OU series with a 3-session half-life and 60 observations, the test detects reversion about 47% of the time.

Caveats:
- Long only and unhedged, so positions keep some market beta.
- The published returns of this strategy family weakened through the 2000s as the trade got crowded (the August 2007 quant unwind is the classic example).

### `skew_premium`: lottery/skewness premium

The first document's version:
- ranked stocks by raw 60-day skewness and traded daily;
- sent MARKET orders;
- sized every position on a 5% stop;
- shorted cash stocks overnight, which Indian retail accounts can't do.

This version:
1. **Lottery score.** The average percentile of two measures:
   - *idiosyncratic* skewness, with market beta removed, so an index-wide move isn't counted as a stock's skew;
   - **MAX**, the mean of the 5 largest daily returns in the last 21 sessions (Bali, Cakici & Whitelaw 2011), which one jump can't dominate the way it can a 60-observation skew.
2. **Rebalancing.** Weekly, with hysteresis: a holding is sold only when it leaves the outer 30%.
3. **Filters and exits.** Liquidity and price filters, an ATR disaster stop, a results blackout, and a 20-session time stop.
4. **Legs.** Long the least lottery-like decile only.

Caveat: in the literature most of the premium comes from the *short* side in small, illiquid stocks. The long-only version behaves much like a low-volatility tilt.

## New overlay: market fragility (`fragility:` in config)

Reco3/Reco4 proposed topological data analysis (persistence landscapes) as a crash early-warning gate. Two cheaper measures capture the same geometry, "correlations tighten and the market collapses onto fewer dimensions":
- **Absorption ratio** (Kritzman, Li, Page & Rigobon 2011): the share of variance in the top fifth of eigenvectors of a 60-session window of `nifty50` returns. It is judged by its standardised shift, the 15-session mean against the 1-year mean.
- **Minimum-spanning-tree length** on correlation distances. MST edges are *exactly* the H0 persistence of a Vietoris-Rips filtration, so this is the TDA computation Reco3 sketched, without a TDA library. It shrinks as the market contracts (Onnela et al. 2003). It is reported, not used for gating.

The default action is `report`: the state is logged and visible to strategies through `ctx.fragility()`, but it changes nothing. `halve` halves new swing entries while the market is fragile, and `block` stops them. Most of the evidence comes from US markets, so let the forward test show the gate earns its keep before you switch it on. The timeline is computed from data before each session; `tests/unit/test_stats.py` rewrites the "future" and checks that nothing changes.

## New gates: variance ratio for `orb` and `vwap_fade` (off by default)

Reco4 proposed multifractal spectrum width, measured each morning from 09:15 to 11:30, as a switch between breakout and fade. Two problems:
- **Reco3's code crashes on that exact input.** 135 one-minute bars can't fill its 160-bar scale; it silently skips the scale, then fits a slope across arrays of different lengths (`TypeError: expected x and y to have same length`).
- **One morning is too little data.** A correct DFA is unbiased, but on 135 points of pure white noise (true H = 0.5), 90% of estimates fall anywhere between 0.35 and 0.68. It needs thousands of points to get within ±0.05.

The gates use the Lo-MacKinlay variance ratio instead:
- it works on 5-minute returns over the last 10 sessions;
- it uses a heteroskedasticity-robust z*;
- it never lets an overnight gap span a sample.

The two settings:
- `orb: {trend_filter: true}` trades only names whose z* ≥ 1 (their 30-minute moves have been extending).
- `vwap_fade: {reversion_filter: true}` fades only names whose z* ≤ −1 (their moves have been reverting).

Both are off by default. Turn one on, rerun the same backtest window, and compare before trusting it.

## Research commands (measure the claims on your own data)

| Command | What it tells you |
|---|---|
| `nsealgo anatomy SYMBOL` | Distribution of gap, upper and lower excursion, and drift. Measured event rates sit next to the Edgeworth estimate, which is marked **invalid** where skew/kurtosis push the expansion outside a proper distribution (common for these heavy-tailed, bounded components). Also gives a 2%-noise stop distance. |
| `nsealgo leadlag LEADER FOLLOWER` | Transfer entropy both ways at 1–5 minute lags. Plug-in estimates use equal-frequency bins that keep ties together, stay within sessions, and get shuffle-surrogate p-values against a Bonferroni level. Also shows the lagged correlation, because TE says nothing about direction. |
| `nsealgo fractal SYMBOL` | Variance ratios at 10, 30 and 60 minutes; DFA Hurst and MF-DFA width over all sessions; and how far a single morning's Hurst estimate wanders day to day. |

## Declined, with reasons

- **Edgeworth / Gram-Charlier probabilities as trade rules (Reco2).**
  - *Validity:* the expansions are only valid for mild skew and kurtosis, while bar excursions are bounded at zero and heavy-tailed. Clamping outputs to [0.0001, 0.9999] hides invalid "probabilities" rather than fixing them.
  - *Noise:* a "P(green close) ≥ 0.60" rule estimated from 60 bars carries about ±6% of sampling noise around a base rate near 50%.
  - *Model D:* it double-counts the morning's move, using the full-day drift distribution shifted by the morning return.
  - *What's kept:* the decomposition and both estimates, in `anatomy`. Stops and gap risk already use empirical quantiles (the gap-quantile cap in sizing).
- **Transfer-entropy lead-lag trading (Reco3/Reco4).**
  - *Direction:* TE measures dependence, not direction.
  - *The estimator:* Reco3's used min-max bins and separately-binned histograms, and had no significance test.
  - *Execution:* most importantly, a minute-level lead must survive API latency, the 10-orders/second cap, and costs (the proposal's "front-running" framing aside).
  - *What you get instead:* `nsealgo leadlag` measures whether any lead exists in your recorded data before anything is built on it.
- **Persistence-landscape crash warnings (Reco3/Reco4).** The published evidence rests on a handful of US crashes, in-sample. The H0 part is included cheaply as MST length (above); a TDA dependency was not added.
- **Kalman-filter hedge ratios (first document).** Not applicable: pairs trading needs overnight shorts, which means futures, and nsealgo trades stocks only.

## How to evaluate a research sleeve

1. **Backtest:** `nsealgo backtest 2021-01-01 2026-09-25 --strategies skew_premium` (research mode uses every core).
2. **Robustness:** `nsealgo walkforward skew_premium 2021-01-01 2026-09-25` (±20% parameter perturbation; fragile = curve-fit).
3. **Forward test:** enable it in `config/system.yaml` and run `nsealgo forwardtest --daemon`. The promotion gates (trade count, drawdown, confidence intervals) decide whether it ever trades money.
