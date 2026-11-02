"""Process and thread pools with safe defaults for numeric Python on Linux, macOS and Windows.

- Processes for CPU-bound work: every worker is a separate interpreter with its own GIL.
- Start method: 'forkserver' on Linux (fork-safe with threads and SQLite handles), 'spawn' on Windows
  and macOS. Work functions are module-level and payloads are plain data, so both methods work.
- Children start with single-threaded BLAS (set before they import numpy) to avoid oversubscription.
- Results come back in input order; failures are collected with tracebacks instead of killing the batch.
"""
from __future__ import annotations

import atexit
import contextlib
import logging
import multiprocessing as mp
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool

from .hardware import BLAS_ENV, limit_blas

log = logging.getLogger("nsealgo.parallel")


class TaskError(RuntimeError):
    pass


GUARD_HINT = ("A worker process died while starting. If you call nsealgo from your own script, put the calling "
              "code under `if __name__ == \"__main__\":` - worker processes re-import the main module (always on "
              "Windows/macOS, and with Linux's 'forkserver'), and unguarded top-level code would run again in "
              "every worker.")


def mp_context(method: str | None = None):
    method = method or os.environ.get("NSEALGO_START_METHOD") or \
        ("forkserver" if sys.platform.startswith("linux") else "spawn")
    try:
        return mp.get_context(method)
    except ValueError:
        return mp.get_context("spawn")


WIN_MAX_WORKERS = 61            # ProcessPoolExecutor refuses more on Windows
_POOL: dict = {"ex": None, "key": None}


def _init_worker(level: int, blas_threads: int, policy: dict | None = None) -> None:
    import signal
    with contextlib.suppress(Exception):
        signal.signal(signal.SIGINT, signal.SIG_IGN)        # Ctrl+C is handled once, by the parent
    limit_blas(blas_threads)
    import warnings
    warnings.filterwarnings("ignore")
    logging.basicConfig(level=level, force=True, datefmt="%H:%M:%S",
                        format="%(asctime)s %(processName)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("hmmlearn", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    from ..system import apply_process_policy
    apply_process_policy({"system": policy or {}}, "worker")


def shutdown_pool(kill: bool = False) -> None:
    ex = _POOL["ex"]
    _POOL.update(ex=None, key=None)
    if ex is None:
        return
    if kill:
        for p in list(getattr(ex, "_processes", {}).values()):
            with contextlib.suppress(Exception):
                p.terminate()
    with contextlib.suppress(Exception):
        ex.shutdown(wait=not kill, cancel_futures=True)


atexit.register(shutdown_pool)


def _executor(n: int, level: int, blas_threads: int, policy: dict | None, start_method: str | None):
    """One pool reused across calls (regime fits, then backtest jobs, then variants): on Windows every new
    worker re-imports numpy/pandas/scipy, so re-creating pools costs seconds each time."""
    method = start_method or os.environ.get("NSEALGO_START_METHOD") or ""
    key = (n, level, blas_threads, tuple(sorted((policy or {}).items())), method)
    if _POOL["ex"] is not None and _POOL["key"] == key:
        return _POOL["ex"]
    shutdown_pool()
    saved = {k: os.environ.get(k) for k in BLAS_ENV}
    for k in BLAS_ENV:
        os.environ[k] = str(blas_threads)
    try:
        ex = ProcessPoolExecutor(max_workers=n, mp_context=mp_context(start_method), initializer=_init_worker,
                                 initargs=(level, blas_threads, policy))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    _POOL.update(ex=ex, key=key)
    return ex


def _call(fn, item):
    t0, c0 = time.perf_counter(), time.process_time()
    try:
        return True, fn(item), time.perf_counter() - t0, time.process_time() - c0
    except Exception:
        return False, traceback.format_exc(), time.perf_counter() - t0, time.process_time() - c0


def process_map(fn, items, workers: int, *, label: str = "tasks", blas_threads: int = 1,
                log_level: int = logging.WARNING, raise_errors: bool = True, stats: dict | None = None,
                start_method: str | None = None, policy: dict | None = None) -> list:
    """fn(item) for every item across `workers` processes; ordered results. `stats` receives wall time,
    summed CPU time of the tasks and the effective parallel speedup (CPU seconds per wall second)."""
    items = list(items)
    if not items:
        return []
    n = max(1, int(workers))
    if sys.platform == "win32":
        n = min(n, WIN_MAX_WORKERS)
    if n > 1 and getattr(mp.current_process(), "_inheriting", False):
        raise RuntimeError(GUARD_HINT)          # we are a child re-executing an unguarded main script
    if n > 1 and mp.parent_process() is not None:
        n = 1                                   # already inside a worker: no nested pools (would oversubscribe)
    if len(items) == 1:
        n = 1
    t0 = time.perf_counter()
    if n == 1:
        results = [_call(fn, it) for it in items]
    else:
        ex = _executor(n, log_level, blas_threads, policy, start_method)
        results, done, last = [None] * len(items), 0, 0.0
        try:
            futs = {ex.submit(_call, fn, it): i for i, it in enumerate(items)}
            for f in as_completed(futs):
                results[futs[f]] = f.result()
                done += 1
                el = time.perf_counter() - t0
                if done == len(items) or el - last >= 5:
                    last = el
                    log.info("%s: %d/%d done in %.1fs", label, done, len(items), el)
        except KeyboardInterrupt:
            shutdown_pool(kill=True)
            raise
        except BrokenProcessPool as ex_:
            shutdown_pool(kill=True)
            raise TaskError(GUARD_HINT) from ex_
    wall = time.perf_counter() - t0
    if stats is not None:
        cpu = sum(r[3] for r in results)
        stats.update({"label": label, "tasks": len(items), "workers": min(n, len(items)), "wall_s": round(wall, 2),
                      "cpu_s": round(cpu, 2), "task_wall_s": round(sum(r[2] for r in results), 2),
                      "speedup": round(cpu / wall, 2) if wall > 0 else None})
    errors = [(i, r[1]) for i, r in enumerate(results) if not r[0]]
    if errors and raise_errors:
        raise TaskError(f"{len(errors)} of {len(items)} {label} failed. First failure:\n{errors[0][1]}")
    return [r[1] if r[0] else None for r in results]


def thread_map(fn, items, workers: int) -> list:
    """For I/O-bound work (network, SQLite reads) and GIL-releasing numeric kernels. Ordered results."""
    items = list(items)
    if workers <= 1 or len(items) <= 1:
        return [fn(it) for it in items]
    with ThreadPoolExecutor(max_workers=min(workers, len(items)), thread_name_prefix="nsealgo") as ex:
        return list(ex.map(fn, items))
