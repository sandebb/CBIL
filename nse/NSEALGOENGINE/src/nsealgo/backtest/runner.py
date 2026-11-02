"""Backtests replay stored history through the same Engine used live (no separate backtest logic).

Two modes:
- research (default when more than one core is available): each strategy is an independent sleeve sized
  off constant capital with frozen Kelly priors; sleeves are cut into period blocks that run in parallel
  on every core, then merged. Measures each strategy's edge cleanly and fast.
- faithful (`--faithful`, or a single core): one engine, all strategies together, compounding equity,
  Bayesian Kelly learning, shared caps and the correlated-drawdown breaker, exactly as live trading.
Both use regime/crowding timelines precomputed once (HMM fits spread across cores).
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import logging
import time
from datetime import date
from pathlib import Path

import pandas as pd

from ..brokers.paper import PaperBroker
from ..brokers.symbols import SymbolMaster
from ..config import DEFAULTS, resolve_path
from ..core.clock import NSECalendar, SimClock, at
from ..core.crowding import build_timeline as crowding_timeline
from ..core.engine import Engine
from ..core.notifier import Notifier
from ..core.regime import build_timeline as regime_timeline
from ..core.storage import Storage
from ..data.feeds import BarReplayFeed, DailyReplayFeed
from ..data.history import HistoryStore
from ..data.universe import Universe
from ..forwardtest.report import build_report
from ..parallel.hardware import detect, workers_for
from ..parallel.jobs import run_job
from ..parallel.merge import merge_jobs
from ..parallel.plan import plan_jobs
from ..system import apply_process_policy, fresh_dir, keep_awake
from ..parallel.pool import process_map
from ..strategies import build_strategies

log = logging.getLogger("nsealgo.backtest")


def load_master(path_or_broker: str | None, cfg: dict) -> SymbolMaster | None:
    """A saved instrument master (CSV path, or a broker name whose cached master to reuse)."""
    if not path_or_broker:
        return None
    p = Path(path_or_broker)
    if not p.exists():
        files = sorted((resolve_path(cfg, "cache_dir") / path_or_broker).glob("master_*.csv"))
        if not files:
            raise FileNotFoundError(f"no cached master for {path_or_broker}; run `nsealgo login {path_or_broker}` first")
        p = files[-1]
    return SymbolMaster(pd.read_csv(p, dtype={"token": str, "broker_symbol": str, "expiry": str}), p.stem)


def _parallel_cfg(cfg: dict) -> dict:
    return {**DEFAULTS["parallel"], **(cfg.get("parallel") or {})}


def build_timelines(cfg: dict, history: HistoryStore, strategies, days, master=None, workers: int = 1,
                    stats: dict | None = None) -> dict:
    rc = cfg["regime"]
    close = history.daily(rc["index_key"])["close"]
    if not len(close):
        close = history.daily("IDX:NIFTY50")["close"]
    vix = history.daily(rc["vix_key"])["close"]
    out = {"regime": {}, "crowding": {}, "fragility": {}}
    if not len(close):
        log.warning("no index history: regime defaults to '%s'", rc.get("default_state"))
        return out
    out["regime"] = regime_timeline(rc, close, vix if len(vix) else None, days, workers, stats,
                                    policy=cfg.get("system"))
    fc = cfg.get("fragility") or {}
    if fc.get("enabled") and days:
        from ..core.fragility import build_timeline as fragility_timeline
        fkeys = Universe(resolve_path(cfg, "universe_dir")).load(fc.get("universe", "nifty50"))
        if fkeys:
            panel = history.daily_panel(fkeys, "close", end=days[-1])
            i0 = max(0, panel.index.searchsorted(pd.Timestamp(days[0])) - int(fc.get("long", 252))
                     - int(fc.get("short", 15)) - int(fc.get("window", 60)) - 5)
            out["fragility"] = fragility_timeline(fc, panel.iloc[i0:], days)
    mom = next((s for s in strategies if s.name == "momentum"), None)
    if mom is not None:
        uni = mom.p.get("universe", "nifty500")
        keys = Universe(resolve_path(cfg, "universe_dir")).load(uni) if isinstance(uni, str) else list(uni)
        panel = history.daily_panel(keys, "close")
        panel = panel.loc[:, panel.notna().sum() > 260]
        out["crowding"] = crowding_timeline(cfg["crowding"], panel, close, days)
    return out


def run_backtest(cfg: dict, **kw) -> dict:
    """Backtest; keeps Windows awake for the duration and applies the process policy (see nsealgo.system)."""
    from ..compute import init_compute
    init_compute(cfg, resolve_path(cfg, "cache_dir"))   # CPU/GPU per kernel, measured on this machine
    from ..system import keep_awake_mode
    apply_process_policy(cfg, "main")
    with keep_awake(enabled=keep_awake_mode(cfg) != "off"):
        return _run_backtest(cfg, **kw)


def _run_backtest(cfg: dict, *, start: date, end: date, granularity: str = "daily", names: list[str] | None = None,
                 history: HistoryStore | None = None, run_dir: str | Path | None = None,
                 master: SymbolMaster | None = None, title: str | None = None, workers=None, split: str | None = None,
                 mode: str | None = None, timelines: dict | None = None) -> dict:
    pc = _parallel_cfg(cfg)
    t0 = time.perf_counter()
    cal = NSECalendar(resolve_path(cfg, "holidays_file"))
    days = cal.trading_days(start, end)
    run_dir = Path(run_dir or resolve_path(cfg, "runs_dir") / f"backtest_{granularity}_{start}_{end}")
    fresh_dir(run_dir)
    history = history or HistoryStore(resolve_path(cfg, "data_dir") / "history.db")
    strategies = build_strategies(cfg, names, granularity)
    n = workers_for("cpu", workers if workers is not None else pc["workers"], use_smt=bool(pc.get("use_smt")))
    split = split or pc["split"]
    if mode in (None, "auto"):
        mode = "research" if (n > 1 and split != "none") else "faithful"
    if history.path == ":memory:":
        n = 1                                  # an in-memory store can't be shared with worker processes
    fit_stats: dict = {}
    tl = timelines or build_timelines(cfg, history, strategies, days, master, n, fit_stats)
    perf = {"mode": mode, "workers": n, "hardware": detect().as_dict(), "timelines_s": round(time.perf_counter() - t0, 2),
            "regime_fits": fit_stats or None}
    title = title or f"Backtest ({granularity}, {mode}) {start} to {end}"
    if mode == "faithful":
        summary = _faithful(cfg, days, granularity, strategies, history, master, run_dir, title, tl)
    else:
        jobs = plan_jobs(days, strategies, granularity, n, split=split, warmup=int(pc["warmup_sessions"]),
                         runoff=int(pc["runoff_sessions"]), min_block_daily=int(pc["min_block_daily"]),
                         min_block_minute=int(pc["min_block_minute"]))
        common = {"cfg": cfg, "granularity": granularity, "history_path": history.path,
                  "master": master.df.reset_index(drop=True) if master is not None else None,
                  "regime": tl["regime"], "crowding": tl["crowding"], "fragility": tl.get("fragility"),
                  "run_root": str(run_dir / "_jobs")}
        specs = [{**common, "job": j.job, "names": j.names, "days": j.days, "window": j.window,
                  "entry_from": j.entry_from} for j in jobs]
        job_stats: dict = {}
        if n > 1:
            results = process_map(run_job, specs, n, label="backtest jobs", stats=job_stats, policy=cfg.get("system"))
        else:
            results = [run_job({**sp, "_history": history}) for sp in specs]
        owned = sum(r["sessions_owned"] for r in results)
        perf.update({"jobs": len(jobs), "job_pool": job_stats or None,
                     "sessions_simulated": sum(r["sessions_simulated"] for r in results), "sessions_owned": owned,
                     "job_cpu_s": round(sum(r["cpu_s"] for r in results), 2)})
        perf["wall_s"] = round(time.perf_counter() - t0, 2)
        summary = merge_jobs(results, cfg, run_dir, title, history, days, [s.name for s in strategies], perf)
    summary["run_dir"] = str(run_dir)
    summary.setdefault("perf", perf)["wall_s"] = round(time.perf_counter() - t0, 2)
    return summary


def _faithful(cfg, days, granularity, strategies, history, master, run_dir, title, tl) -> dict:
    clock = SimClock(at(days[0], "08:00")) if days else SimClock()
    paper = PaperBroker(cfg.get("paper"), resolve_path(cfg, "cache_dir"), clock=clock, mode=granularity, master=master)
    storage = Storage(run_dir / "state.db")
    eng = Engine(cfg, strategies=strategies, execution=paper, data=paper, clock=clock, history=history,
                 storage=storage, mode="backtest", granularity=granularity, run_dir=run_dir, notifier=Notifier({}),
                 regime_timeline=tl["regime"], crowding_timeline=tl["crowding"], io_threads=1,
                 fragility_timeline=tl.get("fragility"))
    Feed = BarReplayFeed if granularity == "minute" else DailyReplayFeed
    for i, d in enumerate(days, 1):
        eng.feed = Feed(history, d)
        eng.run_day(d)
        if i % 100 == 0:
            log.info("backtest %s/%s sessions, equity %.0f", i, len(days), eng.portfolio.equity)
    summary = build_report(storage, cfg, run_dir, title, "backtest", names=[s.name for s in strategies])
    storage.close()
    return summary


def run_parallel_backtest(cfg: dict, start: date, end: date, granularity: str = "daily", names=None, workers=None,
                          run_dir=None, master_csv_path: str | None = None, **kw) -> dict:
    """Alias with the signature from the multi-core design note; always the verified research mode."""
    master = load_master(master_csv_path, cfg) if master_csv_path else kw.pop("master", None)
    return run_backtest(cfg, start=start, end=end, granularity=granularity, names=names, workers=workers,
                        run_dir=run_dir, master=master, mode="research", **kw)
