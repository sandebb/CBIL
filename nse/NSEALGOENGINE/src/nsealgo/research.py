"""Research tools behind `nsealgo anatomy`, `leadlag` and `fractal` (plain, testable functions)."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import math

import numpy as np
import pandas as pd

from .core.stats import (bar_anatomy, cornish_fisher_quantile, edgeworth_valid, exceed_probability, intraday_variance_ratio,
                         mfdfa, moments, transfer_entropy)

EVENTS = {"gap": ("gap <= -{m}", -1, "below"), "upper": ("high >= open +{m}", 1, "above"),
          "lower": ("low <= open -{m}", 1, "above"), "drift": ("close > open", 0, "above")}


def anatomy(daily: pd.DataFrame, move: float = 0.01) -> dict:
    """Distribution of each bar component with the empirical rate of a `move`-sized event next to the
    Edgeworth estimate (None where the expansion is not a valid distribution)."""
    a = bar_anatomy(daily)
    rows = {}
    for comp, (label, sign, side) in EVENTS.items():
        x = a[comp].to_numpy()
        pr = exceed_probability(x, sign * move, side)
        rows[comp] = {**moments(x), "event": label.format(m=f"{move:.1%}"), "p_empirical": pr["empirical"],
                      "p_edgeworth": pr["edgeworth"]}
    lo = a["lower"].to_numpy()
    mo = moments(lo)
    cf = (mo["mean"] + mo["std"] * float(cornish_fisher_quantile(0.98, mo["skew"], mo["exkurt"]))
          if edgeworth_valid(mo["skew"], mo["exkurt"]) else None)
    return {"sessions": len(a), "end": str(a.index[-1].date()) if len(a) else None, "components": rows,
            "stop_noise_98": {"empirical": float(np.quantile(lo, 0.98)) if len(lo) else math.nan, "cornish_fisher": cf}}


def _session_returns(close: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """1-minute log returns within sessions (each session's first bar, which carries the gap, dropped)."""
    days = np.asarray(close.index.date)
    r = np.log(close.to_numpy(dtype=float))
    keep = days[1:] == days[:-1]
    return np.diff(r)[keep], days[1:][keep]


def lead_lag(lead: pd.DataFrame, follow: pd.DataFrame, lags=(1, 2, 3, 5), bins: int = 3, n_shuffle: int = 200) -> dict:
    """Does `lead` carry information about `follow`'s next minutes beyond follow's own past? Transfer entropy in
    both directions at each lag (shuffle-surrogate p-values) and the lagged correlation, on aligned 1-minute
    returns within sessions. Several lags x two directions are tested, so compare p with the Bonferroni level."""
    df = pd.concat([lead["close"], follow["close"]], axis=1, keys=["x", "y"]).dropna()
    if len(df) < 500:
        return {"n": len(df), "rows": [], "note": "need at least 500 aligned 1-minute bars (fetch more minute history)"}
    rx, g = _session_returns(df["x"])
    ry, _ = _session_returns(df["y"])
    rows = []
    for lag in lags:
        xy = transfer_entropy(rx, ry, int(lag), bins, g, n_shuffle)
        yx = transfer_entropy(ry, rx, int(lag), bins, g, n_shuffle)
        rows.append({"lag_min": int(lag), "te_xy": xy["effective"], "p_xy": xy["p"], "te_yx": yx["effective"],
                     "p_yx": yx["p"], "corr_xy": xy["corr"], "corr_yx": yx["corr"]})
    return {"n": int(len(rx)), "sessions": int(len(set(g))), "rows": rows, "bonferroni": 0.05 / (2 * len(lags))}


def fractal(minute: pd.DataFrame, morning_end: str = "11:30") -> dict:
    """Trend vs mean-reversion diagnostics on 1-minute bars: variance ratios of 5-minute returns at 10/30/60-minute
    horizons (with robust z*), DFA Hurst and MF-DFA width over all sessions, and the spread of the Hurst exponent
    estimated from each single morning (09:15 to morning_end), which shows how noisy a one-morning estimate is."""
    vr = {q * 5: intraday_variance_ratio(minute, q) for q in (2, 6, 12)}
    r, g = _session_returns(minute["close"])
    mf = mfdfa(r)
    hh, m = [], minute.between_time("09:15", morning_end)
    for _, day in m.groupby(m.index.date):
        if len(day) >= 100:
            h = mfdfa(np.diff(np.log(day["close"].to_numpy(dtype=float))), qs=(2.0,))["hurst"]
            if math.isfinite(h):
                hh.append(h)
    return {"sessions": int(len(set(g))), "n": int(len(r)),
            "variance_ratio": {k: {"vr": v[0], "z": v[1]} for k, v in vr.items()},
            "hurst": mf["hurst"], "delta_h": mf["delta_h"], "delta_alpha": mf["delta_alpha"],
            "morning_hurst": {"n": len(hh), "mean": float(np.mean(hh)) if hh else math.nan,
                              "min": float(np.min(hh)) if hh else math.nan, "max": float(np.max(hh)) if hh else math.nan}}


def book_study(quotes, horizons=(5, 30, 60), levels=(1, 5, 10, 20), decay: float = 0.5, compute=None) -> pd.DataFrame:
    """How well each order-book signal predicts the mid-price move over the next h seconds, by how many depth
    levels it uses, on a recorded session (Quotes with bids/asks). Rows: signal, levels, horizon_s, ic (Spearman
    rank correlation with the forward log return of the mid, pooled over instruments), hit (sign agreement on
    non-zero moves), n. If the deep rows don't beat levels=1, the extra depth isn't paying for itself."""
    from scipy.stats import spearmanr
    from .compute import get_compute
    from .compute import kernels as K
    comp = compute or get_compute()
    by: dict[str, list] = {}
    for q in quotes:
        if q.bids and q.asks and q.bid and q.ask and q.ask > q.bid:
            by.setdefault(q.key, []).append(q)
    lmax, acc = max(levels), {}
    for qs in by.values():
        qs.sort(key=lambda q: q.ts)
        if len(qs) < 20:
            continue
        arrs = K.book_arrays(qs, lmax)
        ts = np.array([q.ts.timestamp() for q in qs])
        mid = (arrs[0][:, 0] + arrs[2][:, 0]) / 2.0
        fwd = {}
        for h in horizons:
            j = np.searchsorted(ts, ts + h, side="left")
            ok = j < len(ts)
            r = np.full(len(ts), np.nan)
            r[ok] = np.log(mid[j[ok]] / mid[ok])
            fwd[h] = r
        have = min(max(len(q.bids) for q in qs), max(len(q.asks) for q in qs))
        for L in levels:
            if L > have and L != 1:
                continue                               # not recorded this deep (feeds.record_depth)
            sub = [a[:, :L] for a in arrs]
            f = comp.run("book_features", *sub, k=L, decay=decay, bands_bps=(10.0,))
            e = comp.run("mlofi", *[a[:-1] for a in sub], *[a[1:] for a in sub])["ofi"]
            w = decay ** np.arange(L)
            dep = (w * (sub[1][1:] + sub[3][1:]) / 2.0).sum(axis=1)
            sig = {"imbalance": f["imbalance"],
                   "ofi": np.concatenate([[np.nan], (w * e).sum(axis=1) / np.maximum(dep, 1.0)])}
            if L == 1:
                sig["microprice"] = (f["microprice"] - f["mid"]) / f["mid"]
            for name, s in sig.items():
                for h in horizons:
                    a = acc.setdefault((name, L, h), ([], []))
                    a[0].append(np.asarray(s, float))
                    a[1].append(fwd[h])
    rows = []
    for (name, L, h), (ss, rr) in sorted(acc.items()):
        s, r = np.concatenate(ss), np.concatenate(rr)
        ok = np.isfinite(s) & np.isfinite(r)
        s, r = s[ok], r[ok]
        if len(s) < 30:
            continue
        ic = float(spearmanr(s, r).correlation) if s.std() > 0 and r.std() > 0 else float("nan")
        nz = (r != 0) & (s != 0)
        hit = float((np.sign(s[nz]) == np.sign(r[nz])).mean()) if nz.any() else float("nan")
        rows.append({"signal": name, "levels": L, "horizon_s": h, "ic": round(ic, 4), "hit": round(hit, 3), "n": int(len(s))})
    return pd.DataFrame(rows, columns=["signal", "levels", "horizon_s", "ic", "hit", "n"])
