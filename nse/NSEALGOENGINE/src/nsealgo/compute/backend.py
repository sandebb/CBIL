"""Pick CPU (NumPy) or GPU (PyTorch) per kernel and input size, by measurement on this machine.

For each (kernel, size bucket) the first call runs a synthetic input of that size on both: the GPU result must
match NumPy's (tolerance for float32), then the faster one wins (the GPU must be >10% faster, transfers
included). Decisions are cached per machine signature (PyTorch/ROCm version, GPU name, dtype); a GPU error
at run time sends that kernel back to the CPU. Worker processes of parallel research always use the CPU, so
sixteen processes don't contend for one GPU.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import json
import logging
import multiprocessing as mp
import time
from pathlib import Path

import numpy as np

from . import kernels as K
from .ops import NumpyOps, TorchOps

log = logging.getLogger("nsealgo.compute")


def _rng_book(rng, B, L):
    mid = 100.0 + rng.normal(0, 5, B)
    bp = mid[:, None] - 0.05 * (1 + np.arange(L))[None, :]
    ap = mid[:, None] + 0.05 * (1 + np.arange(L))[None, :]
    bq, aq = rng.integers(1, 500, (B, L)).astype(float), rng.integers(1, 500, (B, L)).astype(float)
    return bp, bq, ap, aq


def _frag_in(rng, shape):
    B, w, n = shape
    f = rng.normal(0, 0.01, (B, w, 1))
    return (0.6 * f + rng.normal(0, 0.01, (B, w, n)),)


def _frag_run(ops, X, frac: float = 0.2):
    ar, e = K.fragility_windows(ops, X, frac)
    return {"ar": ar, "edges_sorted": e}


def _book_in(rng, shape):
    return _rng_book(rng, *shape)


def _ofi_in(rng, shape):
    prev = _rng_book(rng, *shape)
    cur = tuple(x.copy() for x in prev)
    cur[0][:, :] += rng.choice([-0.05, 0.0, 0.05], shape)
    cur[1][:, :] = rng.integers(1, 500, shape)
    return prev + cur


def _ofi_run(ops, *a):
    return {"ofi": K.mlofi(ops, a[:4], a[4:])}


# kernel: (synthetic input maker, runner returning a dict of arrays, relative tolerance for float32)
KERNELS = {
    "fragility": (_frag_in, _frag_run, 2e-3),
    "book_features": (_book_in, lambda ops, *a, **kw: K.book_features(ops, *a, **kw), 1e-4),
    "mlofi": (_ofi_in, _ofi_run, 1e-4),
}


def bucket(shape) -> tuple:
    """Round each dimension up to a power of two: one decision covers a range of similar sizes."""
    return tuple(1 << max(int(d) - 1, 0).bit_length() for d in shape)


def _close(a: dict, b: dict, rtol: float) -> bool:
    for k in a:
        x, y = np.asarray(a[k], float), np.asarray(b[k], float)
        if k.endswith("_sorted"):
            x, y = np.sort(x, axis=-1), np.sort(y, axis=-1)
        if x.shape != y.shape or not np.allclose(x, y, rtol=rtol, atol=rtol * (np.nanmax(np.abs(x)) + 1e-12),
                                                 equal_nan=True):
            return False
    return True


class Compute:
    def __init__(self, device: str = "auto", gpu_dtype: str = "float32", cache_path=None, gpu_ops="detect",
                 margin: float = 0.9, repeats: int = 3):
        self.cpu = NumpyOps()
        self.device = device
        if device == "cpu" or (gpu_ops == "detect" and mp.parent_process() is not None):
            self.gpu = None                                     # forced CPU, or a research worker process
        else:
            self.gpu = TorchOps.gpu(gpu_dtype) if gpu_ops == "detect" else gpu_ops
        self.path = Path(cache_path) if cache_path else None
        self.margin, self.repeats = float(margin), int(repeats)
        self.sig = self.gpu.signature() if self.gpu is not None else "cpu-only"
        self.decisions: dict[str, dict] = self._load()

    # ---- persistence ---------------------------------------------------------------------------
    def _load(self) -> dict:
        if self.path and self.path.exists():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                if d.get("signature") == self.sig:
                    return d.get("decisions", {})
            except (OSError, ValueError):
                pass
        return {}

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"signature": self.sig, "decisions": self.decisions}, indent=1),
                                 encoding="utf-8")

    # ---- measurement ---------------------------------------------------------------------------
    def _run(self, ops, kernel: str, arrays, **kw) -> dict:
        _, runner, _ = KERNELS[kernel]
        out = runner(ops, *[ops.asarray(a) for a in arrays], **kw)
        return {k: ops.to_numpy(v) for k, v in out.items()}

    def _time(self, ops, kernel: str, arrays) -> float:
        best = float("inf")
        for _ in range(self.repeats):
            t0 = time.perf_counter()
            self._run(ops, kernel, arrays)
            ops.sync()
            best = min(best, time.perf_counter() - t0)
        return best * 1000.0

    def tune(self, kernel: str, shape) -> dict:
        b = bucket(shape)
        key = f"{kernel}:{'x'.join(map(str, b))}"
        if key in self.decisions:
            return self.decisions[key]
        make, _, rtol = KERNELS[kernel]
        arrays = make(np.random.default_rng(7), b)
        ref = self._run(self.cpu, kernel, arrays)
        d = {"choice": "cpu", "cpu_ms": round(self._time(self.cpu, kernel, arrays), 3), "gpu_ms": None, "parity": None}
        if self.gpu is not None:
            try:
                got = self._run(self.gpu, kernel, arrays)            # also warms up the GPU kernels
                d["parity"] = _close(ref, got, rtol)
                if d["parity"]:
                    d["gpu_ms"] = round(self._time(self.gpu, kernel, arrays), 3)
                    if d["gpu_ms"] < self.margin * d["cpu_ms"]:
                        d["choice"] = "gpu"
                else:
                    log.warning("%s: GPU result differs from NumPy at %s; using the CPU", kernel, b)
            except Exception as ex:
                d["error"] = str(ex)[:200]
                log.warning("%s on GPU failed (%s); using the CPU", kernel, ex)
        self.decisions[key] = d
        self._save()
        return d

    def ops_for(self, kernel: str, shape):
        if self.gpu is None:
            return self.cpu
        return self.gpu if self.tune(kernel, shape)["choice"] == "gpu" else self.cpu

    def run(self, kernel: str, *arrays, shape=None, **kw) -> dict:
        """Run `kernel` on NumPy inputs with the chosen backend; NumPy outputs."""
        shape = shape or np.asarray(arrays[0]).shape
        ops = self.ops_for(kernel, shape)
        if ops is not self.cpu:
            try:
                return self._run(ops, kernel, arrays, **kw)
            except Exception as ex:
                key = f"{kernel}:{'x'.join(map(str, bucket(shape)))}"
                self.decisions[key] = {**self.decisions.get(key, {}), "choice": "cpu", "error": str(ex)[:200]}
                self._save()
                log.warning("%s on GPU failed at run time (%s); back to the CPU", kernel, ex)
        return self._run(self.cpu, kernel, arrays, **kw)


_COMPUTE: Compute | None = None


def init_compute(cfg: dict | None = None, cache_dir=None) -> Compute:
    """Configure the process-wide backend (config `compute`: device auto|cpu|gpu, gpu_dtype)."""
    global _COMPUTE
    c = (cfg or {}).get("compute") or {}
    path = Path(cache_dir) / "compute_autotune.json" if cache_dir else None
    _COMPUTE = Compute(device=str(c.get("device", "auto")), gpu_dtype=str(c.get("gpu_dtype", "float32")), cache_path=path)
    return _COMPUTE


def get_compute() -> Compute:
    global _COMPUTE
    if _COMPUTE is None:
        _COMPUTE = Compute()
    return _COMPUTE
