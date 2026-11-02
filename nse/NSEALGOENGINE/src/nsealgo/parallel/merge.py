"""Merge parallel backtest jobs into one run directory, with a mark-to-market equity curve rebuilt from trades."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import json
import shutil
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from ..core.clock import at
from ..core.storage import Storage


def reconstruct_equity(trades: pd.DataFrame, open_positions: list[dict], history, days: list[date],
                       capital: float) -> pd.DataFrame:
    """Daily mark-to-market P&L of every trade from its entry to its exit, using daily closes.
    Telescopes exactly to each trade's net P&L; costs are booked on the exit day."""
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    pnl = pd.Series(0.0, index=idx)
    costs = pd.Series(0.0, index=idx)
    rows = [] if trades is None or trades.empty else trades.to_dict("records")
    last = idx[-1] if len(idx) else None
    for p in open_positions:     # still open at the end: mark at the last close, costs so far
        rows.append({"key": p["key"], "direction": 1 if p["qty"] > 0 else -1, "qty": abs(p["qty"]),
                     "entry_ts": p["entry_ts"], "exit_ts": str(last.date()), "entry_price": p["avg"], "exit_price": None,
                     "costs": p["costs"], "pnl_net": None, "product": p["product"]})
    keys = sorted({r["key"] for r in rows if r.get("product") != "MIS"})
    closes = history.daily_panel(keys, "close").reindex(idx).ffill() if keys else pd.DataFrame(index=idx)
    for r in rows:
        e, x = pd.Timestamp(str(r["entry_ts"])[:10]), pd.Timestamp(str(r["exit_ts"])[:10])
        if x not in pnl.index:
            continue
        c = closes[r["key"]] if r["key"] in closes else None
        seg = c.loc[e:x] if c is not None else None
        if r.get("product") == "MIS" or e >= x or seg is None or seg.isna().any() or not len(seg):
            if r.get("pnl_net") is not None:
                pnl[x] += r["pnl_net"]
                costs[x] += r["costs"]
            continue
        marks = seg.to_numpy(float).copy()
        if r.get("exit_price") is not None:
            marks[-1] = r["exit_price"]
        inc = np.diff(np.concatenate(([r["entry_price"]], marks))) * r["direction"] * r["qty"]
        inc[-1] -= r["costs"]
        pnl.loc[seg.index] += inc
        costs[x] += r["costs"]
    eq = capital + pnl.cumsum()
    return pd.DataFrame({"ts": [at(d.date(), "15:30").isoformat() for d in idx], "equity": eq.to_numpy(),
                         "realized": pnl.cumsum().to_numpy(), "unrealized": 0.0, "costs": costs.cumsum().to_numpy()})


def merge_jobs(results: list[dict], cfg: dict, out_dir: Path, title: str, history, days: list[date],
               names: list[str], perf: dict) -> dict:
    from ..forwardtest.report import build_report
    out_dir = Path(out_dir)
    shutil.rmtree(out_dir / "_jobs", ignore_errors=True)
    db = out_dir / "state.db"
    for f in (db, Path(f"{db}-wal"), Path(f"{db}-shm")):
        try:
            f.unlink(missing_ok=True)
        except PermissionError as ex:      # Windows: open in another program
            raise RuntimeError(f"cannot replace {f}: it is open in another program") from ex
    st = Storage(db)
    trades = sorted((t for r in results for t in r["trades"]), key=lambda t: (t["exit_ts"], t["strategy"], t["key"]))
    st.add_rows("trades", [{k: v for k, v in t.items() if k != "id"} for t in trades])
    st.add_rows("results", [{k: v for k, v in x.items() if k != "id"} for r in results for x in r["results"]])
    st.add_rows("events", [{k: v for k, v in x.items() if k != "id"} for r in results for x in r["errors"]])
    seen = set()
    for r in results:
        for g in r["regime"]:
            if g["date"] not in seen:
                seen.add(g["date"])
                st.add_regime(g["date"], g["state"], json.loads(g["probs"]) if g.get("probs") else None, g["source"])
    eq = reconstruct_equity(pd.DataFrame(trades), [p for r in results for p in r["open_positions"]], history, days,
                            float(cfg["capital"]))
    st.add_rows("equity", eq.to_dict("records"))
    st.flush()
    summary = build_report(st, cfg, out_dir, title, "backtest", names=names)
    summary["perf"] = perf
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    st.close()
    return summary
