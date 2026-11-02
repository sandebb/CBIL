import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import math
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd

from nsealgo.core.bars import BarBuilder
from nsealgo.core.clock import IST
from nsealgo.core.crowding import avoid_round_level
from nsealgo.core.indicators import (atr, half_life, kalman_hedge, noise_sigma, round_tick, rsi, vpin, vwap_bands,
                                     window_volume)
from nsealgo.core.models import Quote


def _minute_df(days=3, seed=0):
    rng = np.random.default_rng(seed)
    frames = []
    for d in range(days):
        idx = pd.date_range(datetime(2026, 9, 21 + d, 9, 15, tzinfo=IST), periods=375, freq="1min")
        c = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 375)))
        frames.append(pd.DataFrame({"open": c, "high": c * 1.001, "low": c * 0.999, "close": c,
                                    "volume": rng.integers(100, 1000, 375).astype(float)}, index=idx))
    return pd.concat(frames)


def test_atr_constant_range():
    df = pd.DataFrame({"open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0}, index=range(40))
    assert abs(atr(df, 14).iloc[-1] - 4.0) < 1e-9


def test_rsi_extremes():
    up = pd.Series(np.arange(1, 30, dtype=float))
    assert rsi(up, 2).iloc[-1] == 100.0
    assert rsi(-up, 2).iloc[-1] < 1.0


def test_half_life_of_ar1():
    rng = np.random.default_rng(1)
    x, out = 0.0, []
    for _ in range(3000):
        x = 0.8 * x + rng.normal()
        out.append(x)
    hl = half_life(pd.Series(out))
    assert abs(hl - math.log(2) / -math.log(0.8)) < 0.6


def test_kalman_recovers_hedge_ratio():
    rng = np.random.default_rng(2)
    x = pd.Series(np.cumsum(rng.normal(0, 1, 800)) + 100)
    y = 2.0 * x + 5 + rng.normal(0, 0.5, 800)
    beta = kalman_hedge(y, x, delta=1e-5, obs_var=0.25)["beta"].iloc[-1]
    assert abs(beta - 2.0) < 0.05


def test_vwap_and_bands_order():
    m = _minute_df(1)
    vw, up, lo = vwap_bands(m)
    assert (up >= vw).all() and (lo <= vw).all()


def test_vpin_bounded():
    m = _minute_df(3)
    v = vpin(m, bucket_volume=m["volume"].sum() / 150, window=20)
    assert len(v) > 0 and v.between(0, 1).all()


def test_noise_sigma_and_window_volume():
    m = _minute_df(3)
    sig = noise_sigma(m, [time(9, 45), time(10, 15)], 14)
    assert set(sig) == {time(9, 45), time(10, 15)} and all(v > 0 for v in sig.values())
    assert window_volume(m, time(9, 15), time(9, 20), 14) > 0


def test_ofi_sign():
    bb = BarBuilder()
    t = datetime(2026, 9, 25, 9, 30, tzinfo=IST)
    bb.on_quote(Quote("NSE:X", t, 100, 1000, 99.95, 100.0, 500, 500))
    bb.on_quote(Quote("NSE:X", t + timedelta(seconds=1), 100, 1100, 99.95, 100.0, 900, 300))
    assert bb.ofi("NSE:X", t + timedelta(seconds=2)) > 0


def test_round_tick_and_round_levels():
    assert round_tick(100.03, 0.05, "down") == 100.0
    assert round_tick(100.01, 0.05, "up") == 100.05
    assert avoid_round_level(1000.05, 0.05, -1) < 1000.0     # long stop moved below the round number
    assert avoid_round_level(1234.55, 0.05, -1) == 1234.55   # far from round levels: unchanged
