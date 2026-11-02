"""Anti-crowding controls: comomentum throttle, correlated-drawdown breaker, round-number avoidance."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

from datetime import date

import numpy as np
import pandas as pd

from .indicators import comomentum, round_tick


class CrowdingMonitor:
    def __init__(self, cfg: dict, breaker: bool = True):
        self.cfg = cfg
        self.history: list[tuple[str, float]] = []
        self._last_month: tuple[int, int] | None = None
        self.breaches: dict[str, date] = {}
        self.cooldown_until: date | None = None
        self.breaker = breaker                 # research (sleeve) backtests switch the cross-strategy breaker off
        self.fixed_momentum_mult: float | None = None   # set from a precomputed timeline

    def due(self, asof: date) -> bool:
        return (asof.year, asof.month) != self._last_month

    def update_comomentum(self, asof: date, closes: pd.DataFrame, market: pd.Series) -> None:
        ym = (asof.year, asof.month)
        if ym == self._last_month or closes is None or market is None:
            return
        ts = pd.Timestamp(asof)
        val = comomentum(closes.loc[:ts], market.loc[:ts])
        self._last_month = ym
        if np.isfinite(val):
            self.history.append((str(asof), float(val)))

    def momentum_multiplier(self) -> float:
        if self.fixed_momentum_mult is not None:
            return self.fixed_momentum_mult
        if len(self.history) < 12:
            return 1.0
        vals = np.array([v for _, v in self.history])
        pct = float((vals[:-1] < vals[-1]).mean())
        return float(self.cfg.get("comomentum_throttle", 0.5)) if pct >= self.cfg.get("comomentum_pct", 0.8) else 1.0

    def record_breach(self, strategy: str, day: date) -> None:
        self.breaches[strategy] = day

    def evaluate_breaker(self, day: date, calendar) -> bool:
        wk = day.isocalendar()[:2]
        n = sum(1 for d in self.breaches.values() if d.isocalendar()[:2] == wk)
        if n >= int(self.cfg.get("dd_breaker_count", 3)):
            self.cooldown_until = calendar.add_sessions(day, int(self.cfg.get("dd_cooldown_sessions", 10)))
            return True
        return False

    def global_multiplier(self, day: date) -> float:
        if self.breaker and self.cooldown_until and day <= self.cooldown_until:
            return float(self.cfg.get("dd_breaker_mult", 0.5))
        return 1.0


def build_timeline(cfg: dict, closes: pd.DataFrame, market: pd.Series, days) -> dict[str, float]:
    """Momentum-crowding multiplier for every session (monthly comomentum as of the previous session),
    identical to the engine's own monthly updates; precomputed once so parallel jobs agree."""
    mon, out = CrowdingMonitor(cfg), {}
    idx = market.index
    for d in days:
        i = idx.searchsorted(pd.Timestamp(d), side="left")
        if i == 0:
            continue
        prev = idx[i - 1].date()
        if mon.due(prev) and closes is not None and len(closes.columns) >= 20:
            mon.update_comomentum(prev, closes, market)
        out[d.isoformat()] = mon.momentum_multiplier()
    return out


def avoid_round_level(price: float, tick: float, direction: int, levels=(100, 50, 10), zone_ticks: int = 3) -> float:
    """Move a stop/entry just beyond the nearest round number (Osler 2003: stops cluster there).

    direction=-1 pushes the level below the round number (long stops, short entries);
    direction=+1 pushes it above (short stops, long entries)."""
    if not price or not tick:
        return price
    for lvl in sorted(levels, reverse=True):
        if price < lvl * 2:
            continue
        m = round(price / lvl) * lvl
        if abs(price - m) <= zone_ticks * tick:
            return round_tick(m + direction * (zone_ticks + 1) * tick, tick)
    return round_tick(price, tick)
