"""Multi-core building blocks: hardware policy, pools, vectorised kernels, regime timeline."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import math

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from nsealgo.core.bars import BarBuilder
from nsealgo.core.indicators import ofi_vectorized, vpin
from nsealgo.core.regime import RegimeEngine, build_timeline
from nsealgo.parallel import TaskError, detect, process_map, thread_map, workers_for


def test_hardware_and_worker_policy(monkeypatch):
    hw = detect()
    assert hw.physical >= 1 and hw.logical >= hw.physical and 1 <= hw.usable <= hw.logical
    assert workers_for("cpu") >= 1 and workers_for("io") >= 1
    monkeypatch.setenv("NSEALGO_WORKERS", "3")
    assert workers_for("cpu") == 3 and workers_for("cpu", 5) == 5 and workers_for("cpu", tasks=2) == 2


def test_process_pool_keeps_order_and_reports_failures():
    stats = {}
    assert process_map(math.sqrt, [16, 1, 9, 4], 3, stats=stats) == [4.0, 1.0, 3.0, 2.0]
    assert stats["tasks"] == 4 and stats["workers"] == 3
    with pytest.raises(TaskError):
        process_map(math.sqrt, [4, -1], 2)
    assert thread_map(abs, [-3, 2, -1], 3) == [3, 2, 1]


def _vpin_loop(df, bucket, window):
    dp = df["close"].diff().fillna(0.0).to_numpy(float)
    bf = norm.cdf(dp / (float(np.std(dp)) or 1e-9))
    imb, times, vb, vs, filled = [], [], 0.0, 0.0, 0.0
    for ts, v, f in zip(df.index, df["volume"].to_numpy(float), bf):
        while v > 1e-9:
            take = min(v, bucket - filled)
            vb, vs, filled, v = vb + take * f, vs + take * (1 - f), filled + take, v - take
            if filled >= bucket - 1e-9:
                imb.append(abs(vb - vs) / bucket)
                times.append(ts)
                vb = vs = filled = 0.0
    return pd.Series(imb, index=times).rolling(window, min_periods=max(5, window // 5)).mean().dropna()


def test_vectorised_vpin_equals_bucket_loop():
    rng = np.random.default_rng(5)
    idx = pd.date_range("2026-09-01 09:15", periods=2000, freq="1min")
    df = pd.DataFrame({"close": 100 * np.exp(np.cumsum(rng.normal(0, 1e-3, 2000))),
                       "volume": rng.integers(0, 4000, 2000).astype(float)}, index=idx)
    a, b = vpin(df, df["volume"].sum() / 97.3, 20), _vpin_loop(df, df["volume"].sum() / 97.3, 20)
    assert len(a) == len(b) and (a.index == b.index).all() and np.allclose(a, b, atol=1e-12)


def test_vectorised_ofi_equals_incremental():
    rng = np.random.default_rng(9)
    n = 500
    bid = 100 + np.round(np.cumsum(rng.choice([-0.05, 0, 0.05], n)), 2)
    ask = bid + rng.choice([0.05, 0.1], n)
    bq, aq = rng.integers(1, 900, n).astype(float), rng.integers(1, 900, n).astype(float)
    q = [SimpleQuote(*x) for x in zip(bid, bq, ask, aq)]
    inc = [0.0] + [BarBuilder.ofi_increment(q[i - 1], q[i]) for i in range(1, n)]
    assert np.allclose(ofi_vectorized(bid, bq, ask, aq), inc)


class SimpleQuote:
    def __init__(self, bid, bid_qty, ask, ask_qty):
        self.bid, self.bid_qty, self.ask, self.ask_qty = bid, bid_qty, ask, ask_qty


def _regime_series(n=720, seed=4):
    rng = np.random.default_rng(seed)
    vol = np.where((np.arange(n) // 90) % 3 == 2, 0.025, 0.008)
    idx = pd.bdate_range("2023-06-01", periods=n)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0003, vol))), index=idx)


def test_incremental_filter_equals_full_forward_pass():
    close = _regime_series()
    eng = RegimeEngine({"n_states": 3, "restarts": 1, "seed": 7})
    X = RegimeEngine.features(close).to_numpy()
    eng.params = eng._fit(X[:500])
    full = RegimeEngine.filter(X, eng.params)
    for n in (400, 401, 402, 450, 720 - 21):
        assert np.allclose(eng._filtered_last(X[:n]), full[n - 1], atol=1e-10)


def test_parallel_regime_timeline_equals_day_by_day_updates():
    close = _regime_series()
    cfg = {"mode": "both", "n_states": 3, "min_obs": 450, "refit_every": 7, "restarts": 1, "seed": 7}
    days = [d.date() for d in close.index[-30:]]
    tl = build_timeline(cfg, close, None, days, workers=2)
    eng = RegimeEngine(cfg)
    for d in days:
        prev = close.index[close.index < pd.Timestamp(d)][-1]
        r = eng.update(prev, close, None)
        assert tl[d.isoformat()]["state"] == r["state"] and tl[d.isoformat()]["probs"] == r["probs"]
