"""Multi-core execution: hardware detection, process/thread pools, parallel backtest planning and merging."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
from .hardware import Hardware, blas_info, detect, limit_blas, workers_for
from .pool import TaskError, mp_context, process_map, thread_map

__all__ = ["Hardware", "TaskError", "blas_info", "detect", "limit_blas", "mp_context", "process_map",
           "thread_map", "workers_for"]
