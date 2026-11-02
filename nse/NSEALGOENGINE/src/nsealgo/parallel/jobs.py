"""Worker side of parallel backtests: run one job (a sleeve's block of sessions) in its own process."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import time
from datetime import date
from pathlib import Path

import pandas as pd


def run_job(spec: dict) -> dict:
    """Module-level so it pickles under both 'spawn' (Windows/macOS) and 'forkserver' (Linux)."""
    from ..brokers.paper import PaperBroker
    from ..brokers.symbols import SymbolMaster
    from ..config import resolve_path
    from ..core.clock import SimClock, at
    from ..core.engine import Engine
    from ..core.notifier import Notifier
    from ..core.storage import Storage
    from ..data.feeds import BarReplayFeed, DailyReplayFeed
    from ..data.history import HistoryStore
    from ..strategies import build_strategies

    t0, c0 = time.perf_counter(), time.process_time()
    cfg, gran = spec["cfg"], spec["granularity"]
    hist = spec.get("_history") or HistoryStore(spec["history_path"], read_only=True)
    master = spec["master"]
    if isinstance(master, pd.DataFrame):
        master = SymbolMaster(master, "backtest")
    days = [date.fromisoformat(d) for d in spec["days"]]
    w0, w1 = date.fromisoformat(spec["window"][0]), date.fromisoformat(spec["window"][1])
    clock = SimClock(at(days[0], "08:00"))
    paper = PaperBroker(cfg.get("paper"), resolve_path(cfg, "cache_dir"), clock=clock, mode=gran, master=master)
    storage = Storage(":memory:")
    eng = Engine(cfg, strategies=build_strategies(cfg, list(spec["names"]), gran), execution=paper, data=paper,
                 clock=clock, history=hist, storage=storage, mode="backtest", granularity=gran,
                 run_dir=Path(spec["run_root"]) / spec["job"].replace("#", "_").replace("+", "_"),
                 notifier=Notifier({}), regime_timeline=spec.get("regime"), crowding_timeline=spec.get("crowding"),
                 entry_window=(date.fromisoformat(spec["entry_from"]), w1), sizing="constant", learn=False,
                 breaker=False, io_threads=1, fragility_timeline=spec.get("fragility"))
    Feed = BarReplayFeed if gran == "minute" else DailyReplayFeed
    simulated = 0
    for d in days:
        if d > w1 and not eng.portfolio.positions() and not eng.oms.active():
            break                                   # run-off complete: every trade of this block has finished
        eng.feed = Feed(hist, d)
        eng.run_day(d)
        simulated += 1

    def in_window(ts) -> bool:
        return ts is not None and w0.isoformat() <= str(ts)[:10] <= w1.isoformat()

    trades = [r for r in storage.trades_df().to_dict("records") if in_window(r["entry_ts"])]
    results = [r for r in storage.results_df().to_dict("records") if in_window(r.get("entry_ts") or r["ts"])]
    open_pos = [{"strategy": p.strategy, "key": p.key, "qty": p.qty, "avg": p.avg, "entry_ts": p.entry_ts,
                 "costs": p.costs, "product": p.product} for p in eng.portfolio.positions() if in_window(p.entry_ts)]
    errors = storage.events_df("ERROR").to_dict("records")
    regime = [r for r in storage.regime_df().to_dict("records") if w0.isoformat() <= r["date"] <= w1.isoformat()]
    storage.close()
    return {"job": spec["job"], "names": list(spec["names"]), "trades": trades, "results": results,
            "open_positions": open_pos, "errors": errors, "regime": regime, "sessions_simulated": simulated,
            "sessions_owned": sum(1 for d in days if w0 <= d <= w1), "wall_s": time.perf_counter() - t0,
            "cpu_s": time.process_time() - c0}


def variant_job(spec: dict) -> dict:
    """One parameter variant of a robustness test (a full research backtest inside one worker)."""
    from ..backtest.runner import run_backtest
    from ..brokers.symbols import SymbolMaster
    from ..data.history import HistoryStore
    master = spec["master"]
    if isinstance(master, pd.DataFrame):
        master = SymbolMaster(master, "backtest")
    s = run_backtest(spec["cfg"], start=date.fromisoformat(spec["start"]), end=date.fromisoformat(spec["end"]),
                     granularity=spec["granularity"], names=[spec["strategy"]], master=master,
                     history=HistoryStore(spec["history_path"], read_only=True), run_dir=spec["run_dir"],
                     title=spec["title"], workers=1, mode="research", timelines=spec["timelines"])
    m = s["strategies"].get(spec["strategy"], {})
    return {"variant": spec["variant"], "trades": m.get("trades", 0), "expectancy": m.get("expectancy"),
            "exp_unit": m.get("exp_unit"), "net_pnl": m.get("net_pnl"), "max_dd_pct": m.get("max_dd_pct"),
            "sharpe": s["overall"].get("sharpe")}
