"""features_v2 + targets + daily universe -> work\\dataset_v2.parquet

Design rules (see the "DeepLOB -> Daily NSE OHLCV" research design):
  * every input is SCALE-FREE: log ratios, ratios to ATR / volatility / average volume, ranks. A Rs 40 and a
    Rs 40,000 stock mean the same thing to the model, so one model is pooled across all stocks.
  * every input on row t uses information available after the 19:30 IST update on day t only
    (stock data up to t's close; global markets lagged in data.load_market). tests\\test_lookahead.py checks it.
  * targets describe day t+1 relative to day t's close and are scaled by the stock's own daily volatility.

Targets (row t, about day t+1; NaN when t+1 is not the stock's next trading day):
  y_g  log(O[t+1] / C[t])                    overnight gap
  y_r  log(C[t+1] / C[t])                    close-to-close return
  y_u  log(H[t+1] / max(O[t+1], C[t+1]))     upper wick  (>= 0)
  y_d  log(min(O[t+1], C[t+1]) / L[t+1])     lower wick  (>= 0)
  y_v  log(V[t+1] / mean V over the 20 days to t)    relative volume
  y_g_n, y_r_n, y_u_n, y_d_n = the first four divided by sigma (EWMA daily volatility at t)
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import math
import os
from pathlib import Path
import time

import polars as pl

from . import data
from . import links as LK
from .config import cfg, dataset_path, work_dir

F32 = pl.Float32
LN2X4 = 4 * math.log(2)

STOCK_FEATURES = [
    # today's bar, relative to yesterday's close / today's open
    "lr", "gap", "hr", "lrr", "log_co", "log_hl", "clv", "uw", "lw", "lr_z",
    # multi-horizon returns
    "r5", "r10", "r20", "r60", "r120", "r5_z", "r20_z",
    # trend
    "c_sma20", "c_sma50", "sma20_sma50", "dist_hi20", "dist_lo20", "macd", "macd_hist",
    # oscillators
    "rsi14", "stoch14", "zscore20",
    # volatility
    "sigma", "atr14", "vol5", "vol20", "vol_ratio", "park20", "gap_abs20", "uw20", "lw20", "range20",
    # volume / liquidity
    "rel_vol", "rel_vol_5_60", "log_tv", "log_med_tv60", "obv_slope20",
    # distribution
    "skew20", "kurt20",
    # market link
    "beta60", "corr60", "resid1", "resid5",
    # static / calendar
    "listing_age", "is_fno", "dow", "dom", "days_to_month_end",
]
MARKET_FEATURES = [
    "nifty_r1", "nifty_r5", "nifty_r20", "nifty_vol20", "vix_level", "vix_chg",
    "sp500_r1", "sp500_r5", "ftse100_r1", "dax_r1", "nikkei225_r1", "crude_oil_r1", "gold_r1", "usd_inr_r1",
]
CS_SOURCES = ["lr", "r5", "r20", "r60", "rel_vol", "vol20", "log_tv", "resid1", "resid5", "gap"]
CS_FEATURES = [f"cs_{c}" for c in CS_SOURCES]
# Optional "alt" inputs (config: extra_features: true - the parallel model in config_alt.yaml; the original model
# never sees them). From NSEDATAENGINE data_ref: delivery (day t, public ~18:00), short selling (day t, public that
# evening), MTF book (day t-1: published after the day, joined on its `disclosed` date). All scale-free.
ALT_FEATURES = [
    "deliv_pct",      # delivered / traded shares today
    "deliv_z20",      # today's delivery % vs the stock's own last 20 days (z-score)
    "deliv_5_20",     # log(5-day avg delivery % / 20-day avg)
    "deliv_val_tv",   # log(delivered value today / 60-day median turnover)
    "short_pct",      # shares sold short (institutional, reported) / shares traded today
    "short_5d_pct",   # same over the last 5 days
    "short_z60",      # today's short % vs the stock's own last 60 days
    "mtf_days",       # log(1 + MTF-financed shares / 20-day average volume)  = days of volume carried on margin
    "mtf_chg5",       # log change of MTF-financed shares over 5 sessions
    "mtf_amt_tv",     # MTF amount financed / 60-day median turnover
]
# Delivery-% nowcast (config: delivery_nowcast: true, ALT-CLOSE model only - see nseml/nowcast.py): an estimate of
# TODAY's delivery % made at 15:37 from today's bar + delivery history to yesterday (actual % is published ~18:00).
NOWCAST_FEATURES = [
    "deliv_now",      # nowcast of today's delivery %
    "deliv_now_vs20", # nowcast minus the stock's own 20-day average (to yesterday) = the estimated surprise
]
EXTRA = bool(cfg().get("extra_features"))
NOWCAST = EXTRA and bool(cfg().get("delivery_nowcast"))
if NOWCAST and int(cfg().get("alt_lag", 0) or 0) < 1:
    raise SystemExit("delivery_nowcast: true needs alt_lag: 1 (the nowcast stands in for the day not yet published)")
# Multi-day opening gaps (config: gap_features: true - the ALT-GAP model, config_alt_gap.yaml). Row t = the day the
# forecast is made: today's open against each of the last 20 closes and the last 20 opens (log ratios, split-adjusted
# prices). Today's open vs yesterday's close is the existing "gap" feature, so the close series starts at 2 days.
GAP_DAYS = 20
GAP_FEATURES = ([f"gap_c{k}" for k in range(2, GAP_DAYS + 1)]       # log(open today / close k sessions ago)
                + [f"gap_o{k}" for k in range(1, GAP_DAYS + 1)])    # log(open today / open k sessions ago)
GAPS = bool(cfg().get("gap_features"))
# Linked columns (config: link_features: true - the ALT-LINK model, config_alt_link.yaml, built on ALT-CLOSE): 12 hand
# links around delivery (LK.LINK_HAND) + 2 columns per pair of the mined "system map" (frozen in the work folder's
# link_map.json, drawn by the first build). See nseml/links.py.
LINKS = EXTRA and bool(cfg().get("link_features"))
LINK_PAIRS = LK.load_pairs() if LINKS else []
# Intraday inputs (config: intraday_features: true - the ALT-INTRADAY model, config_alt_intraday.yaml, built on ALT):
# how day t traded, from NSEDATAENGINE's intraday_daily (1-minute archive) - realized volatility and its parts, returns
# of the first / middle / last part of the session, the official close vs the last trade and vs the day's VWAP, volume
# shares and buying pressure (data.INTRADAY_COLS), + id_rv5_sig = ln(5-minute realized volatility / sigma). Known after
# the 19:05 minute-archive task: a NIGHTLY input only. Null for stocks without minute data and days with ok = false.
INTRADAY = bool(cfg().get("intraday_features"))
INTRADAY_FEATURES = data.INTRADAY_COLS + ["id_rv5_sig"]
FEATURES = (STOCK_FEATURES + MARKET_FEATURES + CS_FEATURES + (ALT_FEATURES if EXTRA else [])
            + (NOWCAST_FEATURES if NOWCAST else []) + (GAP_FEATURES if GAPS else [])
            + (LK.LINK_HAND + LK.pair_names(LINK_PAIRS) if LINKS else [])
            + (INTRADAY_FEATURES if INTRADAY else []))
TARGETS = ["y_g", "y_r", "y_u", "y_d", "y_v"]
SCALED = {"y_g": "y_g_n", "y_r": "y_r_n", "y_u": "y_u_n", "y_d": "y_d_n", "y_v": "y_v"}   # model targets
KEEP = ["company_id", "datetime", "open", "high", "low", "close", "volume", "vol_sma20", "next_datetime",
        "o_next", "h_next", "l_next", "c_next", "v_next"]


def market_features(mk: pl.DataFrame) -> pl.DataFrame:
    r = lambda c, k=1: (pl.col(c) / pl.col(c).shift(k)).log()
    out = mk.sort("datetime").with_columns(
        nifty_r1=r("nifty_close"), nifty_r5=r("nifty_close", 5), nifty_r20=r("nifty_close", 20),
        vix_level=pl.col("vix") / 100, vix_chg=r("vix"),
        sp500_r1=r("sp500"), sp500_r5=r("sp500", 5), ftse100_r1=r("ftse100"), dax_r1=r("dax"),
        nikkei225_r1=r("nikkei225"), crude_oil_r1=r("crude_oil"), gold_r1=r("gold"), usd_inr_r1=r("usd_inr"),
        next_market_day=pl.col("datetime").shift(-1),
    ).with_columns(nifty_vol20=pl.col("nifty_r1").rolling_std(20))
    return out.select(["datetime", "next_market_day"] + MARKET_FEATURES)


def stock_features(d: pl.DataFrame) -> pl.DataFrame:
    """d: raw rows of a set of companies (sorted by company, date) joined with market_features.
    Returns KEEP + STOCK_FEATURES + market features + targets + universe columns."""
    c0 = cfg()
    g = lambda e: e.over("company_id")
    o, h, l, c = (pl.col(x) for x in ("open", "high", "low", "close"))
    v = pl.col("volume").cast(pl.Float64)
    vpos = pl.when(v > 0).then(v)                                   # volume 0 -> null (no trade)
    c1 = g(c.shift(1))
    hl = h - l
    d = d.with_columns(
        lr=(c / c1).log(), gap=(o / c1).log(), hr=(h / c1).log(), lrr=(l / c1).log(),
        log_co=(c / o).log(), log_hl=(h / l).log(),
        clv=pl.when(hl > 0).then((c - l) / hl - 0.5).otherwise(0.0),
        uw=(h / pl.max_horizontal(o, c)).log(), lw=(pl.min_horizontal(o, c) / l).log(),
        tr=pl.max_horizontal(hl, (h - c1).abs(), (l - c1).abs()),
        tv=c * v, vol_sma20=g(vpos.rolling_mean(20, min_samples=10)),
        vol_sma60=g(vpos.rolling_mean(60, min_samples=30)), vol_sma5=g(vpos.rolling_mean(5, min_samples=3)),
        sma20=g(c.rolling_mean(20)), sma50=g(c.rolling_mean(50)), std20=g(c.rolling_std(20)),
        hi20=g(h.rolling_max(20)), lo20=g(l.rolling_min(20)), hi14=g(h.rolling_max(14)), lo14=g(l.rolling_min(14)),
        ema12=g(c.ewm_mean(span=12, adjust=False)), ema26=g(c.ewm_mean(span=26, adjust=False)),
        obv=g((v * c.diff().sign().fill_null(0)).cum_sum()),
        n_hist=g(pl.int_range(pl.len(), dtype=pl.Int32)),
        dc=g(c.diff()),
        # next day (targets)
        next_datetime=g(pl.col("datetime").shift(-1)),
        o_next=g(o.shift(-1)), h_next=g(h.shift(-1)), l_next=g(l.shift(-1)), c_next=g(c.shift(-1)),
        v_next=g(v.shift(-1)),
    )
    k = lambda n: (c / g(c.shift(n))).log()
    d = d.with_columns(
        sigma=g(pl.col("lr").pow(2).ewm_mean(half_life=c0["targets"]["vol_halflife"], adjust=False, ignore_nulls=True)
                .sqrt()).clip(lower_bound=c0["targets"]["vol_floor"]),
        r5=k(5), r10=k(10), r20=k(20), r60=k(60), r120=k(120),
        atr_rs=g(pl.col("tr").ewm_mean(alpha=1 / 14, adjust=False)),
        vol5=g(pl.col("lr").rolling_std(5)), vol20=g(pl.col("lr").rolling_std(20)),
        park20=(g(pl.col("log_hl").pow(2).rolling_mean(20)) / LN2X4).sqrt(),
        gap_abs20=g(pl.col("gap").abs().rolling_mean(20)), uw20=g(pl.col("uw").rolling_mean(20)),
        lw20=g(pl.col("lw").rolling_mean(20)), range20=g(pl.col("log_hl").rolling_mean(20)),
        rel_vol=(vpos / pl.col("vol_sma20")).log(), rel_vol_5_60=(pl.col("vol_sma5") / pl.col("vol_sma60")).log(),
        log_tv=pl.when(pl.col("tv") > 0).then(pl.col("tv").log()),
        med_tv60=g(pl.col("tv").rolling_median(60, min_samples=40)),
        obv_slope20=g(pl.col("obv") - pl.col("obv").shift(20)) / (20 * pl.col("vol_sma20")),
        skew20=g(pl.col("lr").rolling_skew(20)), kurt20=g(pl.col("lr").rolling_kurtosis(20)),
        c_sma20=(c / pl.col("sma20")).log(), c_sma50=(c / pl.col("sma50")).log(),
        sma20_sma50=(pl.col("sma20") / pl.col("sma50")).log(),
        zscore20=(c - pl.col("sma20")) / pl.col("std20"),
        stoch14=pl.when(pl.col("hi14") > pl.col("lo14"))
                  .then((c - pl.col("lo14")) / (pl.col("hi14") - pl.col("lo14")) - 0.5),
        macd_line=pl.col("ema12") - pl.col("ema26"),
        gain=g(pl.col("dc").clip(lower_bound=0).ewm_mean(alpha=1 / 14, adjust=False)),
        loss=g((-pl.col("dc")).clip(lower_bound=0).ewm_mean(alpha=1 / 14, adjust=False)),
        beta60=g(pl.rolling_cov(pl.col("lr"), pl.col("nifty_r1"), window_size=60, min_samples=40)
                 / pl.col("nifty_r1").rolling_var(60, min_samples=40)),
        corr60=g(pl.rolling_corr(pl.col("lr"), pl.col("nifty_r1"), window_size=60, min_samples=40)),
    )
    d = d.with_columns(
        lr_z=pl.col("lr") / pl.col("sigma"), r5_z=pl.col("r5") / (pl.col("sigma") * 5 ** 0.5),
        r20_z=pl.col("r20") / (pl.col("sigma") * 20 ** 0.5),
        atr14=pl.col("atr_rs") / c,
        dist_hi20=(c - pl.col("hi20")) / pl.col("atr_rs"), dist_lo20=(c - pl.col("lo20")) / pl.col("atr_rs"),
        vol_ratio=(pl.col("vol5") / pl.col("vol20")).log(),
        log_med_tv60=pl.when(pl.col("med_tv60") > 0).then(pl.col("med_tv60").log()),
        macd=pl.col("macd_line") / c,
        macd_hist=(pl.col("macd_line") - g(pl.col("macd_line").ewm_mean(span=9, adjust=False))) / c,
        rsi14=pl.when(pl.col("gain") + pl.col("loss") > 0)
                .then(pl.col("gain") / (pl.col("gain") + pl.col("loss")) - 0.5),
        resid1=pl.col("lr") - pl.col("beta60") * pl.col("nifty_r1"),
        resid5=pl.col("r5") - pl.col("beta60") * pl.col("nifty_r5"),
        listing_age=(pl.col("n_hist").cast(pl.Float64) + 1).log(),
        dow=pl.col("datetime").dt.weekday(), dom=pl.col("datetime").dt.day(),
        days_to_month_end=(pl.col("datetime").dt.month_end() - pl.col("datetime")).dt.total_days(),
    )
    if EXTRA:
        d = alt_features(d, g, c, v)
    if GAPS:
        d = d.with_columns([(o / g(c.shift(k))).log().alias(f"gap_c{k}") for k in range(2, GAP_DAYS + 1)]
                           + [(o / g(o.shift(k))).log().alias(f"gap_o{k}") for k in range(1, GAP_DAYS + 1)])
    if LINKS:
        d = LK.hand_links(d, g, NOWCAST, int(c0.get("alt_lag", 0) or 0))
    if INTRADAY:                  # today's 5-minute realized volatility against the stock's usual daily volatility
        d = d.with_columns(id_rv5_sig=pl.when((pl.col("id_rv5") > 0) & (pl.col("sigma") > 0))
                           .then((pl.col("id_rv5") / pl.col("sigma")).log()))
    # ---- targets: day t+1 relative to day t ----------------------------------------------------------
    hi_oc = pl.max_horizontal("o_next", "c_next")
    lo_oc = pl.min_horizontal("o_next", "c_next")
    # t+1 must be the stock's next trading day (no suspension gap) with trades. A next-day move beyond
    # max_abs_log (default 0.4 = -33% / +49%) is almost always an unadjusted corporate action or a bad print
    # in the source data; such rows get no target (counted in the build log).
    mx = c0["targets"]["max_abs_log"]
    ok = ((pl.col("next_datetime") == pl.col("next_market_day")) & (pl.col("v_next") > 0)
          & ((pl.col("o_next") / c).log().abs() <= mx) & ((pl.col("c_next") / c).log().abs() <= mx)
          & ((pl.col("h_next") / pl.col("l_next")).log() <= mx))
    d = d.with_columns(
        y_g=pl.when(ok).then((pl.col("o_next") / c).log()),
        y_r=pl.when(ok).then((pl.col("c_next") / c).log()),
        y_u=pl.when(ok).then((pl.col("h_next") / hi_oc).log()),
        y_d=pl.when(ok).then((lo_oc / pl.col("l_next")).log()),
        y_v=pl.when(ok).then((pl.col("v_next") / pl.col("vol_sma20")).log()),
    ).with_columns([(pl.col(t) / pl.col("sigma")).alias(n) for t, n in SCALED.items() if t != n])
    # ---- daily universe (information up to t only) ---------------------------------------------------
    u = c0["universe"]
    d = d.with_columns(in_universe=(pl.col("med_tv60") >= u["min_median_turnover"]) & (c >= u["min_price"])
                       & (pl.col("n_hist") >= u["min_history"]) & (v > 0))
    num = (STOCK_FEATURES + MARKET_FEATURES + (ALT_FEATURES if EXTRA else []) + (NOWCAST_FEATURES if NOWCAST else [])
           + (GAP_FEATURES if GAPS else []) + (LK.LINK_HAND if LINKS else []) + (INTRADAY_FEATURES if INTRADAY else [])
           + TARGETS + list(SCALED.values()))
    d = d.with_columns([pl.when(pl.col(x).is_finite()).then(pl.col(x)).cast(F32).alias(x)
                        for x in dict.fromkeys(num) if x not in ("is_fno", "dow", "dom", "days_to_month_end")])
    return d.select(list(dict.fromkeys(KEEP + ["in_universe"] + num)))


def alt_features(d: pl.DataFrame, g, c, v) -> pl.DataFrame:
    """ALT_FEATURES from the joined a_* columns (null where the source has no data yet)."""
    zero = [pl.when(pl.col(k).is_null() & pl.col("datetime").dt.date().is_between(lo, hi)).then(0.0)
            .otherwise(pl.col(k)).alias(k) for k, (lo, hi) in data.RANGES.items() if k in d.columns]
    if zero:                      # inside a source's date range, "not listed" means none (no short sale / no MTF)
        d = d.with_columns(zero)
    lag = int(cfg().get("alt_lag", 0) or 0)                     # delivery + short selling (published ~18:00 on day t)
    lag_m = int(cfg().get("alt_lag_mtf", lag) or 0)             # MTF (already on its publication day; see config)
    sh_cols = [(k, lag) for k in ("a_traded_qty", "a_deliv_qty", "a_short_qty")] + \
              [(k, lag_m) for k in ("a_mtf_qty", "a_mtf_amt")]
    shifts = [g(pl.col(k).shift(n)) for k, n in sh_cols if n and k in d.columns]
    if shifts:                    # alt_lag 1 = N-1: row t only sees the previous session's values (usable at 15:37)
        d = d.with_columns(shifts)
    vq = pl.coalesce(pl.col("a_traded_qty"), v)      # traded qty of the SAME day as the alt data (lag-safe)
    sh = pl.col("a_short_qty")
    mq = pl.col("a_mtf_qty")
    d = d.with_columns(
        deliv_pct=pl.when(pl.col("a_traded_qty") > 0).then(pl.col("a_deliv_qty") / pl.col("a_traded_qty")),
        short_pct=pl.when(vq > 0).then(sh / vq),
    )
    dp, sp = pl.col("deliv_pct"), pl.col("short_pct")
    return d.with_columns(
        deliv_z20=(dp - g(dp.rolling_mean(20, min_samples=10))) / g(dp.rolling_std(20, min_samples=10)),
        deliv_5_20=(g(dp.rolling_mean(5, min_samples=3)) / g(dp.rolling_mean(20, min_samples=10))).log(),
        deliv_val_tv=pl.when((pl.col("a_deliv_qty") > 0) & (pl.col("med_tv60") > 0))
                       .then((pl.col("a_deliv_qty") * c / pl.col("med_tv60")).log()),
        short_5d_pct=g(sh.rolling_sum(5, min_samples=3)) / g(vq.rolling_sum(5, min_samples=3)),
        short_z60=(sp - g(sp.rolling_mean(60, min_samples=20))) / g(sp.rolling_std(60, min_samples=20)),
        mtf_days=(1 + mq / pl.col("vol_sma20")).log(),
        mtf_chg5=((mq + 1) / (g(mq.shift(5)) + 1)).log(),
        mtf_amt_tv=pl.col("a_mtf_amt") * 1e5 / pl.col("med_tv60"),
    ).pipe(lambda x: x.with_columns(
        deliv_now=pl.col("a_dp_now"),
        deliv_now_vs20=pl.col("a_dp_now") - g(pl.col("deliv_pct").rolling_mean(20, min_samples=10)),
    ) if NOWCAST else x)


def cross_sectional(d: pl.DataFrame) -> pl.DataFrame:
    """Percentile rank of each source column among the stocks in the universe that day, centred on 0."""
    return d.with_columns([((pl.col(s).rank("average") / pl.col(s).count()).over("datetime") - 0.5)
                           .cast(F32).alias(f"cs_{s}") for s in CS_SOURCES])


def link_candidates() -> list[str]:
    """Inputs the system map may pair: per-stock inputs only. Market-wide inputs are the same for every stock on a day
    (their cross-sectional rank says nothing), cs_ columns are ranks of inputs already listed, calendar is not a level."""
    skip = set(MARKET_FEATURES) | set(CS_FEATURES) | {"is_fno", "dow", "dom", "days_to_month_end"}
    return [f for f in FEATURES if f not in skip and not f.startswith(LK.PREFIXES)]


def _use_pairs(pairs) -> None:
    """A map drawn inside this process: its columns join FEATURES in place (other modules hold the same list)."""
    LINK_PAIRS[:] = pairs
    FEATURES[:] = [f for f in FEATURES if not f.startswith(("lp_", "ld_"))] + LK.pair_names(pairs)


def build(batch_size: int = 300, companies: list[str] | None = None, start: str | None = None,
          warmup_start: str | None = None, out=None, log=print, end=None,
          extra_raw: pl.DataFrame | None = None, extra_market: dict | None = None,
          live_close: bool = False) -> pl.DataFrame:
    """Build the full modelling dataset and write it to work\\dataset_v2.parquet (or `out`).
    extra_raw / extra_market: today's bars + index closes taken right after the close (at-close forecast), appended to
    NSEDATAENGINE's history; the F&O flag of the newest listed day is carried to that day."""
    import datetime as dt
    t0 = time.time()
    c0 = cfg()
    raw = data.load_raw(warmup_start, companies, end)
    alt = data.load_alt(log) if EXTRA else None
    intr = data.load_intraday(end, log) if INTRADAY else None
    if INTRADAY and live_close:
        # at-close: today's intraday row must already be in intraday_daily - the 15:37 task runs the intraday
        # prefetch (nsealgo prefetch-today) and NSEDATAENGINE's nse_intraday.py daily first, so it is there.
        # Fail loudly (with the fix) if it is missing, rather than silently forecasting without today's intraday.
        at = extra_raw["datetime"].min() if (extra_raw is not None and extra_raw.height) else None
        at_d = (at.date() if hasattr(at, "date") else at) if at is not None else None
        n_today = intr.filter(pl.col("datetime").dt.date() == at_d).height if (at_d is not None and intr is not None) else 0
        if n_today == 0:
            raise SystemExit("ALT-INTRADAY at-close: no intraday row for this date yet - run the intraday prefetch "
                             "(nsealgo prefetch-today) and NSEDATAENGINE's 'python nse_intraday.py daily' first")
    last_official = raw["datetime"].max()          # newest day NSEDATAENGINE has (before any 15:30 snapshot bar)
    if extra_raw is not None and extra_raw.height:
        day = extra_raw["datetime"].min()
        raw = (pl.concat([raw.filter(pl.col("datetime") < day), extra_raw.select(raw.columns).cast(raw.schema)])
               .sort("company_id", "datetime"))
    if NOWCAST:                                     # today's delivery % estimate (+ out-of-sample history)
        from . import nowcast
        nc = nowcast.nowcast_for(raw, log, allow_train=not live_close, last_official=last_official)
        alt = alt if nc is None else (nc if alt is None else alt.join(nc, on=["company_id", "datetime"], how="full",
                                                                       coalesce=True))
    mk = market_features(data.load_market(end, extra=extra_market))
    fno = data.load_fno()
    if extra_raw is not None and extra_raw.height:
        last_f = fno["datetime"].max()
        if last_f < day:
            fno = pl.concat([fno, fno.filter(pl.col("datetime") == last_f).with_columns(pl.lit(day).cast(fno["datetime"].dtype).alias("datetime"))])
    fno_from = data.first_fno_date()
    ids = raw["company_id"].unique().sort().to_list()
    keep_from = dt.datetime.fromisoformat(start or c0["data"]["start"])
    parts = []
    for i in range(0, len(ids), batch_size):
        b = (raw.filter(pl.col("company_id").is_in(ids[i:i + batch_size]))
                .join(mk, on="datetime", how="left")
                .join(fno, on=["company_id", "datetime"], how="left")
                .pipe(lambda x: x.join(alt, on=["company_id", "datetime"], how="left") if alt is not None else x)
                .pipe(lambda x: x.join(intr, on=["company_id", "datetime"], how="left") if intr is not None else x)
                .with_columns(is_fno=pl.when(pl.col("datetime") >= fno_from)
                              .then(pl.col("is_fno").fill_null(0)).cast(pl.Int8))
                .sort("company_id", "datetime"))
        f = stock_features(b).filter(pl.col("in_universe"), pl.col("datetime") >= keep_from)
        parts.append(f)
        log(f"  features: companies {i + 1}-{min(i + batch_size, len(ids))} of {len(ids)}, rows kept {f.height:,}"
            f" ({time.time() - t0:.0f}s)")
    del raw
    d = cross_sectional(pl.concat(parts).sort("datetime", "company_id"))
    if LINKS:
        if not LINK_PAIRS:
            if live_close:
                raise SystemExit(f"ALT-LINK: no link map yet ({LK.map_path()}) - the nightly build draws it first")
            log("link map: none yet - drawing it once, from the first test quarter's training window")
            _use_pairs(LK.mine(d, link_candidates(), log))
        d = LK.add_pairs(d, LINK_PAIRS)
    d = d.select(KEEP + FEATURES + TARGETS + [v for k, v in SCALED.items() if k != v])
    if out is not False:
        p = out or dataset_path()
        tmp = Path(str(p) + ".tmp")                   # write aside, then swap in: a reader never sees half a file
        d.write_parquet(tmp, compression="zstd", row_group_size=200_000)
        os.replace(tmp, p)
        log(f"dataset: {d.height:,} rows, {d['company_id'].n_unique()} stocks, {d['datetime'].n_unique()} days, "
            f"{len(FEATURES)} features -> {p} ({time.time() - t0:.0f}s)")
    return d
