"""Split a backtest into independent jobs that can run on separate cores.

Research backtests treat each strategy as an independent SLEEVE sized off constant capital with frozen
Kelly priors (see RiskManager). Under those rules a sleeve's period can be simulated on its own:
- intraday sleeves are flat every night, so any block of sessions is exactly independent;
- swing sleeves get a WARM-UP before the block (entries allowed, but trades entered there belong to the
  previous block and are dropped) so positions and strategy state at the block start match a continuous
  run, and a RUN-OFF after it (no new entries) so trades entered in the block finish their natural life.
Union of all blocks' trades == one continuous run of the sleeve (verified by the test-suite).
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Job:
    job: str
    names: tuple
    days: tuple            # sessions to simulate (ISO): warm-up + block + run-off
    window: tuple          # (first, last) session whose entries belong to this job
    entry_from: str        # entries allowed from here (warm-up start)


def plan_jobs(days, strategies, granularity: str, workers: int, *, split: str = "auto", warmup: int = 120,
              runoff: int = 90, min_block_daily: int = 250, min_block_minute: int = 1,
              min_block_swing_minute: int = 20) -> list[Job]:
    iso = [d.isoformat() for d in days]
    n = len(iso)
    if n == 0:
        return []
    horizon = {s.name: s.horizon for s in strategies}
    hold = {s.name: int(s.p.get("max_hold") or 0) if isinstance(s.p.get("max_hold"), (int, float)) else 0
            for s in strategies}                  # sleeves that hold longer get longer warm-up and run-off
    sleeves = [(s.name,) for s in strategies]
    target = max(2 * workers, len(sleeves))                  # ~2 jobs per worker balances uneven job lengths
    per_sleeve = 1 if (workers <= 1 or split == "strategy") else max(1, math.ceil(target / len(sleeves)))
    jobs = []
    for sl in sleeves:
        swing = any(horizon[x] == "swing" for x in sl)
        if not swing:
            mb = min_block_minute
        else:
            mb = min_block_daily if granularity == "daily" else min_block_swing_minute
        k = max(1, min(per_sleeve, n // max(mb, 1)))
        size = math.ceil(n / k)
        hb = max(hold.get(x, 0) for x in sl)
        wu, ro = max(warmup, hb + 10), max(runoff, hb + 5)
        for b in range(k):
            lo, hi = b * size, min(n, (b + 1) * size)
            if lo >= hi:
                continue
            w0 = max(0, lo - wu) if (swing and lo > 0) else lo
            r1 = min(n, hi + ro) if swing else hi
            jobs.append(Job(f"{'+'.join(sl)}#{b}", sl, tuple(iso[w0:r1]), (iso[lo], iso[hi - 1]), iso[w0]))
    return jobs
