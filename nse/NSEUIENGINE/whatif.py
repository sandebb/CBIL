"""
NSEUIENGINE - whatif.py: what would have happened if every signal of a session had been traded?

    .venv\\Scripts\\python.exe whatif.py                 today's signals, Rs 1,00,000 split equally
    .venv\\Scripts\\python.exe whatif.py --capital 100000 --date 2026-10-05 --all
        --all   also include the signals the regime blocked

Uses today's real open / high / low / last price from Fyers (read-only, no orders) and the backtest's rules:
  fill    day LIMIT buy at the signal price: filled if the low reached it (at the open if it opened below)
  stop    a filled position is stopped out if the low also went through the stop (the order of the day's
          moves is unknown with daily prices, so a fill and a stop on the same day both count - conservative)
  target  counted if the high reached the target after a fill
  else    still open, valued at the last price
Duplicates (one stock signalled by several strategies) are traded once.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import csv
import math
import os
import sys
from datetime import date
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fyers_token import data_dir, get_access_token, get_client_id, shared_dir  # noqa: E402


def f(v, d=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except (TypeError, ValueError):
        return d


def quotes(tickers):
    s = requests.Session()
    s.headers["Authorization"] = f"{get_client_id()}:{get_access_token()}"
    out = {}
    for i in range(0, len(tickers), 50):
        d = s.get("https://api-t1.fyers.in/data/quotes", params={"symbols": ",".join(tickers[i:i + 50])},
                  timeout=20).json()
        for q in d.get("d", []) or []:
            v = q.get("v") or {}
            if v.get("lp") is not None:
                out[q["n"]] = v
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=f"{date.today()}")
    ap.add_argument("--capital", type=float, default=100000)
    ap.add_argument("--all", action="store_true", help="include regime-blocked signals")
    a = ap.parse_args(argv)
    sig_dir = shared_dir("NSEDATAENGINE_SIGNALS_DIR", "NSEDATAENGINE", "signals", Path(__import__("os").environ.get("NSE_ROOT", r"D:\CBIL") + r"/code\NSEDATAENGINE\signals"))
    path = sig_dir / f"signals_for_{a.date.replace('-', '')}.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    rows.sort(key=lambda r: (r["regime_allowed"] != "True", -f(r.get("bt_profit_factor"))))
    seen, picks = set(), []
    for r in rows:
        if (a.all or r["regime_allowed"] == "True") and r["symbol"] not in seen:
            seen.add(r["symbol"])
            picks.append(r)
    tick = {r["symbol"]: f"NSE:{r['symbol']}-EQ" for r in picks}
    q = quotes(list(tick.values()))
    per = a.capital / len(picks)
    res, tot = [], {"filled": 0, "unfilled": 0, "stop": 0, "target": 0, "up": 0, "down": 0, "pnl": 0.0, "inv": 0.0}
    for r in picks:
        v = q.get(tick[r["symbol"]])
        buy, stop, tgt = f(r["trigger_or_limit"]), f(r["stop"]), f(r["target"])
        if not v:
            res.append((r, "no quote", 0, 0, 0, 0))
            continue
        o, h, l, lp = f(v.get("open_price")), f(v.get("high_price")), f(v.get("low_price")), f(v.get("lp"))
        if r["order"] == "BUY-STOP-LIMIT":
            filled = h >= buy and o <= f(r["limit_price"]) * 1.0
            fill = max(o, buy) if filled else 0
        else:
            filled = l <= buy
            fill = min(o, buy) if filled else 0
        if not filled:
            tot["unfilled"] += 1
            res.append((r, f"not filled (low {l:,.2f} > buy)", 0, 0, 0, lp))
            continue
        qty = math.floor(per / fill)
        if qty < 1:
            tot["unfilled"] += 1
            res.append((r, f"skipped: 1 share ({fill:,.0f}) > Rs {per:,.0f} allocation", 0, 0, 0, lp))
            continue
        if l <= stop:
            out, how = stop, "STOPPED OUT"
            tot["stop"] += 1
        elif h >= tgt:
            out, how = tgt, "TARGET HIT"
            tot["target"] += 1
        else:
            out, how = lp, "open, in profit" if lp >= fill else "open, in loss"
            tot["up" if lp >= fill else "down"] += 1
        pnl = (out - fill) * qty
        tot["filled"] += 1
        tot["pnl"] += pnl
        tot["inv"] += fill * qty
        res.append((r, how, fill, qty, pnl, lp))

    print(f"\nWhat-if for {a.date}: {len(picks)} signals ({'incl.' if a.all else 'excl.'} regime-blocked), "
          f"Rs {a.capital:,.0f} split equally = Rs {per:,.0f} each\n")
    print(f"{'Symbol':12s} {'Strategy':28s} {'Buy':>9s} {'Stop':>9s} {'Fill':>9s} {'Qty':>4s} {'Now':>9s} "
          f"{'P&L Rs':>8s} {'%':>6s}  Outcome")
    for r, how, fill, qty, pnl, lp in res:
        pct = (pnl / (fill * qty) * 100) if fill and qty else 0
        print(f"{r['symbol']:12s} {r['strategy']:28s} {f(r['trigger_or_limit']):>9.2f} {f(r['stop']):>9.2f} "
              f"{fill:>9.2f} {qty:>4d} {lp:>9.2f} {pnl:>+8.0f} {pct:>+6.2f}  {how}"
              + ("" if r["regime_allowed"] == "True" else "  [regime-blocked]"))
    print(f"\nFilled {tot['filled']} of {len(picks)} (not filled: {tot['unfilled']})")
    print(f"  stopped out (loss):      {tot['stop']}")
    print(f"  target hit (profit):     {tot['target']}")
    print(f"  still open, in profit:   {tot['up']}")
    print(f"  still open, in loss:     {tot['down']}")
    print(f"Invested Rs {tot['inv']:,.0f}; P&L Rs {tot['pnl']:+,.0f} "
          f"({(tot['pnl'] / tot['inv'] * 100) if tot['inv'] else 0:+.2f}% of the money invested) before charges\n")


if __name__ == "__main__":
    import contextlib
    import io
    buf = io.StringIO()

    class Tee(io.TextIOBase):
        def write(self, t):
            sys.__stdout__.write(t)
            buf.write(t)
            return len(t)

    with contextlib.redirect_stdout(Tee()):
        main()
    from datetime import datetime
    out = data_dir() / "reports" / f"whatif_{datetime.now():%Y%m%d_%H%M}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(buf.getvalue(), encoding="utf-8")
    print(f"saved {out}")
