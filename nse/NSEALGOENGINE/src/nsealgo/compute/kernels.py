"""Batched kernels, written once against `ops` (compute/ops.py).

Market structure (research, fragility overlay):
- fragility_windows: for a batch of return windows, the absorption ratio (top eigenvalue share of the covariance;
  Kritzman et al. 2011) and the minimum-spanning-tree edge lengths on correlation distances sqrt(2(1-rho))
  (Mantegna; Onnela et al.), via batched dense Prim's algorithm: n-1 vectorised steps for the whole batch.
Order book (Part 3 signals, research on recorded depth):
- book_features: mid, spread, microprice, depth-weighted imbalance over the first k levels, depth within
  +-x bps of mid per side and its log pressure.
- mlofi: multi-level order-flow imbalance between two snapshots (Cont, Kukanov & Stoikov 2014 at each level;
  Xu, Gould & Howison 2019).
Book arrays are (B, L), best level first; a missing level has price NaN and quantity 0.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import math

import numpy as np


# ---- market structure ------------------------------------------------------------------------------
def fragility_windows(ops, X, frac: float = 0.2):
    """X: (B, w, n) returns. Returns (absorption ratio (B,), MST edge lengths (B, n-1))."""
    w, n = X.shape[1], X.shape[2]
    Xc = X - ops.mean(X, axis=1, keepdims=True)
    C = ops.swap_last(Xc) @ Xc / (w - 1)                       # sample covariance, ddof = 1 like np.cov
    ev = ops.sort_desc(ops.eigvalsh(C))
    k = max(1, int(round(n * frac)))
    ar = ops.sum(ev[:, :k], axis=-1) / ops.sum(ev, axis=-1)
    sd = ops.sqrt(ops.diagonal(C))
    corr = C / (sd[:, :, None] * sd[:, None, :])
    return ar, mst_edges(ops, corr)


def mst_edges(ops, corr):
    """Batched dense Prim's on distances sqrt(2(1 - rho)); (B, n, n) -> (B, n-1) edge lengths. Every minimum
    spanning tree has the same multiset of edge lengths, so tie-breaking can't change the statistics."""
    D = ops.sqrt(ops.clip_min(2.0 * (1.0 - corr), 0.0))
    B, n = D.shape[0], D.shape[-1]
    rows = ops.arange(B)
    in_tree = ops.zeros_bool((B, n))
    in_tree[:, 0] = True
    best = D[:, 0, :] + 0.0
    best[in_tree] = math.inf
    edges = []
    for _ in range(n - 1):
        j = ops.argmin(best, axis=-1)
        edges.append(best[rows, j])
        in_tree[rows, j] = True
        best = ops.minimum(best, D[rows, j, :])
        best[in_tree] = math.inf
    return ops.clip_min(ops.stack(edges, axis=-1), 1e-12)      # scipy's MST counts ~0 edges as 1e-12


def mst_summary(edges: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Normalised tree length (mean edge) and persistent entropy in [0, 1] for each row of edge lengths."""
    e = np.asarray(edges, dtype=float)
    p = e / e.sum(axis=1, keepdims=True)
    ent = -(p * np.log(p)).sum(axis=1) / math.log(e.shape[1]) if e.shape[1] > 1 else np.full(len(e), np.nan)
    return e.mean(axis=1), ent


# ---- order book ------------------------------------------------------------------------------------
def book_arrays(quotes, levels: int):
    """Quotes with .bids/.asks ((price, qty[, orders]), best first) -> four (B, levels) arrays."""
    B = len(quotes)
    bp, ap = np.full((B, levels), np.nan), np.full((B, levels), np.nan)
    bq, aq = np.zeros((B, levels)), np.zeros((B, levels))
    for i, q in enumerate(quotes):
        for side, P, Q in ((q.bids, bp, bq), (q.asks, ap, aq)):
            m = min(len(side), levels)
            if m:
                a = np.asarray([lv[:2] for lv in side[:m]], dtype=float)
                P[i, :m], Q[i, :m] = a[:, 0], a[:, 1]
    return bp, bq, ap, aq


def book_features(ops, bp, bq, ap, aq, k: int = 5, decay: float = 0.5, bands_bps=(10.0, 25.0)) -> dict:
    b1, a1, qb1, qa1 = bp[:, 0], ap[:, 0], bq[:, 0], aq[:, 0]
    mid = (b1 + a1) / 2.0
    den = qb1 + qa1
    ok = den > 0
    micro = ops.where(ok, (b1 * qa1 + a1 * qb1) / ops.where(ok, den, 1.0), mid)
    kk = min(int(k), bp.shape[1])
    wts = ops.asarray(decay ** np.arange(kk))
    Wb, Wa = ops.sum(bq[:, :kk] * wts, axis=1), ops.sum(aq[:, :kk] * wts, axis=1)
    tot = Wb + Wa
    out = {"mid": mid, "spread_bps": (a1 - b1) / mid * 1e4, "microprice": micro,
           "imbalance": ops.where(tot > 0, (Wb - Wa) / ops.where(tot > 0, tot, 1.0), 0.0)}
    for x in bands_bps:
        lo, hi = mid * (1.0 - x / 1e4), mid * (1.0 + x / 1e4)
        db = ops.sum(ops.where(bp >= lo[:, None], bq, 0.0), axis=1)
        da = ops.sum(ops.where(ap <= hi[:, None], aq, 0.0), axis=1)
        out[f"depth_bid_{x:g}"], out[f"depth_ask_{x:g}"] = db, da
        out[f"pressure_{x:g}"] = ops.log((db + 1.0) / (da + 1.0))
    return out


def mlofi(ops, prev, cur):
    """Per-level order-flow imbalance between snapshots prev and cur (each (bp, bq, ap, aq)); (B, L).
    Bid side: q(t) if the level's price rose, q(t) - q(t-1) if unchanged, -q(t-1) if it fell; the ask side
    mirrors it; e = bid flow - ask flow. Levels missing in either snapshot contribute 0."""
    pbp, pbq, pap, paq = prev
    bp, bq, ap, aq = cur
    dW = ops.where(bp > pbp, bq, ops.where(bp == pbp, bq - pbq, -pbq))
    dV = ops.where(ap < pap, aq, ops.where(ap == pap, aq - paq, -paq))
    vb = ~(ops.isnan(bp) | ops.isnan(pbp))
    va = ~(ops.isnan(ap) | ops.isnan(pap))
    return ops.where(vb, dW, 0.0) - ops.where(va, dV, 0.0)
