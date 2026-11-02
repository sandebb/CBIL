"""Documented per-instrument daily systems (long only, delivery, decided at the close).

- turtle: Donchian channel breakout with the Turtle rules (Faith, 'Way of the Turtle', 2007). Buy a close
  above the prior entry_n-day high; the stop sits stop_n x N below entry (N = 20-day ATR); exit on a close
  below the prior exit_n-day low. System 1 (20/10) skips a breakout when the previous one would have won;
  that test uses prices only, as the original rule did. Pyramiding (adding units every N/2) is not
  implemented.
- supertrend: an ATR trend-following rule popular in Indian trading forums and TradingView (O. Seban;
  period 10, multiplier 3), on liquid stocks. Buy when the daily Supertrend flips up; exit when it flips down.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402


import numpy as np

from ..core.indicators import atr, supertrend
from .templates import DailySignalSleeve


def last_breakout_won(d, entry_n: int = 20, exit_n: int = 10, stop_n: float = 2.0, atr_n: int = 20,
                      lookback: int = 150) -> bool:
    """Turtle System 1 filter: would the most recent earlier entry_n-day breakout have been a winner? An
    unfinished hypothetical trade counts as a winner (the move is still running)."""
    h, lo, c = (d[x].to_numpy(float) for x in ("high", "low", "close"))
    N = atr(d, atr_n).to_numpy(float)
    last = len(c) - 1
    for k in range(last - 1, max(entry_n, last - lookback), -1):
        if not np.isfinite(N[k]) or c[k] <= h[k - entry_n:k].max():
            continue
        entry, stop = c[k], c[k] - stop_n * N[k]
        for j in range(k + 1, last):
            if lo[j] <= stop:
                return False
            if j >= exit_n and c[j] < lo[j - exit_n:j].min():
                return c[j] > entry
        return True
    return False


class TurtleBreakout(DailySignalSleeve):
    name = "turtle"
    prior = (0.38, 2.5)
    tail = 260
    default_params = {**DailySignalSleeve.default_params, "symbols": [], "universe": "fno", "entry_n": 55,
                      "exit_n": 20, "atr_n": 20, "stop_n": 2.0, "system1_filter": False, "max_positions": 8,
                      "max_hold": 120, "results_window": 3}

    def entry(self, ctx, key, d):
        n = int(self.p["entry_n"])
        c, prior_high = float(d["close"].iloc[-1]), float(d["high"].iloc[-(n + 1):-1].max())
        if not c > prior_high:
            return None
        if self.p["system1_filter"] and last_breakout_won(d.iloc[:-1], n, int(self.p["exit_n"]), float(self.p["stop_n"]),
                                                           int(self.p["atr_n"])):
            return None
        N = float(atr(d.iloc[:-1], int(self.p["atr_n"])).iloc[-1])
        return c - float(self.p["stop_n"]) * N if np.isfinite(N) and N > 0 else None

    def rank(self, ctx, key, d):
        N = float(atr(d.iloc[:-1], int(self.p["atr_n"])).iloc[-1])
        n = int(self.p["entry_n"])
        return (float(d["close"].iloc[-1]) - float(d["high"].iloc[-(n + 1):-1].max())) / N if N > 0 else 0.0

    def exit_reason(self, ctx, key, d, info):
        m = int(self.p["exit_n"])
        if float(d["close"].iloc[-1]) < float(d["low"].iloc[-(m + 1):-1].min()):
            return f"{m}-day low"
        return None


class SupertrendTrend(DailySignalSleeve):
    name = "supertrend"
    prior = (0.40, 2.2)
    tail = 200
    default_params = {**DailySignalSleeve.default_params, "symbols": [], "universe": "nifty100",
                      "period": 10, "mult": 3.0, "max_hold": 120, "max_positions": 6, "results_window": 3}

    def entry(self, ctx, key, d):
        line, dirn = supertrend(d, int(self.p["period"]), float(self.p["mult"]))
        if len(dirn) < 2 or not (dirn.iloc[-1] > 0 and dirn.iloc[-2] < 0):
            return None
        return float(line.iloc[-1])

    def exit_reason(self, ctx, key, d, info):
        _, dirn = supertrend(d, int(self.p["period"]), float(self.p["mult"]))
        return "Supertrend flipped down" if dirn.iloc[-1] < 0 else None
