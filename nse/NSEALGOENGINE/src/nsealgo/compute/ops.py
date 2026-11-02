"""The array operations kernels use, for NumPy (CPU) and PyTorch (GPU via ROCm or CUDA, or CPU).

Kernels are written once against this small surface, so NumPy tests exercise the same kernel code the GPU runs;
each TorchOps method is a one-line translation (axis -> dim, keepdims -> keepdim, ...). Arithmetic, comparisons,
`@`, advanced indexing (x[rows, j]) and boolean-mask assignment are written directly and behave the same in both.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import numpy as np


class NumpyOps:
    name, device = "numpy", "cpu"

    def signature(self) -> str:
        return f"numpy-{np.__version__}"

    def asarray(self, x):
        return np.asarray(x, dtype=np.float64)

    def to_numpy(self, x):
        return np.asarray(x)

    def arange(self, n: int):
        return np.arange(n)

    def full(self, shape, v: float):
        return np.full(tuple(shape), float(v), dtype=np.float64)

    def zeros_bool(self, shape):
        return np.zeros(tuple(shape), dtype=bool)

    def where(self, c, a, b):
        return np.where(c, a, b)

    def isnan(self, x):
        return np.isnan(x)

    def sum(self, x, axis, keepdims: bool = False):
        return np.sum(x, axis=axis, keepdims=keepdims)

    def mean(self, x, axis, keepdims: bool = False):
        return np.mean(x, axis=axis, keepdims=keepdims)

    def sqrt(self, x):
        return np.sqrt(x)

    def log(self, x):
        return np.log(x)

    def clip_min(self, x, lo: float):
        return np.maximum(x, lo)

    def minimum(self, a, b):
        return np.minimum(a, b)

    def argmin(self, x, axis: int = -1):
        return np.argmin(x, axis=axis)

    def stack(self, xs, axis: int = -1):
        return np.stack(xs, axis=axis)

    def swap_last(self, x):
        return np.swapaxes(x, -1, -2)

    def diagonal(self, x):
        return np.diagonal(x, axis1=-2, axis2=-1)

    def eigvalsh(self, x):
        return np.linalg.eigvalsh(x)

    def sort_desc(self, x):
        return -np.sort(-x, axis=-1)

    def sync(self) -> None:
        pass


class TorchOps:
    """PyTorch on `device`: "cuda" is the GPU under both ROCm (AMD) and CUDA (NVIDIA); "cpu" works anywhere."""

    def __init__(self, device: str = "cuda", dtype: str = "float32"):
        import torch
        self.t = torch
        self.dev = torch.device(device)
        self.dtype = getattr(torch, dtype)
        self.name, self.device = f"torch-{self.dev.type}", self.dev.type

    @classmethod
    def gpu(cls, dtype: str = "float32"):
        """TorchOps on the GPU, or None when PyTorch or a usable GPU is missing."""
        try:
            import torch
            if not torch.cuda.is_available():
                return None
            return cls("cuda", dtype)
        except Exception:
            return None

    def signature(self) -> str:
        t = self.t
        hip = getattr(t.version, "hip", None)
        name = t.cuda.get_device_name(0) if self.dev.type == "cuda" else "cpu"
        return f"torch-{t.__version__}|{'rocm-' + str(hip) if hip else 'cuda-' + str(t.version.cuda)}|{name}|{self.dtype}"

    def _t(self, x):
        return x if isinstance(x, self.t.Tensor) else self.t.as_tensor(x, dtype=self.dtype, device=self.dev)

    def asarray(self, x):
        return self.t.as_tensor(np.asarray(x), dtype=self.dtype, device=self.dev)

    def to_numpy(self, x):
        return x.detach().to("cpu").numpy() if isinstance(x, self.t.Tensor) else np.asarray(x)

    def arange(self, n: int):
        return self.t.arange(n, device=self.dev)

    def full(self, shape, v: float):
        return self.t.full(tuple(shape), float(v), dtype=self.dtype, device=self.dev)

    def zeros_bool(self, shape):
        return self.t.zeros(tuple(shape), dtype=self.t.bool, device=self.dev)

    def where(self, c, a, b):
        return self.t.where(c, self._t(a), self._t(b))

    def isnan(self, x):
        return self.t.isnan(x)

    def sum(self, x, axis, keepdims: bool = False):
        return self.t.sum(x, dim=axis, keepdim=keepdims)

    def mean(self, x, axis, keepdims: bool = False):
        return self.t.mean(x, dim=axis, keepdim=keepdims)

    def sqrt(self, x):
        return self.t.sqrt(x)

    def log(self, x):
        return self.t.log(x)

    def clip_min(self, x, lo: float):
        return self.t.clamp(x, min=lo)

    def minimum(self, a, b):
        return self.t.minimum(a, b)

    def argmin(self, x, axis: int = -1):
        return self.t.argmin(x, dim=axis)

    def stack(self, xs, axis: int = -1):
        return self.t.stack(list(xs), dim=axis)

    def swap_last(self, x):
        return x.transpose(-1, -2)

    def diagonal(self, x):
        return self.t.diagonal(x, dim1=-2, dim2=-1)

    def eigvalsh(self, x):
        return self.t.linalg.eigvalsh(x)

    def sort_desc(self, x):
        return self.t.sort(x, dim=-1, descending=True).values

    def sync(self) -> None:
        if self.dev.type == "cuda":
            self.t.cuda.synchronize()
