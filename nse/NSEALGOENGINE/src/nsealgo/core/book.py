"""Order-book signals for instruments that stream market depth (Upstox 5/30 levels, Dhan 20).

The engine updates them on every batch of quotes; strategies read them with ctx.book(key) (None when an
instrument has no fresh depth, so every caller keeps its top-of-book fallback).
- ofi_z: multi-level order-flow imbalance (compute.kernels.mlofi) between consecutive snapshots, weighted by
  decay**level and divided by the displayed depth it is measured against; summed over `window_seconds` and
  expressed in standard deviations of its own recent history (like the scalper's top-of-book z-score).
- imbalance: depth-weighted bid/ask imbalance over the first `imbalance_levels`; microprice_bps: microprice
  minus mid; pressure: log ratio of bid to ask depth within 10 bps of mid (compute.kernels.book_features).
- absorb_ask / absorb_bid: volume that traded at the best ask (bid) while that price held, divided by the
  average size displayed there over `absorb_window_seconds`. Above ~2, far more traded than was ever shown and
  the level didn't break: hidden supply (demand), an iceberg. Snapshot feeds don't show every order, so trades
  are assigned by comparing the last price with the previous best bid/ask: an estimate, not an order log.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from ..compute import get_compute
from ..compute import kernels as K

DEFAULTS = {"levels": 50, "decay": 0.5, "imbalance_levels": 5, "window_seconds": 30.0, "history": 600,
            "absorb_window_seconds": 60.0, "max_age_seconds": 5.0, "min_levels": 2}


@dataclass
class BookState:
    ts: datetime
    levels: int
    mid: float
    spread_bps: float
    microprice_bps: float
    imbalance: float
    pressure: float
    ofi_z: float
    absorb_ask: float
    absorb_bid: float


class _Level:
    """Absorption at one side's best price: trades there vs size shown there, while the price holds."""
    __slots__ = ("px", "hits", "shown")

    def __init__(self):
        self.px, self.hits, self.shown = None, deque(), deque()

    def observe(self, now: float, px: float, qty: float, hit: float, window: float) -> None:
        if self.px is None or abs(px - self.px) > 1e-9:
            self.px, self.hits, self.shown = px, deque(), deque()   # the level broke or moved: start over
        if hit > 0:
            self.hits.append((now, hit))
        self.shown.append((now, qty))
        cut = now - window
        while self.hits and self.hits[0][0] < cut:
            self.hits.popleft()
        while self.shown and self.shown[0][0] < cut:
            self.shown.popleft()

    def score(self) -> float:
        if not self.shown:
            return 0.0
        avg = sum(q for _, q in self.shown) / len(self.shown)
        return sum(h for _, h in self.hits) / avg if avg > 0 else 0.0


def _rounds(quotes):
    """Split a batch into rounds in which each instrument appears once (snapshots stay in order)."""
    out, seen = [[]], set()
    for q in quotes:
        if q.key in seen:
            out.append([])
            seen = set()
        out[-1].append(q)
        seen.add(q.key)
    return out


class BookSignals:
    def __init__(self, cfg: dict | None = None, compute=None):
        self.p = {**DEFAULTS, **(cfg or {})}
        self.compute = compute
        self.reset()

    def reset(self) -> None:
        self.prev_rows: dict[str, tuple] = {}
        self.prev_q: dict = {}
        self.flow: dict[str, deque] = {}
        self.hist: dict[str, deque] = {}
        self.ask_lv: dict[str, _Level] = {}
        self.bid_lv: dict[str, _Level] = {}
        self.states: dict[str, BookState] = {}

    def update(self, quotes) -> None:
        mn = int(self.p["min_levels"])
        qs = [q for q in quotes if len(q.bids) >= mn and len(q.asks) >= mn and q.bid and q.ask and q.ask > q.bid]
        if not qs:
            return
        comp = self.compute or get_compute()
        L, decay = int(self.p["levels"]), float(self.p["decay"])
        w = decay ** np.arange(L)
        for rnd in _rounds(qs):
            bp, bq, ap, aq = K.book_arrays(rnd, L)
            f = comp.run("book_features", bp, bq, ap, aq, k=int(self.p["imbalance_levels"]), decay=decay,
                         bands_bps=(10.0,))
            have = [i for i, q in enumerate(rnd) if q.key in self.prev_rows]
            ofi = {}
            if have:
                prev = [np.stack([self.prev_rows[rnd[i].key][j] for i in have]) for j in range(4)]
                cur = [x[have] for x in (bp, bq, ap, aq)]
                e = comp.run("mlofi", *prev, *cur)["ofi"]
                depth = (w * (cur[1] + cur[3]) / 2.0).sum(axis=1)
                for n, i in enumerate(have):
                    ofi[rnd[i].key] = float((w * e[n]).sum() / max(depth[n], 1.0))
            for i, q in enumerate(rnd):
                self.prev_rows[q.key] = (bp[i], bq[i], ap[i], aq[i])
                self._state(q, i, f, ofi.get(q.key))

    def _state(self, q, i: int, f: dict, ofi: float | None) -> None:
        now = q.ts.timestamp()
        win, absorb_win = float(self.p["window_seconds"]), float(self.p["absorb_window_seconds"])
        fl = self.flow.setdefault(q.key, deque())
        hist = self.hist.setdefault(q.key, deque(maxlen=int(self.p["history"])))
        if ofi is not None:
            fl.append((now, ofi))
            hist.append(ofi)
        while fl and fl[0][0] < now - win:
            fl.popleft()
        sd = float(np.std(hist)) if len(hist) >= 30 else 0.0
        z = sum(x for _, x in fl) / (sd * math.sqrt(len(fl))) if sd > 0 and fl else 0.0
        pq = self.prev_q.get(q.key)
        hit_ask = hit_bid = 0.0
        if pq is not None and q.volume and pq.volume and q.volume > pq.volume and q.ltp:
            dv = float(q.volume - pq.volume)
            if pq.ask and q.ltp >= pq.ask - 1e-9 and abs(q.ask - pq.ask) <= 1e-9:
                hit_ask = dv                                          # bought at the ask, and the ask held
            elif pq.bid and q.ltp <= pq.bid + 1e-9 and abs(q.bid - pq.bid) <= 1e-9:
                hit_bid = dv                                          # sold at the bid, and the bid held
        a = self.ask_lv.setdefault(q.key, _Level())
        b = self.bid_lv.setdefault(q.key, _Level())
        a.observe(now, float(q.ask), float(q.ask_qty or 0.0), hit_ask, absorb_win)
        b.observe(now, float(q.bid), float(q.bid_qty or 0.0), hit_bid, absorb_win)
        self.prev_q[q.key] = q
        mid = float(f["mid"][i])
        self.states[q.key] = BookState(
            ts=q.ts, levels=min(len(q.bids), len(q.asks)), mid=mid, spread_bps=float(f["spread_bps"][i]),
            microprice_bps=(float(f["microprice"][i]) - mid) / mid * 1e4, imbalance=float(f["imbalance"][i]),
            pressure=float(f["pressure_10"][i]), ofi_z=float(z), absorb_ask=a.score(), absorb_bid=b.score())

    def state(self, key: str, now: datetime) -> BookState | None:
        s = self.states.get(key)
        if s is None or (now - s.ts).total_seconds() > float(self.p["max_age_seconds"]):
            return None
        return s
