"""Compatibility facade with the names used in the multi-core design note (discover_hardware,
HardwareTopology, MultiCoreExecutor), implemented on nsealgo.parallel.

Differences from the note, on purpose: physical cores are measured (psutil, /proc/cpuinfo), not guessed
as logical // 2 (wrong on CPUs without SMT); container CPU quotas and RAM cap the worker count; BLAS is
pinned to one thread only inside worker processes (a single-process run keeps its multi-threaded BLAS);
the start method is 'forkserver' on Linux and 'spawn' elsewhere.
"""
from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

from ..parallel.hardware import detect, workers_for
from ..parallel.pool import _init_worker, mp_context

log = logging.getLogger("nsealgo.concurrency")


@dataclass(frozen=True)
class HardwareTopology:
    logical_cores: int
    physical_cores: int
    optimal_workers: int


def discover_hardware(worker_override: int | None = None, use_smt: bool = False) -> HardwareTopology:
    hw = detect()
    n = workers_for("cpu", worker_override or None, use_smt=use_smt)
    log.info("hardware: %d logical threads, %d physical cores usable; %d workers", hw.usable, hw.usable_physical, n)
    return HardwareTopology(logical_cores=hw.usable, physical_cores=hw.usable_physical, optimal_workers=n)


class MultiCoreExecutor:
    """ProcessPoolExecutor factory with single-threaded BLAS in every worker."""

    def __init__(self, max_workers: int | None = None, use_smt: bool = False):
        self.topo = discover_hardware(max_workers, use_smt)
        self.ctx = mp_context()

    def get_executor(self) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(max_workers=self.topo.optimal_workers, mp_context=self.ctx,
                                   initializer=_init_worker, initargs=(logging.WARNING, 1))
