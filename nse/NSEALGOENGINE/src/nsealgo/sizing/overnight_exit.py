"""
Overnight trade - the morning SALE (chosen 19 Oct 2026 from scripts/research/overnight_exit_backtest.py).

Rule "75% to high, else at 12:00 [ML close > actual open]", per stock bought the evening before:
  1. At ~09:06:30 read the stock's indicative opening price (IEP) from the pre-open recorder.
  2. HOLD only if the ML's P50 predicted close (15:37 forecast of the buy day) is ABOVE that open:
       target = open + 0.75 x (P50 predicted high - open), rounded UP to the tick
       -> LIMIT SELL at the target from 09:15; at 12:00 cancel what is left and SELL it at MARKET.
  3. Otherwise (predicted close <= open, or the predicted high <= open, or no price): SELL in the pre-open auction
     (market order, before the order window closes at 09:07-09:08) - the old rule.
Backtest (658 nights, OPTIMIZED top 5, Rs 5 lakh): Rs 1,237 -> Rs 1,529 a night, but t = 1.5 (not proven) and most of
the gain came in 2025. Nothing here sends orders: it writes files to review.

The evening before, `thresholds` gives each stock's HOLD price: if the 09:07 indicative open is BELOW it, hold
(the predicted close is above the open); at or above it, sell in the auction. So the morning decision is one compare.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import datetime as dt
import math
from pathlib import Path

import numpy as np
import pandas as pd

FRAC = 0.75             # of the way from the open to the predicted high
CUTOFF = "12:00"        # unsold at this time -> market
TICK = 0.05


def _up(x: float, tick: float = TICK) -> float:
    return round(math.ceil(x / tick - 1e-9) * tick, 2)


def _need(plan: pd.DataFrame) -> None:
    miss = [c for c in ("high_p50", "close_p50") if c not in plan.columns]
    if miss:
        raise SystemExit(f"the sizing plan has no {', '.join(miss)} columns (the 15:37 forecast's P50 high / close) - "
                         "re-run size-overnight with today's latest_close.csv")


def thresholds(plan: pd.DataFrame) -> pd.DataFrame:
    """The evening view: per stock bought, hold_below = P50 predicted close (hold if the open is below it) and the
    target the limit would get at a few opening prices."""
    _need(plan)
    b = plan[plan["qty"] > 0].copy()
    b["hold_below"] = b["close_p50"]
    b["target_if_open_at_close"] = [_up(p + FRAC * (h - p)) if h > p else np.nan for p, h in zip(b["price"], b["high_p50"])]
    return b[["rank", "company_id", "tradingsymbol", "qty", "price", "open_p50", "high_p50", "close_p50", "hold_below",
              "target_if_open_at_close"]]


def latest_buy_plan(runs_dir: Path, today: pd.Timestamp) -> Path:
    fs = sorted(p for p in Path(runs_dir).glob("overnight_sizing_????-??-??.csv")
                if pd.Timestamp(p.stem[-10:]) < today)
    if not fs:
        raise SystemExit(f"no runs/overnight_sizing_<day>.csv before {today:%Y-%m-%d} - nothing was planned to sell")
    return fs[-1]


def indicative_open(preopen_dir: Path, day: pd.Timestamp) -> pd.DataFrame:
    """company_id -> open (latest IEP before 09:08, or the final price once the auction closed) and its time."""
    p = Path(preopen_dir) / f"{day:%Y-%m-%d}.parquet"
    if not p.exists():
        return pd.DataFrame(columns=["company_id", "open", "price_time"])
    d = pd.read_parquet(p)
    d["snap"] = pd.to_datetime(d["snap"])
    d = d.sort_values("snap").groupby("company_id").tail(1)
    after = d["snap"].dt.time >= dt.time(9, 8, 5)
    px = np.where(after & d["final_price"].notna() & (d["final_price"] > 0), d["final_price"], d["iep"])
    return pd.DataFrame({"company_id": d["company_id"], "open": pd.to_numeric(px, errors="coerce"),
                         "price_time": d["snap"].dt.strftime("%H:%M:%S")})


def plan_exit(runs_dir: Path, preopen_dir: Path, day=None, buy_plan: Path | None = None,
              prices: dict | None = None, positions: Path | None = None, frac: float = FRAC,
              cutoff: str = CUTOFF) -> tuple[pd.DataFrame, dict]:
    """This morning's sale of the stocks bought the evening before. prices: {company_id or symbol: open} overrides
    the recorder; positions: CSV (tradingsymbol or company_id, qty) with what was really bought."""
    day = pd.Timestamp(day or dt.date.today()).normalize()
    bp = Path(buy_plan) if buy_plan else latest_buy_plan(runs_dir, day)
    plan = pd.read_csv(bp)
    _need(plan)
    b = plan[plan["qty"] > 0].copy()
    if positions:
        pos = pd.read_csv(positions)
        key = "company_id" if "company_id" in pos.columns else "tradingsymbol"
        b = b.drop(columns=["qty"]).merge(pos[[key, "qty"]], on=key, how="inner")
    io = indicative_open(preopen_dir, day)
    b = b.merge(io, on="company_id", how="left")
    b["price_from"] = np.where(b["open"].notna(), "pre-open " + b["price_time"].fillna(""), "none")
    for k, v in (prices or {}).items():
        m = (b["company_id"] == k) | (b["tradingsymbol"] == k) | (b["tradingsymbol"] == f"{k}-EQ")
        b.loc[m, "open"], b.loc[m, "price_from"] = float(v), "typed"
    rows = []
    for r in b.itertuples():
        o, h, cl = r.open, r.high_p50, r.close_p50
        if not (isinstance(o, float) and np.isfinite(o)):
            act, why, tgt = "AUCTION", "no opening price - sell in the pre-open auction", np.nan
        elif not cl > o:
            act, why, tgt = "AUCTION", f"predicted close {cl:.2f} is not above the open {o:.2f} - likely to fade", np.nan
        elif not h > o:
            act, why, tgt = "AUCTION", f"predicted high {h:.2f} is not above the open {o:.2f}", np.nan
        else:
            tgt = _up(o + frac * (h - o))
            act = "LIMIT"
            why = (f"predicted close {cl:.2f} > open {o:.2f}: limit at {tgt:.2f} (open + {frac:.0%} of the way to the "
                   f"predicted high {h:.2f}); at {cutoff} sell what is left at market")
        rows.append({"rank": r.rank, "company_id": r.company_id, "tradingsymbol": r.tradingsymbol, "qty": int(r.qty),
                     "bought_at": r.price, "open": o, "price_from": r.price_from, "pred_high": h, "pred_close": cl,
                     "action": act, "limit_price": tgt,
                     "target_vs_open_pct": 100 * (tgt / o - 1) if act == "LIMIT" else np.nan, "why": why})
    out = pd.DataFrame(rows)
    summ = {"day": f"{day:%Y-%m-%d}", "buy_plan": str(bp), "frac": frac, "cutoff": cutoff,
            "n_limit": int((out["action"] == "LIMIT").sum()) if len(out) else 0,
            "n_auction": int((out["action"] == "AUCTION").sum()) if len(out) else 0,
            "no_price": int((b["open"].isna()).sum())}
    return out, summ


def orders(exit_plan: pd.DataFrame) -> pd.DataFrame:
    """The order list to review: auction market sells, 09:15 limit sells (and the 12:00 market sells are the
    LIMIT rows' unfilled remainder - not listed, see the plan)."""
    e = exit_plan
    o = pd.DataFrame({"when": np.where(e["action"] == "LIMIT", "09:15 (cancel and sell rest at MKT at cut-off)",
                                       "now, pre-open (before 09:07)"),
                      "tradingsymbol": e["tradingsymbol"], "exchange": "NSE", "side": "S", "product": "C",
                      "price_type": np.where(e["action"] == "LIMIT", "LMT", "MKT"),
                      "price": np.where(e["action"] == "LIMIT", e["limit_price"], 0.0),
                      "qty": e["qty"], "retention": "DAY"})
    return o
