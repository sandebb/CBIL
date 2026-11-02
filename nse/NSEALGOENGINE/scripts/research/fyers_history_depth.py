"""
How far back does Fyers give 1-minute candles? Asks for one trading week of 1-minute bars at several past dates and
prints how many bars came back. Needs today's Fyers token (NSEUIENGINE's 08:00 token task), so run it on a trading day.

    python scripts\\research\\fyers_history_depth.py [--symbol NSE:RELIANCE-EQ]
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import datetime as dt
import os
import time

import requests

URL = "https://api-t1.fyers.in/data/history"
PROBES = ["2015-01-05", "2016-06-06", "2017-07-03", "2018-01-08", "2019-01-07", "2020-01-06", "2021-01-04",
          "2022-01-03", "2023-01-02", "2024-01-08", "2025-01-06", "2026-01-05"]


def env(name):
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                return str(winreg.QueryValueEx(k, name)[0])
        except OSError:
            pass
    return os.environ.get(name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NSE:RELIANCE-EQ")
    a = ap.parse_args()
    tok, app, day = env("FYERS_ACCESS_TOKEN"), env("FYERS_CLIENT_ID"), env("FYERS_TOKEN_DATE")
    if not tok or not app:
        raise SystemExit("no Fyers token in FYERS_ACCESS_TOKEN / FYERS_CLIENT_ID")
    if day != dt.date.today().isoformat():
        print(f"warning: token is dated {day}, not today - it may be expired")
    s = requests.Session()
    s.headers.update({"Authorization": f"{app}:{tok}"})
    print(f"1-minute bars for {a.symbol}, one week from each date:")
    first = None
    for d in PROBES:
        d0 = dt.date.fromisoformat(d)
        r = s.get(URL, params={"symbol": a.symbol, "resolution": "1", "date_format": "1", "range_from": d,
                               "range_to": (d0 + dt.timedelta(days=6)).isoformat(), "cont_flag": "1"}, timeout=20).json()
        n = len(r.get("candles") or [])
        msg = "" if r.get("s") == "ok" else f"  ({r.get('message') or r.get('s')})"
        if n:
            c0 = dt.datetime.fromtimestamp(r["candles"][0][0])
            msg = f"  first bar {c0:%Y-%m-%d %H:%M}" + msg
            first = first or d
        print(f"  {d}: {n:5d} bars{msg}")
        time.sleep(0.4)
    print(f"\nearliest probe with 1-minute data: {first or 'none'}")


if __name__ == "__main__":
    main()
