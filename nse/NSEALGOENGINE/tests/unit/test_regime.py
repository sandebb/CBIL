import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import numpy as np
import pandas as pd

from nsealgo.config import DEFAULTS
from nsealgo.core.regime import RegimeEngine


def _series(seed=4):
    rng = np.random.default_rng(seed)
    r = np.concatenate([rng.normal(0.0006, 0.006, 700), rng.normal(-0.002, 0.03, 60)])
    idx = pd.bdate_range("2023-01-02", periods=len(r))
    close = pd.Series(10000 * np.exp(np.cumsum(r)), index=idx)
    vix = pd.Series(12 + 800 * pd.Series(r).rolling(10, min_periods=1).std().to_numpy(), index=idx)
    return close, vix


def test_detects_turbulence_and_uses_filtered_probabilities():
    close, vix = _series()
    eng = RegimeEngine({**DEFAULTS["regime"], "min_obs": 300, "mode": "hmm"})
    calm = eng.update(close.index[650].date(), close, vix)
    assert calm["state"] == "calm"
    for d in close.index[-15:]:
        res = eng.update(d.date(), close, vix)
    assert res["state"] in ("turbulent", "crisis")


def test_forward_filter_has_no_look_ahead():
    close, vix = _series()
    eng = RegimeEngine({**DEFAULTS["regime"], "min_obs": 300})
    X = eng.features(close, vix).to_numpy()
    params = eng._fit(X[:500])
    full = eng.filter(X, params)
    prefix = eng.filter(X[:600], params)
    assert np.allclose(full[:600], prefix)   # probabilities up to t never change when later data arrives


def test_posture_table():
    eng = RegimeEngine(DEFAULTS["regime"])
    eng.state = "turbulent"
    assert eng.posture("intraday", "liquidity_providing") == (False, 0.0, False)
    assert eng.posture("swing", "liquidity_taking") == (True, 0.5, False)
    eng.state = "crisis"
    assert eng.posture("swing", "liquidity_providing") == (False, 0.0, True)
