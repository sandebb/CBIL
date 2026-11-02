"""Indicators and statistics used by the strategies and the risk engine (pure functions, no I/O)."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import math
from datetime import datetime, time

import numpy as np
import pandas as pd
from scipy.stats import norm


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def ofi_vectorized(bid, bid_qty, ask, ask_qty) -> np.ndarray:
    """Cont-Kukanov-Stoikov order-flow imbalance for a whole sequence of best-quote snapshots at once
    (element 0 is 0). Identical to BarBuilder.ofi_increment applied pairwise; use it for research on
    recorded ticks, the engine keeps the incremental form for live data."""
    b, bq, a, aq = (np.asarray(x, dtype=float) for x in (bid, bid_qty, ask, ask_qty))
    out = np.zeros(len(b))
    if len(b) < 2:
        return out
    eb = np.where(b[1:] >= b[:-1], bq[1:], 0.0) - np.where(b[1:] <= b[:-1], bq[:-1], 0.0)
    ea = np.where(a[1:] <= a[:-1], aq[1:], 0.0) - np.where(a[1:] >= a[:-1], aq[:-1], 0.0)
    out[1:] = eb - ea
    return out


compute_ofi_vectorized = ofi_vectorized   # name used in the multi-core design note


def atr_panel(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """Wilder ATR for many instruments at once (dates x symbols); same definition as atr()."""
    pc = close.shift(1)
    tr = np.fmax(high - low, np.fmax((high - pc).abs(), (low - pc).abs()))
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def logsumexp_small(a: np.ndarray, axis=None):
    """logsumexp for the tiny arrays of the regime filter (3 states): ~20x cheaper than scipy's generic one."""
    m = np.max(a, axis=axis, keepdims=True)
    out = m + np.log(np.sum(np.exp(a - m), axis=axis, keepdims=True))
    return np.squeeze(out, axis=axis) if axis is not None else float(out.reshape(-1)[0])


def rsi(s: pd.Series, n: int = 2) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    out = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return out.where(dn != 0, 100.0)


def vwap_bands(df: pd.DataFrame, k: float = 2.0):
    """Session VWAP with volume-weighted standard-deviation bands."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    v = df["volume"].clip(lower=0)
    cv = v.cumsum().replace(0, np.nan)
    vwap = (tp * v).cumsum() / cv
    var = (tp * tp * v).cumsum() / cv - vwap ** 2
    sd = np.sqrt(var.clip(lower=0))
    return vwap, vwap + k * sd, vwap - k * sd


def _days(minute_df: pd.DataFrame):
    return sorted(set(minute_df.index.date))


def noise_sigma(minute_df: pd.DataFrame, check_times: list[time], lookback: int = 14) -> dict:
    """Zarattini-Aziz-Barbon noise area: mean |price(t)/open - 1| over the last `lookback` sessions."""
    if minute_df is None or minute_df.empty:
        return {}
    res: dict = {t: [] for t in check_times}
    for d in _days(minute_df)[-lookback:]:
        g = minute_df[minute_df.index.date == d]
        if g.empty:
            continue
        o = float(g["open"].iloc[0])
        for t in check_times:
            cutoff = pd.Timestamp(datetime.combine(d, t)).tz_localize(g.index.tz) - pd.Timedelta(minutes=1)
            sub = g.loc[:cutoff]
            if len(sub) and o > 0:
                res[t].append(abs(float(sub["close"].iloc[-1]) / o - 1.0))
    return {t: float(np.mean(v)) for t, v in res.items() if v}


def window_volume(minute_df: pd.DataFrame, start: time, end: time, lookback: int = 14) -> float:
    """Average volume of bars starting in [start, end) over the last `lookback` sessions."""
    if minute_df is None or minute_df.empty:
        return float("nan")
    t = np.array([ts.time() for ts in minute_df.index])
    sel = minute_df[(t >= start) & (t < end)]
    if sel.empty:
        return float("nan")
    per_day = sel["volume"].groupby(sel.index.date).sum()
    return float(per_day.iloc[-lookback:].mean())


def cum_volume_profile(minute_df: pd.DataFrame, lookback: int = 14) -> pd.Series:
    """Minute-of-day -> average cumulative volume by then (for 'RVOL so far'). Vectorised."""
    if minute_df is None or minute_df.empty:
        return pd.Series(dtype=float)
    days = np.array(minute_df.index.date)
    keep = np.isin(days, sorted(set(days))[-lookback:])
    m = minute_df.loc[keep, "volume"]
    d = days[keep]
    mod = m.index.hour * 60 + m.index.minute
    cum = m.groupby(d).cumsum()
    wide = pd.DataFrame({"d": d, "mod": np.asarray(mod), "cum": cum.to_numpy()}).pivot_table(
        index="mod", columns="d", values="cum", aggfunc="last")
    return wide.sort_index().ffill().mean(axis=1)


def vpin(minute_df: pd.DataFrame, bucket_volume: float, window: int = 50) -> pd.Series:
    """Volume-synchronised probability of informed trading with bulk-volume classification from bars.

    Exact closed form of the bucket-filling loop: within a bar the buy fraction is constant, so cumulative
    buy volume is piecewise-linear in cumulative volume; interpolating it at the bucket boundaries gives
    every bucket's buy volume at once (O(n), no Python loop)."""
    empty = pd.Series(dtype=float)
    if minute_df is None or len(minute_df) < 3 or not bucket_volume or bucket_volume <= 0:
        return empty
    close = minute_df["close"].to_numpy(float)
    vol = np.clip(minute_df["volume"].to_numpy(float), 0.0, None)
    dp = np.diff(close, prepend=close[0])
    sd = float(np.std(dp)) or 1e-9
    bf = norm.cdf(dp / sd)
    cv = np.concatenate(([0.0], np.cumsum(vol)))
    cb = np.concatenate(([0.0], np.cumsum(vol * bf)))
    nb = int(np.floor(cv[-1] / bucket_volume + 1e-9))
    if nb < 1:
        return empty
    bounds = np.arange(nb + 1, dtype=float) * bucket_volume
    buy = np.diff(np.interp(bounds, cv, cb))
    imb = np.abs(2.0 * buy - bucket_volume) / bucket_volume
    bar = np.clip(np.searchsorted(cv, bounds[1:] - 1e-9, side="left") - 1, 0, len(vol) - 1)
    s = pd.Series(imb, index=minute_df.index[bar])
    return s.rolling(window, min_periods=max(5, window // 5)).mean().dropna()


def momentum_scores(closes: pd.DataFrame) -> pd.Series:
    """NSE-momentum-index-style composite: mean of z(6m return / vol) and z(12m return / vol)."""
    if len(closes) < 253:
        return pd.Series(dtype=float)
    last = closes.iloc[-1]
    r6 = last / closes.iloc[-127] - 1
    r12 = last / closes.iloc[-253] - 1
    vol = np.log(closes).diff().iloc[-252:].std() * np.sqrt(252)

    def z(x):
        x = x.replace([np.inf, -np.inf], np.nan)
        return (x - x.mean()) / (x.std(ddof=0) or 1.0)

    return ((z(r6 / vol) + z(r12 / vol)) / 2).dropna().sort_values(ascending=False)


def half_life(spread: pd.Series) -> float:
    s = pd.Series(spread).dropna()
    if len(s) < 30:
        return math.inf
    lag, ds = s.shift(1).iloc[1:].to_numpy(), s.diff().iloc[1:].to_numpy()
    b = np.polyfit(lag, ds, 1)[0]
    phi = 1 + b
    if b >= 0 or phi <= 0:
        return math.inf
    return float(-math.log(2) / math.log(phi))


def ols_hedge(y: np.ndarray, x: np.ndarray) -> tuple[float, float]:
    beta, alpha = np.polyfit(np.asarray(x, float), np.asarray(y, float), 1)
    return float(beta), float(alpha)


def kalman_hedge(y: pd.Series, x: pd.Series, delta: float = 1e-4, obs_var: float = 1e-3) -> pd.DataFrame:
    """Dynamic hedge ratio (beta, alpha) via a 2-state random-walk Kalman filter."""
    yv, xv = np.asarray(y, float), np.asarray(x, float)
    n0 = min(60, len(yv))
    theta = np.array(ols_hedge(yv[:n0], xv[:n0]))
    P = np.eye(2) * 1e-2
    Vw = delta / (1 - delta) * np.eye(2)
    out = np.zeros((len(yv), 2))
    for t in range(len(yv)):
        F = np.array([xv[t], 1.0])
        R = P + Vw
        e = yv[t] - F @ theta
        Q = F @ R @ F + obs_var
        K = R @ F / Q
        theta = theta + K * e
        P = R - np.outer(K, F) @ R
        out[t] = theta
    return pd.DataFrame(out, index=getattr(y, "index", None), columns=["beta", "alpha"])


def coint_pvalue(y, x) -> float:
    from statsmodels.tsa.stattools import coint
    return float(coint(np.asarray(y, float), np.asarray(x, float))[1])


def gap_quantile(daily: pd.DataFrame, q: float = 0.01, lookback: int = 750) -> float:
    """Magnitude of the q-quantile overnight gap (open vs previous close) — stops don't work through these."""
    if daily is None or len(daily) < 25:
        return 0.05
    g = (daily["open"] / daily["close"].shift(1) - 1).dropna().iloc[-lookback:]
    return float(abs(min(g.quantile(q), 0.0))) or 0.01


def expected_shortfall(pnl, alpha: float = 0.975) -> float:
    losses = -np.asarray(pnl, float)
    if losses.size == 0:
        return 0.0
    var = np.quantile(losses, alpha)
    return float(max(losses[losses >= var].mean(), 0.0))


def comomentum(closes: pd.DataFrame, market: pd.Series, top_frac: float = 0.1, weeks: int = 52) -> float:
    """Lou & Polk (2022): avg pairwise correlation of market-adjusted weekly returns among momentum winners."""
    if len(closes) < 260:
        return float("nan")
    mom = (closes.iloc[-22] / closes.iloc[-253] - 1).dropna()
    if len(mom) < 10:
        return float("nan")
    winners = mom.sort_values(ascending=False).index[: max(5, int(len(mom) * top_frac))]
    wk = closes[winners].resample("W-FRI").last().pct_change().iloc[-weeks:]
    mk = market.resample("W-FRI").last().pct_change().reindex(wk.index)
    ok = mk.notna()
    wk, mk = wk[ok].dropna(axis=1), mk[ok]
    if wk.shape[1] < 5 or len(mk) < 20:
        return float("nan")
    x = mk.to_numpy()
    resid = []
    for c in wk.columns:
        yv = wk[c].to_numpy()
        b, a = np.polyfit(x, yv, 1)
        resid.append(yv - (b * x + a))
    C = np.corrcoef(np.array(resid))
    n = C.shape[0]
    return float((np.nansum(C) - n) / (n * (n - 1)))


def round_tick(price: float, tick: float, mode: str = "nearest") -> float:
    if price is None or not tick or tick <= 0:
        return price
    q = price / tick
    n = math.floor(q + 1e-9) if mode == "down" else math.ceil(q - 1e-9) if mode == "up" else round(q)
    return round(n * tick, 4)


def supertrend(df: pd.DataFrame, period: int = 10, mult: float = 3.0) -> tuple[pd.Series, pd.Series]:
    """Supertrend (O. Seban): ATR bands around (H+L)/2 that only tighten while the trend holds; the trend flips
    when the close crosses the opposite band. Returns (line, direction +1/-1), NaN until ATR exists."""
    h, lo, c = (df[x].to_numpy(float) for x in ("high", "low", "close"))
    a = atr(df, period).to_numpy(float)
    mid = (h + lo) / 2
    ub, lb = mid + mult * a, mid - mult * a
    n = len(c)
    fu, fl, d, line = np.full(n, np.nan), np.full(n, np.nan), np.zeros(n), np.full(n, np.nan)
    started = False
    for i in range(n):
        if not np.isfinite(a[i]):
            continue
        if not started:
            fu[i], fl[i], d[i], started = ub[i], lb[i], (1.0 if c[i] >= mid[i] else -1.0), True
        else:
            fu[i] = ub[i] if (ub[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
            fl[i] = lb[i] if (lb[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
            d[i] = 1.0 if (d[i - 1] < 0 and c[i] > fu[i]) else -1.0 if (d[i - 1] > 0 and c[i] < fl[i]) else d[i - 1]
        line[i] = fl[i] if d[i] > 0 else fu[i]
    return pd.Series(line, index=df.index), pd.Series(np.where(np.isfinite(line), d, np.nan), index=df.index)


def cpr_width(df: pd.DataFrame) -> pd.Series:
    """Central Pivot Range width as a fraction of the pivot (Ochoa, 'Secrets of a Pivot Boss')."""
    p = (df["high"] + df["low"] + df["close"]) / 3
    bc = (df["high"] + df["low"]) / 2
    return (2 * p - 2 * bc).abs() / p
