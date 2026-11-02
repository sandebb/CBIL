"""Hardware detection and worker policy: physical cores, SMT threads, affinity, container quotas, RAM, BLAS.

CPU-bound work (backtests, HMM fits) runs one process per PHYSICAL core by default: numeric Python gains
little from SMT siblings, and two busy processes on one core mostly fight for the same execution units.
Set parallel.use_smt: true (or --workers 32) to also use the hyper-threads; measure with `nsealgo bench`.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import os
import platform
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

BLAS_ENV = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
            "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS")
GB_PER_WORKER = 0.6          # resident memory of one backtest worker (pandas + data), conservative
_LIMITER = None


@dataclass(frozen=True)
class Hardware:
    physical: int              # physical cores
    logical: int               # hardware threads (SMT siblings included)
    usable: int                # logical CPUs this process may use (affinity and container quota applied)
    usable_physical: int
    memory_gb: float | None
    system: str
    python: str

    @property
    def smt(self) -> bool:
        return self.logical > self.physical

    def as_dict(self) -> dict:
        return {**asdict(self), "smt": self.smt}


def _cgroup_quota() -> float | None:
    """CPU limit imposed by Docker/Kubernetes (cgroup v2, then v1), if any."""
    try:
        parts = Path("/sys/fs/cgroup/cpu.max").read_text(encoding="utf-8").split()
        if parts and parts[0] != "max":
            return int(parts[0]) / int(parts[1])
    except (OSError, ValueError, IndexError, ZeroDivisionError):
        pass
    try:
        q = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text(encoding="utf-8"))
        p = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text(encoding="utf-8"))
        if q > 0 and p > 0:
            return q / p
    except (OSError, ValueError):
        pass
    return None


def _physical_from_proc(logical: int) -> int:
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8")
    except OSError:
        return logical
    pairs, phys, core = set(), None, None
    for line in text.splitlines() + [""]:
        if line.startswith("physical id"):
            phys = line.split(":", 1)[1].strip()
        elif line.startswith("core id"):
            core = line.split(":", 1)[1].strip()
        elif not line.strip():
            if core is not None:
                pairs.add((phys, core))
            phys = core = None
    return len(pairs) or logical


@lru_cache(maxsize=1)
def detect() -> Hardware:
    logical = os.cpu_count() or 1
    physical = mem = affinity = None
    try:
        import psutil
        physical = psutil.cpu_count(logical=False)
        mem = psutil.virtual_memory().total / 2 ** 30
        try:
            affinity = len(psutil.Process().cpu_affinity())
        except (AttributeError, NotImplementedError, OSError):
            affinity = None
    except ImportError:
        pass
    physical = physical or _physical_from_proc(logical)
    if affinity is None:
        try:
            affinity = len(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            affinity = logical
    quota = _cgroup_quota()
    usable = max(1, min(affinity, int(quota) if quota else affinity))
    usable_physical = max(1, min(physical, round(physical * usable / max(logical, 1))))
    return Hardware(physical, logical, usable, usable_physical, round(mem, 1) if mem else None,
                    platform.platform(terse=True), platform.python_version())


def workers_for(kind: str = "cpu", requested=None, *, use_smt: bool = False, tasks: int | None = None,
                per_worker_gb: float = GB_PER_WORKER) -> int:
    """Worker count: explicit request > NSEALGO_WORKERS env > hardware policy; capped by RAM and task count."""
    if requested in (None, "auto", 0, "0", ""):
        requested = os.environ.get("NSEALGO_WORKERS") or None
    hw = detect()
    if requested not in (None, "auto"):
        n = int(requested)
    elif kind == "io":
        n = min(32, hw.usable * 2)
    else:
        n = hw.usable if use_smt else hw.usable_physical
        if hw.memory_gb:
            n = min(n, max(1, int(hw.memory_gb * 0.75 / per_worker_gb)))
    if tasks is not None:
        n = min(n, max(tasks, 1))
    if sys.platform == "win32":
        n = min(n, 61)             # ProcessPoolExecutor limit on Windows
    return max(1, n)


def limit_blas(n: int = 1) -> None:
    """Pin BLAS/OpenMP pools to n threads. With 16 worker processes, 16 BLAS threads each would mean 256
    threads fighting over 16 cores; one per process is fastest for this workload."""
    global _LIMITER
    for v in BLAS_ENV:
        os.environ[v] = str(n)
    try:
        from threadpoolctl import threadpool_limits
        _LIMITER = threadpool_limits(limits=n)      # keep a reference: limits persist while it lives
    except Exception:
        pass


def blas_info() -> list[str]:
    try:
        import numpy  # noqa: F401  (loads the BLAS library so it can be inspected)
        from threadpoolctl import threadpool_info
        return [f"{i.get('internal_api')} {i.get('version')} ({i.get('num_threads')} threads)" for i in threadpool_info()]
    except Exception:
        return []
