"""Market fragility from the eigen-structure of returns: an early-warning overlay for swing risk.

Tightening cross-asset correlation is the common thread in the random-matrix and topological crash-warning
literature. Two measures on a rolling 60-session window of the universe's daily log returns:
- absorption ratio: share of variance in the top fifth of eigenvectors (Kritzman, Li, Page & Rigobon 2011),
  judged by its standardised shift (15-session mean vs 1-year mean, in 1-year standard deviations);
- minimum-spanning-tree length on correlation distances, which is the H0 persistence a Vietoris-Rips (TDA)
  computation reduces to; it shrinks as the market contracts (Onnela et al. 2003). Reported, not gated.
fragile = absorption shift > threshold. action: "report" (default: logged, visible to strategies via
ctx.fragility()), "halve" (new swing entries at half size) or "block" (no new swing entries).
The evidence is mostly from US markets: forward-test the overlay before letting it cut risk.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import math

import numpy as np
import pandas as pd


DEFAULTS = {"enabled": True, "universe": "nifty50", "window": 60, "ar_frac": 0.2, "short": 15, "long": 252,
            "threshold": 1.0, "action": "report", "min_names": 10}


CHUNK = 256                                        # windows per batch (bounds memory for large universes)


def series(cfg: dict, closes: pd.DataFrame, compute=None) -> pd.DataFrame:
    """Absorption ratio and tree length for every date that ends a full window (index = window end).
    Windows with the same valid stocks are batched and run on the CPU or the GPU, whichever matched and measured
    faster on this machine (nsealgo.compute); results equal the per-window definitions."""
    from numpy.lib.stride_tricks import sliding_window_view
    from ..compute import get_compute
    from ..compute.kernels import mst_summary
    c = {**DEFAULTS, **(cfg or {})}
    w, mn, frac = int(c["window"]), int(c["min_names"]), float(c["ar_frac"])
    R = np.log(closes.astype(float)).diff().iloc[1:]
    vals = R.to_numpy(dtype=float)
    if len(vals) < w or vals.shape[1] == 0:
        return pd.DataFrame(columns=["ar", "tree_length"], index=pd.DatetimeIndex([]), dtype=float)
    views = sliding_window_view(vals, w, axis=0)       # (windows, stocks, w): no copy
    groups: dict[bytes, tuple] = {}
    for i in range(views.shape[0]):
        win = views[i].T
        cols = np.flatnonzero(np.isfinite(win).all(axis=0))
        if len(cols) < mn:
            continue
        cols = cols[win[:, cols].std(0) > 0]
        if len(cols) < mn:
            continue
        groups.setdefault(cols.tobytes(), (cols, []))[1].append(i)
    comp = compute or get_compute()
    ar, tl = {}, {}
    for cols, idx in groups.values():
        for s in range(0, len(idx), CHUNK):
            part = idx[s:s + CHUNK]
            X = np.stack([views[i].T[:, cols] for i in part])
            out = comp.run("fragility", X, frac=frac)
            lengths = mst_summary(out["edges_sorted"])[0] if len(cols) >= 3 else np.full(len(part), np.nan)
            for i, a_, t_ in zip(part, out["ar"], lengths):
                ar[i], tl[i] = float(a_), float(t_)
    order = sorted(ar)
    return pd.DataFrame({"ar": [ar[i] for i in order], "tree_length": [tl[i] for i in order]},
                        index=pd.DatetimeIndex([R.index[i + w - 1] for i in order]))


def evaluate(cfg: dict, ser: pd.DataFrame) -> dict:
    """State as of the last row of `ser` (rows must end at or before the decision's previous session)."""
    c = {**DEFAULTS, **(cfg or {})}
    out = {"fragile": False, "ar": math.nan, "ar_shift": math.nan, "tree_length": math.nan}
    short, long_ = int(c["short"]), int(c["long"])
    if len(ser) < short + 20:
        return out
    ar = ser["ar"]
    base = ar.iloc[-long_:]
    sd = float(base.std())
    shift = (float(ar.iloc[-short:].mean()) - float(base.mean())) / sd if sd > 0 else math.nan
    out.update(ar=round(float(ar.iloc[-1]), 4), ar_shift=round(shift, 3) if math.isfinite(shift) else math.nan,
               tree_length=round(float(ser["tree_length"].iloc[-1]), 4),
               fragile=bool(math.isfinite(shift) and shift > float(c["threshold"])))
    return out


def build_timeline(cfg: dict, closes: pd.DataFrame, days) -> dict[str, dict]:
    """Fragility state for every session in `days`, computed from data up to the previous session."""
    ser = series(cfg, closes)
    out = {}
    for d in days:
        i = ser.index.searchsorted(pd.Timestamp(d), side="left")      # rows strictly before the session
        if i > 0:
            out[d.isoformat()] = evaluate(cfg, ser.iloc[:i])
    return out
