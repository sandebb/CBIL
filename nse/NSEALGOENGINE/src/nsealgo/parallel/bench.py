"""`nsealgo bench`: measure the multi-core speedup on YOUR machine with a synthetic market.

Runs the same research backtests with 1 worker and with N workers, checks that both produce identical
trades, and reports wall time, CPU time and speedup. Synthetic data says nothing about profitability.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import time
from datetime import date
from pathlib import Path

import pandas as pd

from ..backtest.runner import load_master, run_backtest
from ..config import deep_merge, load_config
from ..core.clock import NSECalendar
from ..core.storage import Storage
from ..data.history import HistoryStore
from ..data.synthetic import build_synthetic, demo_overrides
from .hardware import detect, workers_for

COLS = ["strategy", "key", "entry_ts", "exit_ts", "qty", "entry_price", "exit_price", "pnl_net"]


def _trades(summary: dict) -> pd.DataFrame:
    st = Storage(Path(summary["run_dir"]) / "state.db")
    try:                                   # close promptly: Windows can't clear a run dir with an open DB
        t = st.trades_df()
    finally:
        st.close()
    return t[COLS].sort_values(COLS[:4]).reset_index(drop=True).round(6) if len(t) else t


def run_bench(directory: str | Path, workers=None, days: int = 1300, swing_days: int = 1000,
              intraday_days: int = 10, config: str | None = None) -> dict:
    directory = Path(directory).resolve()
    hist_path = directory / "data" / "history.db"
    end = NSECalendar().prev_trading_day(date.today())
    if not hist_path.exists():
        build_synthetic(directory, end=end, days=days, minute_days=intraday_days + 16)
    c = deep_merge(load_config(config), demo_overrides(directory))
    c["_root"] = str(directory)
    master = load_master(str(directory / "data" / "synthetic_master.csv"), c)
    hist = HistoryStore(hist_path)
    import sqlite3
    with sqlite3.connect(hist_path) as con:
        sessions = [date.fromisoformat(r[0]) for r in con.execute("SELECT DISTINCT d FROM daily ORDER BY d")]
        mdays = [date.fromisoformat(r[0]) for r in con.execute("SELECT DISTINCT substr(ts,1,10) FROM minute ORDER BY 1")]
    n = workers_for("cpu", workers)
    cases = [("daily swing sleeves", "daily", None, sessions[-min(swing_days, len(sessions) - 300)]),
             ("minute intraday sleeves", "minute", ["orb", "vwap_fade", "compression_breakout"],
              mdays[-min(intraday_days, max(len(mdays) - 14, 1))])]
    rows = []
    for label, gran, names, start in cases:
        res = {}
        for w in (1, n):
            t0 = time.perf_counter()
            s = run_backtest(c, start=start, end=sessions[-1] if gran == "daily" else mdays[-1], granularity=gran,
                             names=names, history=hist, master=master, workers=w, mode="research",
                             run_dir=directory / "runs" / f"bench_{gran}_w{w}", title=f"bench {label} x{w}")
            res[w] = (time.perf_counter() - t0, s)
        (t1, s1), (tn, sn) = res[1], res[n]
        a, b = _trades(s1), _trades(sn)
        rows.append({"case": label, "sessions": s1["overall"].get("sessions"), "trades": len(a),
                     "sequential_s": round(t1, 1), "parallel_s": round(tn, 1), "workers": n,
                     "jobs": sn.get("perf", {}).get("jobs"), "speedup": round(t1 / tn, 2) if tn else None,
                     "identical_trades": bool(len(a) == len(b) and (len(a) == 0 or (a == b).all().all()))})
    return {"hardware": detect().as_dict(), "workers": n, "rows": rows}
