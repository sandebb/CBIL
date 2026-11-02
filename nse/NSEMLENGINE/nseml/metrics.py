"""Scores for quantile forecasts of the five next-day targets.

Predictions frame: company_id, datetime + for each model target T (y_g_n, y_r_n, y_u_n, y_d_n, y_v) the
columns T_q10, T_q50, T_q90 (model units: sigma units for g/r/u/d, log relative volume for v).
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import math

import numpy as np
import polars as pl

from .features import SCALED

MODEL_TARGETS = list(SCALED.values())


def qcol(t: str, q: float) -> str:
    return f"{t}_q{int(round(q * 100)):02d}"


def pinball(y: np.ndarray, p: np.ndarray, q: float) -> float:
    e = y - p
    return float(np.mean(np.maximum(q * e, (q - 1) * e)))


def evaluate(pred: pl.DataFrame, actual: pl.DataFrame, quantiles: list[float]) -> dict:
    """pred joined to actual (dataset rows) on company_id, datetime. Returns a flat dict of scores."""
    d = actual.join(pred, on=["company_id", "datetime"], how="inner").filter(pl.col("y_r").is_not_null())
    out = {"rows": d.height, "days": d["datetime"].n_unique()}
    lo, hi = qcol("x", min(quantiles))[1:], qcol("x", max(quantiles))[1:]
    for t in MODEL_TARGETS:
        dt_ = d.filter(pl.col(t).is_finite())
        y = dt_[t].to_numpy().astype(np.float64)
        losses = [pinball(y, dt_[qcol(t, q)].to_numpy().astype(np.float64), q) for q in quantiles]
        out[f"{t}.pinball"] = float(np.mean(losses))
        for q in quantiles:     # calibration: share of outcomes below each predicted quantile (ideal = q)
            out[f"{t}.below_q{int(q * 100):02d}"] = float(np.mean(y <= dt_[qcol(t, q)].to_numpy()))
        out[f"{t}.coverage"] = float(np.mean((y >= dt_[t + lo].to_numpy()) & (y <= dt_[t + hi].to_numpy())))
    # direction / ranking of the close-to-close return (P50)
    ic = (d.group_by("datetime").agg(pl.corr(pl.col(qcol("y_r_n", 0.5)).rank(), pl.col("y_r").rank()).alias("ic"))
           ["ic"].drop_nulls().drop_nans().to_numpy())
    out["y_r.rank_ic"] = float(ic.mean()) if len(ic) else float("nan")
    out["y_r.rank_ic_t"] = float(ic.mean() / ic.std() * math.sqrt(len(ic))) if len(ic) > 2 and ic.std() > 0 else float("nan")
    # the same ranking against the open -> close return (what a trade entered at the next open earns)
    # and the gap ranking (P50 of the gap vs the realised gap)
    for key, pc, yc in (("oc.rank_ic", "y_r_n", (pl.col("c_next") / pl.col("o_next")).log()),
                        ("y_g.rank_ic", "y_g_n", pl.col("y_g"))):
        v = (d.with_columns(_y=yc).group_by("datetime")
              .agg(pl.corr(pl.col(qcol(pc, 0.5)).rank(), pl.col("_y").rank()).alias("ic"))["ic"]
              .drop_nulls().drop_nans().to_numpy())
        out[key] = float(v.mean()) if len(v) else float("nan")
    p50 = d[qcol("y_r_n", 0.5)].to_numpy()
    yr = d["y_r"].to_numpy()
    out["y_r.hit_rate"] = float(np.mean(np.sign(p50[yr != 0]) == np.sign(yr[yr != 0]))) if np.any(p50 != 0) else float("nan")
    # relative volume (P50) - R squared
    dv = d.filter(pl.col("y_v").is_finite())
    yv, pv = dv["y_v"].to_numpy(), dv[qcol("y_v", 0.5)].to_numpy()
    out["y_v.r2"] = float(1 - np.sum((yv - pv) ** 2) / np.sum((yv - yv.mean()) ** 2))
    # rupee OHLC rebuilt from the medians: mean absolute % error vs the actual next-day bar
    s, c = d["sigma"].to_numpy().astype(np.float64), d["close"].to_numpy()
    g, r = d[qcol("y_g_n", .5)].to_numpy() * s, d[qcol("y_r_n", .5)].to_numpy() * s
    u, dn = np.maximum(d[qcol("y_u_n", .5)].to_numpy(), 0) * s, np.maximum(d[qcol("y_d_n", .5)].to_numpy(), 0) * s
    O, Cn = c * np.exp(g), c * np.exp(r)
    H, L = np.maximum(O, Cn) * np.exp(u), np.minimum(O, Cn) * np.exp(-dn)
    for name, p, a in (("open", O, "o_next"), ("high", H, "h_next"), ("low", L, "l_next"), ("close", Cn, "c_next")):
        out[f"ohlc.mape_{name}"] = float(np.mean(np.abs(p / d[a].to_numpy() - 1)) * 100)
    # buy-limit fill: a limit at the low rebuilt with the d-quantile q (others at P50) fills if L[t+1] <= limit
    for q in quantiles:
        dq = np.maximum(d[qcol("y_d_n", q)].to_numpy(), 0) * s
        lim = np.minimum(O, Cn) * np.exp(-dq)
        out[f"fill.low_d{int(q * 100):02d}"] = float(np.mean(d["l_next"].to_numpy() <= lim))
    return out
