"""Programmatic multi-core backtest (research mode) - note the __main__ guard, required for worker processes."""
import argparse
from datetime import date

from nsealgo.backtest.runner import run_backtest
from nsealgo.config import load_config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("start")
    ap.add_argument("end")
    ap.add_argument("--granularity", default="daily")
    ap.add_argument("--workers", default="auto")
    ap.add_argument("--config", default="config/system.yaml")
    a = ap.parse_args()
    s = run_backtest(load_config(a.config), start=date.fromisoformat(a.start), end=date.fromisoformat(a.end),
                     granularity=a.granularity, workers=a.workers)
    print(s["overall"])
    print({k: s["perf"].get(k) for k in ("mode", "workers", "jobs", "wall_s", "job_cpu_s")})


if __name__ == "__main__":
    main()
