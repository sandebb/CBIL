"""Building blocks of the documented strategies: Supertrend, compression setups, Turtle filter and the
planner's holding-aware warm-up."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
from datetime import date

import numpy as np
import pandas as pd

from nsealgo.core.clock import NSECalendar
from nsealgo.core.indicators import supertrend
from nsealgo.parallel.plan import plan_jobs
from nsealgo.strategies import REGISTRY
from nsealgo.strategies.intraday_patterns import compression_setup
from nsealgo.strategies.timing import last_breakout_won


def _bars(close, spread=0.01):
    c = np.asarray(close, float)
    idx = pd.bdate_range("2025-01-01", periods=len(c))
    return pd.DataFrame({"open": c, "high": c * (1 + spread), "low": c * (1 - spread), "close": c,
                         "volume": 1e6}, index=idx)


def test_supertrend_follows_trend_and_flips_on_a_crash():
    up = 100 * np.exp(np.linspace(0, 0.4, 120))
    d = _bars(np.concatenate([up, up[-1] * np.exp(np.linspace(0, -0.25, 25))]))
    line, dirn = supertrend(d, 10, 3.0)
    assert dirn.iloc[119] == 1 and line.iloc[119] < d["close"].iloc[119]          # below price in the uptrend
    assert dirn.iloc[-1] == -1 and line.iloc[-1] > d["close"].iloc[-1]            # flipped and above price
    flips = dirn.diff().fillna(0).ne(0).sum()
    assert 1 <= flips <= 3


def test_compression_setups():
    rng = np.random.default_rng(2)
    c = 100 + np.cumsum(rng.normal(0, 1, 30))
    d = _bars(c, 0.02)
    d.iloc[-1, d.columns.get_loc("high")] = d["close"].iloc[-1] * 1.001           # tiny range, inside the day before
    d.iloc[-1, d.columns.get_loc("low")] = d["close"].iloc[-1] * 0.999
    d.iloc[-2, d.columns.get_loc("high")] = d["close"].iloc[-1] * 1.03
    d.iloc[-2, d.columns.get_loc("low")] = d["close"].iloc[-1] * 0.97
    assert compression_setup(d, "nr7") and compression_setup(d, "inside") and compression_setup(d, "nr4_inside")
    base = np.full(30, 100.0)                            # CPR width depends on where the close sits in the range
    cp = pd.DataFrame({"open": base, "high": base * 1.02, "low": base * 0.98, "close": base * 1.015,
                       "volume": 1e6}, index=pd.bdate_range("2025-01-01", periods=30))
    cp.iloc[-1, cp.columns.get_loc("close")] = 100.0      # close at the range midpoint: zero-width CPR
    assert compression_setup(cp, "narrow_cpr", 0.2)
    cp.iloc[-1, cp.columns.get_loc("close")] = 101.9      # close near the high: the widest CPR of the window
    assert not compression_setup(cp, "narrow_cpr", 0.2)
    assert not compression_setup(_bars(np.linspace(100, 130, 30), 0.001 * np.arange(30) + 0.001), "nr7")


def test_turtle_filter_uses_prices_only():
    win = _bars(np.concatenate([np.full(30, 100.0), np.linspace(101, 130, 20), np.full(5, 130.0)]), 0.005)
    assert last_breakout_won(win)                        # the earlier breakout ran up without a stop-out
    lose = _bars(np.concatenate([np.full(30, 100.0), [103.0], np.linspace(95, 90, 10), np.full(5, 90.0)]), 0.005)
    assert not last_breakout_won(lose)                   # the earlier breakout hit its 2N stop


def test_long_holding_sleeves_get_longer_warm_up():
    days = NSECalendar().trading_days(date(2024, 1, 1), date(2026, 6, 30))
    turtle = REGISTRY["turtle"]({"max_hold": 200})
    jobs = plan_jobs(days, [turtle], "daily", 4, min_block_daily=100)
    later = [j for j in jobs if j.window[0] != j.days[0]]
    def warm(j):
        return days.index(date.fromisoformat(j.window[0])) - days.index(date.fromisoformat(j.days[0]))
    assert later and all(warm(j) >= min(210, days.index(date.fromisoformat(j.window[0]))) for j in later)
    assert max(warm(j) for j in later) >= 210             # full max_hold + 10 wherever the data allows
