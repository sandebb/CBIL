"""Array compute: NumPy always; PyTorch on the GPU (ROCm on the Radeon 8060S) where it measures faster and
matches NumPy on this machine. See backend.py and docs/03-architecture/PX13.md."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
from .backend import Compute, get_compute, init_compute
from .ops import NumpyOps, TorchOps

__all__ = ["Compute", "get_compute", "init_compute", "NumpyOps", "TorchOps"]
