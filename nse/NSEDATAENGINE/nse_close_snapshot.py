"""
nse_close_snapshot.py - today's daily bar for every stock right after the 15:30 close, so NSEMLENGINE can forecast the
next session at ~15:37 (in time for NSE's 15:40-16:00 post-close session) instead of after the 19:30 update.

NSE's official closing price is the volume-weighted average price (VWAP) of the trades between 15:00 and 15:30, not the
last trade. Fyers quotes carry the day's average traded price (atp) and volume, so two snapshots give that VWAP exactly:
    close = (atp_1530 x vol_1530 - atp_1500 x vol_1500) / (vol_1530 - vol_1500)
(no trade in the last 30 minutes -> the last traded price, as NSE does). Both estimates are stored and checked every
night against NSE's file (NSEMLENGINE close-check), so we learn which one matches.

  python nse_close_snapshot.py run       14:57 task: waits for 15:00:00, snapshot, waits for 15:30:15, snapshot,
                                         writes <at_close>\\YYYY-MM-DD.parquet (scheduled task NSEDATAENGINE-AtClose)
  python nse_close_snapshot.py once      one snapshot now (tests the Fyers token / symbols; nothing is written
                                         except the raw reply)
  python nse_close_snapshot.py show      days written, stocks, how many closes came from the 30-minute VWAP

Stocks: every company in raw_ohlcv with a bar in the last 10 sessions, 60-day median turnover >= Rs 1 crore and
close >= Rs 10 (NSEMLENGINE's own universe thresholds). Fyers symbol NSE:<symbol>-EQ, then -BE / -BZ if refused.
Indices: NIFTY 50 and INDIA VIX (Fyers), Nikkei 225 close (yfinance ^N225, Tokyo closes 11:30 IST).
Fyers token: FYERS_ACCESS_TOKEN / FYERS_CLIENT_ID / FYERS_TOKEN_DATE (written by NSEUIENGINE's 08:00 token task).
Columns: contract\\schema.yaml dataset at_close.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import datetime as dt
import gzip
import json
import os
import sys
import time
from collections import deque
from pathlib import Path

import nse_common as NC  # noqa: F401  (installs missing packages on import)
import nse_contract as C
from nse_common import pl

DATA_API = "https://api-t1.fyers.in/data"
T_BEGIN, T_END = dt.time(15, 0, 0), dt.time(15, 30, 15)
INDICES = {"NIFTY 50": "NSE:NIFTY50-INDEX", "INDIA VIX": "NSE:INDIAVIX-INDEX"}

try:
    OUT = C.location("at_close")
except KeyError:
    OUT = C.location("master") / "at_close"
LOG = C.location("master") / "logs" / "at_close_log.txt"


def log(msg: str) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def env(name: str) -> str | None:
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                return str(winreg.QueryValueEx(k, name)[0])
        except OSError:
            pass
    return os.environ.get(name)


def fnum(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


class Fyers:
    def __init__(self):
        import requests
        tok, app, day = env("FYERS_ACCESS_TOKEN"), env("FYERS_CLIENT_ID"), env("FYERS_TOKEN_DATE")
        if not tok or not app:
            raise SystemExit("no Fyers token: FYERS_ACCESS_TOKEN / FYERS_CLIENT_ID not set (NSEUIENGINE token task)")
        if day != dt.date.today().isoformat():
            log(f"WARNING: Fyers token is dated {day}, not today - the 08:00 token task may not have run")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"{app}:{tok}", "Accept": "application/json"})
        self.calls = deque()

    def _throttle(self):                       # Fyers: 10/s and 200/min - stay well below
        while True:
            t = time.monotonic()
            while self.calls and t - self.calls[0] > 60:
                self.calls.popleft()
            if len(self.calls) < 150 and len([x for x in self.calls if t - x < 1]) < 6:
                break
            time.sleep(0.1)
        self.calls.append(time.monotonic())

    def quotes(self, symbols: list[str]) -> tuple[dict, list]:
        """symbol -> v (Fyers quote fields), plus the raw replies."""
        out, raws = {}, []
        for i in range(0, len(symbols), 50):
            chunk = symbols[i:i + 50]
            for attempt in range(3):
                self._throttle()
                try:
                    r = self.s.get(f"{DATA_API}/quotes", params={"symbols": ",".join(chunk)}, timeout=15)
                    d = r.json()
                    break
                except Exception as ex:
                    d = {"s": "error", "message": repr(ex)}
                    time.sleep(1)
            raws.append(d)
            if d.get("s") != "ok" and not d.get("d"):
                log(f"  quotes {i}-{i + len(chunk)}: {d.get('message') or d.get('s')}")
                continue
            for q in d.get("d") or []:
                v = q.get("v") or {}
                if q.get("s") == "ok" and fnum(v.get("lp")):
                    out[q.get("n")] = v
        return out, raws


def universe() -> list[str]:
    p = C.file_path("raw_ohlcv", "master")
    since = dt.datetime.combine(dt.date.today() - dt.timedelta(days=130), dt.time())
    d = (pl.scan_parquet(p).filter(pl.col("datetime") >= since)
         .select("company_id", "datetime", "close", "volume").collect().sort("company_id", "datetime"))
    last = d["datetime"].unique().sort()
    recent = last[-10] if last.len() >= 10 else last[0]
    g = d.group_by("company_id").agg((pl.col("close") * pl.col("volume")).tail(60).median().alias("t"),
                                     pl.col("close").last().alias("c"), pl.col("datetime").max().alias("last"))
    g = g.filter((pl.col("t") >= 1e7) & (pl.col("c") >= 10) & (pl.col("last") >= recent))
    return sorted(g["company_id"].to_list())


def snapshot(fy: Fyers, syms: dict[str, str], tag: str, day: dt.date) -> dict:
    """company_id -> v for every stock (+ the indices under their names)."""
    t0 = time.time()
    got, raws = fy.quotes(list(syms.values()) + list(INDICES.values()))
    # refused -EQ symbols: try -BE, then -BZ
    for suffix in ("-BE", "-BZ"):
        miss = [c for c, s in syms.items() if s not in got]
        if not miss:
            break
        alt = {c: f"NSE:{c}{suffix}" for c in miss}
        g2, r2 = fy.quotes(list(alt.values()))
        raws += r2
        for c, s in alt.items():
            if s in g2:
                syms[c] = s
                got[s] = g2[s]
    rawdir = OUT / "raw" / f"{day:%Y-%m-%d}"
    rawdir.mkdir(parents=True, exist_ok=True)
    with gzip.open(rawdir / f"{tag}.json.gz", "wt", encoding="utf-8") as f:
        json.dump(raws, f)
    res = {c: got[s] for c, s in syms.items() if s in got}
    res.update({name: got[s] for name, s in INDICES.items() if s in got})
    log(f"  snapshot {tag}: {len(res)} quotes ({time.time() - t0:.0f}s)")
    return res


def nikkei_close(day: dt.date) -> float | None:
    try:
        import yfinance as yf
        h = yf.Ticker("^N225").history(period="5d", auto_adjust=False)
        if not h.empty and h.index[-1].date() == day:
            return float(h["Close"].iloc[-1])
        log(f"  Nikkei: no close for {day} on Yahoo (last {h.index[-1].date() if not h.empty else '-'}) - "
            f"NSEMLENGINE will repeat the previous close")
    except Exception as ex:
        log(f"  Nikkei: {ex}")
    return None


def build_rows(day: dt.date, b: dict, e: dict, syms: dict) -> pl.DataFrame:
    rows = []
    when = dt.datetime.combine(day, dt.time())
    for c, v in e.items():
        kind = "index" if c in INDICES else "stock"
        v0 = b.get(c, {})
        atp1, vol1, atp0, vol0 = (fnum(v.get("atp")), fnum(v.get("volume")), fnum(v0.get("atp")), fnum(v0.get("volume")))
        lp = fnum(v.get("lp"))
        vwap = None
        if kind == "stock" and None not in (atp1, vol1, atp0, vol0) and vol1 > vol0:
            vwap = (atp1 * vol1 - atp0 * vol0) / (vol1 - vol0)
            if not (lp and abs(vwap / lp - 1) < 0.05):          # far from the last price -> bad data, do not use
                vwap = None
        close, src = (vwap, "vwap30") if vwap else (lp, "ltp")
        rows.append({"company_id": c, "datetime": when, "kind": kind, "fyers_symbol": INDICES.get(c, syms.get(c)),
                     "open": fnum(v.get("open_price")), "high": fnum(v.get("high_price")),
                     "low": fnum(v.get("low_price")), "close": close, "volume": vol1, "ltp": lp, "vwap30": vwap,
                     "close_src": src, "atp": atp1, "prev_close": fnum(v.get("prev_close_price")),
                     "vol_1500": vol0, "atp_1500": atp0})
    return pl.DataFrame(rows, schema={"company_id": pl.String, "datetime": pl.Datetime("ns"), "kind": pl.String,
                                      "fyers_symbol": pl.String, "open": pl.Float64, "high": pl.Float64,
                                      "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64, "ltp": pl.Float64,
                                      "vwap30": pl.Float64, "close_src": pl.String, "atp": pl.Float64,
                                      "prev_close": pl.Float64, "vol_1500": pl.Float64, "atp_1500": pl.Float64})


def wait_until(t: dt.time):
    while (left := (dt.datetime.combine(dt.date.today(), t) - dt.datetime.now()).total_seconds()) > 0:
        time.sleep(min(left, 10))


def run():
    day = dt.date.today()
    if day.weekday() >= 5:
        log("weekend - nothing to do")
        return
    if dt.datetime.now().time() > dt.time(15, 25):
        log("started after 15:25 - the 15:00 snapshot is missed; no file today (the 19:30 forecast still runs)")
        return
    ids = universe()
    syms = {c: f"NSE:{c}-EQ" for c in ids}
    log(f"at-close snapshot for {day}: {len(ids)} stocks")
    fy = Fyers()
    wait_until(T_BEGIN)
    b = snapshot(fy, syms, "1500", day)
    if len(b) < 0.5 * len(ids):
        log("fewer than half the stocks quoted at 15:00 - Fyers token / API problem; stopping")
        sys.exit(1)
    wait_until(T_END)
    e = snapshot(fy, syms, "1530", day)
    df = build_rows(day, b, e, syms)
    st = df.filter(pl.col("kind") == "stock")
    traded = st.filter(pl.col("volume") > pl.col("vol_1500").fill_null(0))
    if traded.height < 0.2 * max(st.height, 1):
        log(f"only {traded.height} of {st.height} stocks traded 15:00-15:30 - market holiday? no file written")
        return
    nk = nikkei_close(day)
    df = pl.concat([df, pl.DataFrame([{"company_id": "NIKKEI 225", "datetime": dt.datetime.combine(day, dt.time()),
                                       "kind": "index", "close": nk, "close_src": "yahoo"}], schema_overrides=df.schema)],
                   how="diagonal_relaxed")
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{day:%Y-%m-%d}.parquet"
    tmp = p.with_suffix(".tmp")
    df.write_parquet(tmp)
    tmp.replace(p)
    n_v = st.filter(pl.col("close_src") == "vwap30").height
    log(f"written {p}: {st.height} stocks ({n_v} closes from the 15:00-15:30 VWAP, {st.height - n_v} last price), "
        f"NIFTY {df.filter(pl.col('company_id') == 'NIFTY 50')['close'].to_list()}, Nikkei {nk}")


def once():
    ids = universe()
    fy = Fyers()
    v = snapshot(fy, {c: f"NSE:{c}-EQ" for c in ids}, f"test_{dt.datetime.now():%H%M%S}", dt.date.today())
    ex = next((x for k, x in v.items() if k not in INDICES), {})
    log(f"once: {len(v)} of {len(ids) + len(INDICES)} quoted; fields in a quote: {sorted(ex)}")
    if "atp" not in ex:
        log("WARNING: no 'atp' field in Fyers quotes - the closes will be last prices, not the 30-minute VWAP")


def show():
    files = sorted(OUT.glob("????-??-??.parquet")) if OUT.exists() else []
    print(f"{OUT}: {len(files)} day(s)")
    for p in files:
        d = pl.read_parquet(p).filter(pl.col("kind") == "stock")
        print(f"  {p.stem}  {d.height:5d} stocks  vwap30 {d.filter(pl.col('close_src') == 'vwap30').height:5d}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "once", "show"])
    a = ap.parse_args()
    if a.cmd == "run":
        try:
            run()
        except SystemExit:
            raise
        except Exception as ex:
            log(f"run failed: {ex!r}")
            sys.exit(1)
    elif a.cmd == "once":
        once()
    else:
        show()


if __name__ == "__main__":
    main()
