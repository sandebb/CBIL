"""Compute layer: kernels against direct definitions, fragility batching against the per-window loop, and the
CPU/GPU selector (agreement first, then speed; fallbacks; cache per machine)."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import importlib.util
import math

import numpy as np
import pandas as pd
import pytest

from nsealgo.compute import Compute, NumpyOps
from nsealgo.compute import kernels as K
from nsealgo.compute.backend import KERNELS, bucket
from nsealgo.core import fragility
from nsealgo.core.models import Quote
from nsealgo.core.stats import absorption_ratio, mst_stats


def legacy_series(cfg, closes):
    """The pre-3.3 per-window loop, kept as the reference definition."""
    c = {**fragility.DEFAULTS, **cfg}
    w = int(c["window"])
    R = np.log(closes.astype(float)).diff().iloc[1:]
    rows, idx, vals = [], [], R.to_numpy()
    for end in range(w, len(R) + 1):
        win = vals[end - w:end]
        win = win[:, np.isfinite(win).all(axis=0)]
        if win.shape[1] < int(c["min_names"]):
            continue
        win = win[:, win.std(0) > 0]
        if win.shape[1] < int(c["min_names"]):
            continue
        rows.append((absorption_ratio(win, float(c["ar_frac"])), mst_stats(np.corrcoef(win, rowvar=False))["tree_length"]))
        idx.append(R.index[end - 1])
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx), columns=["ar", "tree_length"])


def test_fragility_batches_equal_the_per_window_loop():
    rng = np.random.default_rng(3)
    T, N = 260, 24
    r = 0.5 * rng.normal(0, 0.01, (T, 1)) + rng.normal(0, 0.012, (T, N))
    closes = pd.DataFrame(100 * np.exp(np.cumsum(r, axis=0)), index=pd.bdate_range("2024-01-01", periods=T),
                          columns=[f"S{i}" for i in range(N)])
    closes.iloc[50:58, 3] = np.nan                     # a data gap
    closes.iloc[100:200, 5] = 123.0                    # a halted stock: zero variance inside some windows
    closes.iloc[:30, 7] = np.nan                       # a late listing
    cfg = {"window": 60, "min_names": 10, "ar_frac": 0.2}
    new = fragility.series(cfg, closes, compute=Compute(device="cpu"))
    old = legacy_series(cfg, closes)
    assert new.index.equals(old.index) and len(new) > 150
    np.testing.assert_allclose(new.to_numpy(), old.to_numpy(), rtol=1e-12, atol=1e-12)


def _quotes():
    rng = np.random.default_rng(5)
    out = []
    for i, depth in enumerate((5, 20, 30, 2)):
        mid = 100.0 + i
        bids = tuple((round(mid - 0.05 * (k + 1), 2), float(rng.integers(1, 400))) for k in range(depth))
        asks = tuple((round(mid + 0.05 * (k + 1), 2), float(rng.integers(1, 400))) for k in range(depth))
        out.append(Quote(f"NSE:S{i}", pd.Timestamp("2026-09-30 10:00"), mid, 0, bids[0][0], asks[0][0],
                         bids[0][1], asks[0][1], bids=bids, asks=asks))
    return out


def test_book_features_match_direct_formulas():
    qs = _quotes()
    f = K.book_features(NumpyOps(), *K.book_arrays(qs, 30), k=5, decay=0.5, bands_bps=(10.0,))
    for i, q in enumerate(qs):
        (b, qb), (a, qa) = q.bids[0], q.asks[0]
        mid = (b + a) / 2
        assert f["microprice"][i] == pytest.approx((b * qa + a * qb) / (qa + qb))
        assert f["spread_bps"][i] == pytest.approx((a - b) / mid * 1e4)
        w = [0.5 ** j for j in range(5)]
        wb = sum(wj * lv[1] for wj, lv in zip(w, q.bids[:5]))
        wa = sum(wj * lv[1] for wj, lv in zip(w, q.asks[:5]))
        assert f["imbalance"][i] == pytest.approx((wb - wa) / (wb + wa))
        assert f["depth_bid_10"][i] == pytest.approx(sum(v for p, v in q.bids if p >= mid * (1 - 10 / 1e4)))
        assert f["depth_ask_10"][i] == pytest.approx(sum(v for p, v in q.asks if p <= mid * (1 + 10 / 1e4)))


def test_mlofi_follows_the_level_rules():
    a = np.array
    prev = (a([[100.0, 99.9, 99.8]]), a([[10.0, 20, 30]]), a([[100.2, 100.3, 100.4]]), a([[7.0, 8, 9]]))
    cur = (a([[100.1, 99.9, 99.7]]), a([[5.0, 25, 40]]), a([[100.15, 100.3, 100.5]]), a([[3.0, 10, 11]]))
    # bid flow: price up -> new qty 5; same -> 25-20; down -> -30. ask flow: 3, 10-8, -9.
    np.testing.assert_allclose(K.mlofi(NumpyOps(), prev, cur), [[2.0, 3.0, -21.0]])
    cur_gap = (a([[100.1, 99.9, np.nan]]),) + cur[1:]                 # third bid level missing now
    np.testing.assert_allclose(K.mlofi(NumpyOps(), prev, cur_gap), [[2.0, 3.0, 9.0]])


class FakeGPU(NumpyOps):
    """A stand-in GPU: NumPy maths, optionally wrong (bias) or broken (fail)."""
    name, device = "fake-gpu", "gpu"

    def __init__(self, sig="fake-1", bias=0.0, fail=False):
        self.sig, self.bias, self.fail = sig, bias, fail

    def signature(self):
        return self.sig

    def clip_min(self, x, lo):
        if self.fail:
            raise RuntimeError("hipErrorNoBinaryForGpu")
        return super().clip_min(x, lo) * (1 + self.bias)


def _comp(gpu, path, cpu_ms, gpu_ms):
    c = Compute(gpu_ops=gpu, cache_path=path)
    c._time = lambda ops, kernel, arrays: gpu_ms if ops is gpu else cpu_ms
    return c


def test_gpu_is_used_only_when_it_agrees_and_is_faster(tmp_path):
    shape = (16, 60, 20)
    fast = _comp(FakeGPU(), tmp_path / "a.json", 10.0, 2.0)
    assert fast.ops_for("fragility", shape) is fast.gpu
    marginal = _comp(FakeGPU(), tmp_path / "b.json", 10.0, 9.5)        # under 10% faster: not worth it
    assert marginal.ops_for("fragility", shape) is marginal.cpu
    wrong = _comp(FakeGPU(bias=0.05), tmp_path / "c.json", 10.0, 1.0)  # fast but disagrees with NumPy
    assert wrong.ops_for("fragility", shape) is wrong.cpu
    assert wrong.decisions["fragility:16x64x32"]["parity"] is False
    broken = _comp(FakeGPU(fail=True), tmp_path / "d.json", 10.0, 1.0)
    assert broken.ops_for("fragility", shape) is broken.cpu and "error" in broken.decisions["fragility:16x64x32"]


def test_a_gpu_failure_at_run_time_falls_back_to_the_cpu(tmp_path):
    g = FakeGPU()
    c = _comp(g, tmp_path / "c.json", 10.0, 2.0)
    X = np.random.default_rng(1).normal(0, 0.01, (16, 60, 20))
    assert c.ops_for("fragility", X.shape) is g
    g.fail = True
    out = c.run("fragility", X)
    assert np.isfinite(out["ar"]).all() and c.ops_for("fragility", X.shape) is c.cpu


def test_decisions_are_cached_per_machine(tmp_path):
    path = tmp_path / "c.json"
    _comp(FakeGPU("machine-A"), path, 10.0, 2.0).ops_for("mlofi", (300, 30))
    assert Compute(gpu_ops=FakeGPU("machine-A"), cache_path=path).decisions        # reused
    assert Compute(gpu_ops=FakeGPU("machine-B"), cache_path=path).decisions == {}  # new GPU/driver: re-measure


def test_cpu_setting_never_touches_the_gpu():
    assert Compute(device="cpu").gpu is None
    assert bucket((300, 30)) == (512, 32) and bucket((1, 60, 50)) == (1, 64, 64)


@pytest.mark.skipif(importlib.util.find_spec("torch") is None, reason="PyTorch not installed")
def test_torch_matches_numpy_on_every_kernel():
    import torch
    from nsealgo.compute.ops import TorchOps
    t = TorchOps("cuda" if torch.cuda.is_available() else "cpu", "float64")
    c = Compute(device="cpu")
    shapes = {"fragility": (8, 60, 20), "book_features": (64, 30), "mlofi": (64, 30)}
    for name, (make, _, _) in KERNELS.items():
        arrays = make(np.random.default_rng(11), shapes[name])
        ref, got = c._run(c.cpu, name, arrays), c._run(t, name, arrays)
        for k in ref:
            x, y = (np.sort(ref[k], -1), np.sort(got[k], -1)) if k.endswith("_sorted") else (ref[k], got[k])
            np.testing.assert_allclose(y, x, rtol=1e-8, atol=1e-10, err_msg=f"{name}.{k}")
    assert not math.isnan(float(np.asarray(got[next(iter(got))]).ravel()[0]))
