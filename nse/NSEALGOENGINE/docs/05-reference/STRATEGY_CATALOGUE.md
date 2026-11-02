> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Documented strategies: what nsealgo includes, and what it leaves out

These are strategies published in books, academic papers and trading forums.

- **Included:** every one is a runnable strategy. Those added in 2.3 are *research sleeves*, off until you enable them in `config/system.yaml`.
- **Excluded:** each has a stated reason.

Test them all the same way:
1. `nsealgo backtest 2021-01-01 2026-09-25 --strategies NAME`, which uses every core;
2. `nsealgo walkforward NAME 2021-01-01 2026-09-25` (±20% parameter perturbation);
3. a forward test, whose promotion gates decide whether a strategy ever trades money.

Published results mostly come from US data, before costs and before the effect became widely known. Expect weaker results on NSE after STT (0.1% per side on delivery trades) and other charges.

## Included (17 strategies)

| nsealgo name | Source | Idea | Horizon |
|---|---|---|---|
| `orb` | Crabel (1990); Zarattini, Barbon & Aziz (2024) | Opening-range breakout in "stocks in play" | intraday |
| `vwap_fade` | Market-microstructure reversion; VPIN (Easley, López de Prado & O'Hara 2012) | Fade VWAP-band extremes on quiet days, vetoed when order flow is toxic | intraday |
| `momentum` | Jegadeesh & Titman (1993); NSE momentum-index method | Cross-sectional momentum with a breakout entry | swing |
| `pead` | Ball & Brown (1968); Bernard & Thomas (1989) | Post-earnings-announcement drift | swing |
| `pullback` | Connors & Alvarez (2009) | RSI(2) dips in stocks above their 200-day average | swing |
| `forced_flow` | Index-inclusion literature (Harris & Gurel 1986; Shleifer 1986) | Liquidity to index-rebalance flows | swing |
| `resid_reversion` | Avellaneda & Lee (2010) | OU reversion of factor residuals | swing |
| `skew_premium` | Bali, Cakici & Whitelaw (2011); Boyer, Mitton & Vorkink (2010) | Avoid lottery stocks, hold low-skew ones | swing |
| **`turtle`** | Faith, *Way of the Turtle* (2007); Donchian channels | 55/20-day breakout (or 20/10 with the System 1 filter), 2N stop | swing |
| **`supertrend`** | O. Seban; Indian forums, TradingView | ATR(10) × 3 trailing trend flips on index ETFs | swing |
| **`high52`** | George & Hwang (2004) | Hold stocks nearest their 52-week high | monthly |
| **`low_vol`** | Ang et al. (2006); Blitz & van Vliet (2007); Baker, Bradley & Wurgler (2011) | Hold the least volatile stocks (the rule behind NSE's Nifty100 Low Vol 30) | monthly |
| **`resid_momentum`** | Blitz, Huij & Martens (2011) | Momentum on market-model residuals: fewer momentum crashes | monthly |
| **`compression_breakout`** | Crabel (1990); Ochoa, *Secrets of a Pivot Boss* (2010) | Break of yesterday's range after an NR7, inside-day or narrow-CPR day | intraday |
| **`gap_fade`** | Retail staple (Zerodha Varsity and forums); mixed evidence | Fade 0.3–1.2% opening gaps toward the previous close | intraday |
| `scalper` | Cont, Kukanov & Stoikov (2014) order-flow imbalance | Take short moves the order flow predicts, only when the target pays costs (2.4, paper) | seconds–minutes |
| `market_maker` | Avellaneda & Stoikov (2008) | Inventory-skewed passive quotes, spread floored at costs (2.4, paper) | intraday |

Bold = new in 2.3. Where the implementations depart from the originals (deliberately, and stated in each module's docstring):
- `turtle` does not pyramid (no adding of units).
- Connors' systems get a 4-ATR disaster stop; the books use none.
- Crabel's stop orders at a fixed "stretch" from the open become breakouts confirmed on a 1-minute close.
- Monthly portfolios re-underwrite each holding every quarter, via a time stop that immediately re-buys names still ranked. This also keeps parallel backtests exact.

## Documented, but deliberately not included

| Strategy family | Why not |
|---|---|
| Futures and options of any kind: option selling (straddles, strangles, iron condors), futures trend or carry, calendar spreads | nsealgo trades cash-market stocks only (since 3.0). They need option chains, Greeks and SPAN-margin modelling, and SEBI's late-2024 F&O measures (larger contracts, fewer weekly expiries) changed their economics. |
| Value and quality factors: Magic Formula (Greenblatt), Piotroski F-score, dividend yield | They need fundamental data, which no broker API supplies. They could be added from a fundamentals CSV. |
| Overnight premium, BTST (buy the close, sell the open) | Delivery STT of 0.1% per side, plus stamp and DP charges, exceeds the documented overnight edge. |
| Expiry-day option-flow patterns (max pain, pinning) | They need option open interest, and the expiry calendar has changed repeatedly. |
| Latency arbitrage | It needs exchange co-location and tick-by-tick feeds; free broker feeds are about one-second snapshots. Scalping and market making are included since 2.4 as cost-gated paper-test strategies; see MICROSTRUCTURE.md. |
| News, sentiment and NLP trading | They need licensed news feeds and low latency. |
| IPO listing day, lock-up expiry, dividend capture, buybacks | They need corporate-action data, involve tax effects, and have small samples. |
| Machine-learning "predictors" from GitHub and forums | No documented out-of-sample edge, and a high risk of overfitting. The engine can host one; none is shipped. |
| Ichimoku, Heikin Ashi, MACD and EMA crossovers, Parabolic SAR, Camarilla pivots | Forum staples without consistent published evidence. `supertrend` and the CPR setup represent this family; any other takes about 20 lines (below). |
| Martingale, averaging down, grid trading | Unbounded loss by construction. The risk layer is designed to prevent it. |
| Muhurat trading, Budget-day patterns | One session a year is too few observations to test. |
| Weekly short-term reversal (Jegadeesh 1990; Lehmann 1990) | Covered by `pullback`; weekly turnover at 0.2% STT per round trip is costly. |

| Pairs and long/short statistical arbitrage | Holding a short overnight needs futures; in the cash market short selling is intraday-only. (`pairs` was removed in 3.0; `resid_reversion` and `skew_premium` are long-only.) |
| Index and ETF strategies: noise-band index momentum, ETF dual momentum, index-ETF mean reversion, turn-of-the-month | Stocks only. `noise_band`, `etf_rotation`, `index_mr` and `calendar_effects` were removed in 3.0 (still in the 2.5 package). |

## Adding another documented rule (about 20 lines)

Per-instrument daily systems subclass `DailySignalSleeve`; periodic portfolios subclass `RebalanceSleeve` (see `strategies/templates.py`). For example, a 50/200-day golden cross on Nifty 100 stocks:

```python
from nsealgo.strategies.templates import DailySignalSleeve

class GoldenCross(DailySignalSleeve):
    name = "golden_cross"
    default_params = {**DailySignalSleeve.default_params, "universe": "nifty100", "max_hold": 250}

    def entry(self, ctx, key, d):
        c = d["close"]
        crossed = c.iloc[-50:].mean() > c.iloc[-200:].mean() and c.iloc[-51:-1].mean() <= c.iloc[-201:-1].mean()
        return self.disaster_stop(d) if crossed else None

    def exit_reason(self, ctx, key, d, info):
        c = d["close"]
        return "death cross" if c.iloc[-50:].mean() < c.iloc[-200:].mean() else None
```

Then add it to `REGISTRY` in `strategies/__init__.py`, enable it in `config/system.yaml`, and run the three steps at the top.
