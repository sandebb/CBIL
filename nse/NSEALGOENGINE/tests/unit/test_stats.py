"""Statistical toolkit: moments, expansions, OU/Dickey-Fuller, random matrices, fragility, VR, TE, MF-DFA."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import math

import numpy as np
import pandas as pd
import pytest

from nsealgo.core import fragility
from nsealgo.core.stats import (absorption_ratio, ar1_fit, bar_anatomy, cornish_fisher_quantile, edgeworth_valid,
                                exceed_probability, mfdfa, moments, mp_clean, mst_stats, transfer_entropy, variance_ratio)
from nsealgo.research import anatomy

RNG = np.random.default_rng(11)


def test_moments_match_pandas_and_expansions_are_guarded():
    x = RNG.standard_t(5, 400)
    m, s = moments(x), pd.Series(x)
    assert math.isclose(m["skew"], s.skew(), rel_tol=1e-9) and math.isclose(m["exkurt"], s.kurt(), rel_tol=1e-9)
    assert edgeworth_valid(0.3, 0.5) and not edgeworth_valid(2.5, 12.0)        # heavy tails break the expansion
    assert math.isclose(float(cornish_fisher_quantile(0.975, 0.0, 0.0)), 1.959964, rel_tol=1e-5)
    p = exceed_probability(np.zeros(100), 1.0)
    assert 0 < p["empirical"] < 0.02                                             # never-seen is not "impossible"


def test_bar_anatomy_components_sum_to_close_to_close():
    c = 100 * np.exp(np.cumsum(RNG.normal(0, 0.01, 60)))
    o = c * np.exp(RNG.normal(0, 0.003, 60))
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.004, "low": np.minimum(o, c) * 0.996, "close": c})
    a = bar_anatomy(df)
    assert np.allclose(a["gap"] + a["drift"], np.diff(np.log(c)))
    assert (a["upper"] >= 0).all() and (a["lower"] >= 0).all()


def test_dickey_fuller_stops_random_walks_passing_as_mean_reverting():
    walks = 100 * np.exp(np.cumsum(RNG.normal(0, 0.015, (90, 800)), axis=0))   # no mean reversion at all
    f = ar1_fit(walks)
    assert (f["half_life"] <= 15).mean() > 0.35          # an OLS half-life alone "finds" reversion in many
    assert f["stationary"].mean() < 0.10                # Dickey-Fuller holds that near its 5% level
    b = 0.5 ** (1 / 3)                                    # a genuine OU with a 3-session half-life
    x = np.zeros(400)
    for t in range(1, 400):
        x[t] = b * x[t - 1] + RNG.normal()
    g = ar1_fit(x)
    assert g["stationary"] and 2.0 < g["half_life"] < 4.5


def test_random_matrix_cleaning_separates_factors_from_noise():
    T, N = 250, 40
    noise = mp_clean(RNG.normal(size=(T, N)))
    assert noise["n_signal"] <= 1
    F = RNG.normal(size=(T, 2))
    L = np.zeros((N, 2)); L[:20, 0] = 1.0; L[20:, 1] = 1.0
    planted = mp_clean(F @ L.T * 0.8 + RNG.normal(size=(T, N)))
    assert planted["n_signal"] >= 2
    C = planted["corr"]
    assert np.allclose(C, C.T) and np.allclose(np.diag(C), 1.0) and np.linalg.eigvalsh(C).min() > -1e-10


def test_absorption_ratio_and_tree_length_track_coupling():
    base = RNG.normal(size=(120, 30))
    common = RNG.normal(size=(120, 1))
    loose, tight = base + 0.2 * common, base + 2.0 * common
    assert absorption_ratio(tight) > absorption_ratio(loose)
    assert mst_stats(np.corrcoef(tight, rowvar=False))["tree_length"] < mst_stats(np.corrcoef(loose, rowvar=False))["tree_length"]


def test_fragility_timeline_has_no_look_ahead_and_flags_coupling():
    days = pd.bdate_range("2024-01-01", periods=420)
    R = RNG.normal(0, 0.01, (420, 20))
    R[-40:] += RNG.normal(0, 0.03, (40, 1))                # correlations tighten over the last 40 sessions
    closes = pd.DataFrame(100 * np.exp(np.cumsum(R, axis=0)), index=days, columns=[f"S{i}" for i in range(20)])
    cfg = {"window": 60, "short": 15, "long": 252, "threshold": 1.0, "min_names": 10}
    probe = [d.date() for d in days[-60:]]
    tl = fragility.build_timeline(cfg, closes, probe)
    cut = days[-30]
    altered = closes.copy()
    altered.loc[cut:] *= 1.5                              # rewrite the "future" relative to that session
    tl2 = fragility.build_timeline(cfg, altered, [cut.date()])
    assert tl2[cut.date().isoformat()] == tl[cut.date().isoformat()]
    assert tl[probe[-1].isoformat()]["fragile"] and not tl[probe[0].isoformat()]["fragile"]


def test_variance_ratio_detects_trend_and_reversion():
    e = RNG.normal(size=6000)
    trend = np.convolve(e, [1, 0.4], "same")              # positively autocorrelated returns
    revert = np.convolve(e, [1, -0.4], "same")
    assert abs(variance_ratio(e, 5)[1]) < 3
    assert variance_ratio(trend, 5)[1] > 3 and variance_ratio(revert, 5)[1] < -3


def test_transfer_entropy_finds_the_leader_only():
    x = RNG.normal(size=3000)
    y = np.r_[0.0, 0.3 * x[:-1]] + RNG.normal(size=3000)
    assert transfer_entropy(x, y)["p"] < 0.05
    assert transfer_entropy(y, x)["p"] > 0.05
    assert transfer_entropy(RNG.normal(size=3000), y)["p"] > 0.05


def test_mfdfa_is_sane_and_never_crashes_on_short_input():
    w = mfdfa(RNG.normal(size=5000))
    assert abs(w["hurst"] - 0.5) < 0.08 and abs(w["delta_h"]) < 0.2
    assert math.isnan(mfdfa(RNG.normal(size=40))["hurst"])     # too short: NaN instead of an exception
    assert math.isfinite(mfdfa(RNG.normal(size=135))["hurst"])  # one morning of 1-minute bars



def test_research_anatomy_reports_rates_and_rejects_invalid_expansions():
    rng = np.random.default_rng(1)
    n = 400
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.012, n)))
    o = c * np.exp(rng.normal(0, 0.004, n))
    d = pd.DataFrame({"open": o, "high": np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.006, n))),
                      "low": np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.006, n))), "close": c, "volume": 1e6},
                     index=pd.bdate_range("2024-01-01", periods=n))
    r = anatomy(d)
    comps = r["components"]
    assert r["sessions"] == n - 1 and {"gap", "upper", "lower"} <= set(comps)
    for x in comps.values():
        assert 0 <= x["p_empirical"] <= 1 and (x["p_edgeworth"] is None or 0 <= x["p_edgeworth"] <= 1)
    assert comps["gap"]["p_edgeworth"] == pytest.approx(comps["gap"]["p_empirical"], abs=0.05)  # near-normal: valid
    assert comps["upper"]["p_edgeworth"] is None                                               # skewed: rejected
