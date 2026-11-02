"""
nse_preopen.py - records NSE's pre-open auction (09:00-09:15) every trading morning.

Why: the opening price is decided in the 09:00-09:08 pre-open auction. To trade AT the open (the gap strategies)
the stocks must be ranked on the INDICATIVE price around 09:07, before the order window closes at a random moment
between 09:07 and 09:08. NSE keeps no history of indicative prices, so this records them; `report` then shows how
often the 09:05 / 09:06 / 09:07 ranking matches the final opening price.

  python nse_preopen.py record      08:59 -> 09:14: a snapshot every 20 s (every 5 s 09:06:00-09:07:10, around the
                                    09:06:50 decision time) until the auction closes (09:08),
                                    then 09:09:30, 09:12:30 (final equilibrium price) and 09:14:00.
                                    Scheduled task "NSEDATAENGINE-PreOpen" (Mon-Fri 08:58) runs this.
                                    Started late (PC asleep at 08:58)? Takes one snapshot of the final prices.
                                    Holiday / weekend: NSE's data is not today's -> logs it and stops.
  python nse_preopen.py once        one snapshot now (test the connection any time; outside 09:00-09:15 NSE shows
                                    the last session's pre-open, which is saved under that session's date)
  python nse_preopen.py report      per recorded day: indicative price at 09:05 / 09:06 / 09:07 / last before
                                    09:08 vs the final open - top-15 gap-up / gap-down overlap, rank correlation,
                                    average error. -> logs\\preopen_report.txt
  python nse_preopen.py show        days recorded, snapshots per day

Files (contract dataset `preopen`, location `preopen` = data\\preopen unless NSEDATAENGINE_PREOPEN_DIR is set)
  <preopen>\\YYYY-MM-DD.parquet            one row per stock per snapshot (columns: contract\\schema.yaml)
  <preopen>\\raw\\YYYY-MM-DD\\HHMMSS_<key>.json.gz   NSE's reply as received (kept 60 days), so a changed NSE layout
                                          can be re-parsed: python nse_preopen.py reparse YYYY-MM-DD
  <master>\\logs\\preopen_log.txt
NSE's website API is unofficial: the parser looks for fields by several names, and every reply is kept raw.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import datetime as dt
import gzip
import json
import shutil
import sys
import time
from pathlib import Path

import nse_common as NC  # noqa: F401  (installs missing packages on import)
import nse_contract as C
from nse_common import HEADERS, pl

URL = "https://www.nseindia.com/api/market-data-pre-open"
KEYS_ALL = ("ALL",)
KEYS_SPLIT = ("FO", "OTHERS")          # used when key=ALL fails (FO + OTHERS = every pre-open stock)
EVERY_S = 20
FAST_S, FAST_FROM, FAST_TO = 5, dt.time(9, 6, 0), dt.time(9, 7, 10)   # every 5 s around the decision time (09:06:50)
AUCTION_CLOSE = dt.time(9, 8, 5)
AFTER = (dt.time(9, 9, 30), dt.time(9, 12, 30), dt.time(9, 14, 0))
KEEP_RAW_DAYS = 60
TOP = 15

try:
    OUT = C.location("preopen")
except KeyError:                       # contract older than 1.6
    OUT = C.location("master") / "preopen"
LOG = C.location("master") / "logs" / "preopen_log.txt"


def log(msg: str) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------------------------------------------
# DNS fallback: the PC's DNS lookups fail for minutes at a time (19 / 21 / 22 Oct 2026: "Failed to resolve
# www.nseindia.com" right through 09:06-09:14). Every successful lookup is remembered (also on disk), and when a lookup
# fails the remembered address is used instead - the connection itself usually still works. HTTPS still checks the
# certificate against the host name, so this only skips the lookup.
# ---------------------------------------------------------------------------------------------------------------
import socket

DNS_FILE = OUT / "dns_cache.json"
DNS_HOSTS = ("www.nseindia.com",)
_dns: dict = {}
_orig_getaddrinfo = socket.getaddrinfo


def _dns_load():
    try:
        _dns.update({k: [tuple(x) for x in v] for k, v in json.loads(DNS_FILE.read_text()).items()})
    except Exception:
        pass


def _dns_save():
    try:
        DNS_FILE.parent.mkdir(parents=True, exist_ok=True)
        DNS_FILE.write_text(json.dumps(_dns))
    except Exception:
        pass


def _getaddrinfo(host, port, *a, **k):
    try:
        res = _orig_getaddrinfo(host, port, *a, **k)
        if host in DNS_HOSTS:
            ips = sorted({(r[0].value if hasattr(r[0], "value") else int(r[0]), r[4][0]) for r in res})
            if ips and [list(x) for x in ips] != [list(x) for x in _dns.get(host, [])]:
                _dns[host] = ips
                _dns_save()
        return res
    except socket.gaierror:
        if host in _dns and _dns[host]:
            log(f"  DNS lookup of {host} failed - using the remembered address {_dns[host][0][1]}")
            return [(fam, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                     (ip, port) if fam == socket.AF_INET else (ip, port, 0, 0)) for fam, ip in _dns[host]]
        raise


def dns_fallback_on():
    """Install the fallback and look the hosts up once now (refreshes the remembered addresses)."""
    _dns_load()
    socket.getaddrinfo = _getaddrinfo
    for h in DNS_HOSTS:
        try:
            socket.getaddrinfo(h, 443)
        except Exception as ex:
            log(f"  DNS lookup of {h} failed at start ({ex}); remembered: {_dns.get(h) or 'nothing yet'}")


def _network_error(ex) -> bool:
    t = repr(ex)
    return any(x in t for x in ("NameResolution", "getaddrinfo", "ConnectionError", "Max retries", "timed out"))


# ---------------------------------------------------------------------------------------------------------------
# NSE client
# ---------------------------------------------------------------------------------------------------------------
class Client:
    def __init__(self):
        self.s = None

    def _session(self, fresh=False):
        if fresh or self.s is None:
            import requests
            self.s = requests.Session()
            self.s.headers.update(HEADERS)
            try:
                self.s.get("https://www.nseindia.com/market-data/pre-open-market-cm-and-emerge-market", timeout=10)
            except Exception:
                pass
        return self.s

    def get(self, key: str, quick: bool = False):
        """quick (around the 09:07 decision): short timeouts and pauses, no new session after a network error, so a
        network hiccup costs seconds instead of a minute and a half."""
        err = None
        for attempt in range(2 if quick else 3):
            fresh = attempt > 0 and not (err and _network_error(err))     # a new session only helps against HTTP 401/403
            try:
                r = self._session(fresh=fresh).get(URL, params={"key": key}, timeout=4 if quick else 10,
                                                   headers={"Accept": "application/json"})
                if r.status_code == 200 and r.content:
                    return r.content
                err = f"HTTP {r.status_code}"
            except Exception as ex:
                err = repr(ex)
            time.sleep(0.5 if quick else 1.5)
        raise RuntimeError(f"pre-open key={key}: {err}")


# ---------------------------------------------------------------------------------------------------------------
# parsing (field names looked up by several spellings - NSE renames them now and then)
# ---------------------------------------------------------------------------------------------------------------
def pick(d: dict, *names, default=None):
    if not isinstance(d, dict):
        return default
    low = {k.lower(): v for k, v in d.items()}
    for n in names:
        v = low.get(n.lower())
        if v not in (None, "", "-"):
            return v
    return default


def num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def nse_time(s):
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(str(s).strip(), fmt)
        except (TypeError, ValueError):
            pass
    return None


def parse(raw: bytes, key: str, snap: dt.datetime) -> list[dict]:
    d = json.loads(raw)
    rows = []
    for item in d.get("data", []) if isinstance(d, dict) else []:
        md = item.get("metadata", {}) or {}
        po = (item.get("detail", {}) or {}).get("preOpenMarket", {}) or {}
        ato = po.get("ato", {}) or {}
        sym = pick(md, "symbol")
        if not sym:
            continue
        iep = num(pick(po, "IEP", "iep")) or num(pick(md, "iep", "lastPrice"))
        rows.append({
            "snap": snap,
            "nse_time": nse_time(pick(po, "lastUpdateTime", "lastupdatetime")),
            "symbol": str(sym).strip(),
            "key": key,
            "iep": iep,
            "prev_close": num(pick(po, "prevClose")) or num(pick(md, "previousClose")),
            "pchange": num(pick(md, "pChange")) if pick(md, "pChange") is not None else num(pick(po, "perChange")),
            "final_price": num(pick(po, "finalPrice")),
            "final_qty": num(pick(po, "finalQuantity")) or num(pick(md, "finalQuantity")),
            "total_buy_qty": num(pick(po, "totalBuyQuantity")),
            "total_sell_qty": num(pick(po, "totalSellQuantity")),
            "ato_buy_qty": num(pick(po, "atoBuyQty")) or num(pick(ato, "totalBuyQuantity", "buy")),
            "ato_sell_qty": num(pick(po, "atoSellQty")) or num(pick(ato, "totalSellQuantity", "sell")),
            "turnover": num(pick(md, "totalTurnover")),
        })
    return rows


SCHEMA = {"snap": pl.Datetime("ns"), "nse_time": pl.Datetime("ns"), "symbol": pl.Utf8, "key": pl.Utf8,
          "iep": pl.Float64, "prev_close": pl.Float64, "pchange": pl.Float64, "final_price": pl.Float64,
          "final_qty": pl.Float64, "total_buy_qty": pl.Float64, "total_sell_qty": pl.Float64,
          "ato_buy_qty": pl.Float64, "ato_sell_qty": pl.Float64, "turnover": pl.Float64}


def frame(rows: list[dict]) -> pl.DataFrame:
    df = pl.DataFrame(rows, schema=SCHEMA) if rows else pl.DataFrame(schema=SCHEMA)
    return df.with_columns(pl.col("nse_time").dt.date().alias("date"),
                           pl.col("symbol").alias("company_id"))


def append_day(day: dt.date, df: pl.DataFrame) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{day:%Y-%m-%d}.parquet"
    if p.exists():
        df = pl.concat([pl.read_parquet(p), df], how="diagonal_relaxed")
    df = df.unique(subset=["snap", "symbol"], keep="last").sort(["snap", "symbol"])
    tmp = p.with_suffix(".tmp")
    df.write_parquet(tmp)
    tmp.replace(p)
    return p


# ---------------------------------------------------------------------------------------------------------------
# one snapshot
# ---------------------------------------------------------------------------------------------------------------
def snapshot(cli: Client, today: dt.date | None = None, quick: bool = False) -> tuple[int, dt.date | None]:
    """Fetch, keep the raw reply, parse, append. Returns (rows, NSE's date of the data)."""
    snap = dt.datetime.now()
    rows, raws = [], []
    try:
        raws = [(k, cli.get(k, quick)) for k in KEYS_ALL]
        rows = parse(raws[0][1], "ALL", snap)
    except Exception as ex:
        if _network_error(ex):                         # the network is down: FO / OTHERS would fail the same way
            log(f"  key=ALL failed (network: {str(ex)[:160]}) - retrying in a few seconds")
            return 0, None
        log(f"  key=ALL failed ({ex}) - trying FO + OTHERS")
    if not rows:
        raws = []
        for k in KEYS_SPLIT:
            try:
                raw = cli.get(k)
                raws.append((k, raw))
                rows += parse(raw, k, snap)
            except Exception as ex:
                log(f"  key={k} failed: {ex}")
    if not rows:
        return 0, None
    df = frame(rows).unique(subset=["symbol"], keep="first")
    dd = df["date"].drop_nulls()
    day = dd.mode()[0] if dd.len() else None
    if today is not None and day is not None and day != today:
        return df.height, day                      # NSE still shows an earlier session (holiday): do not save
    day = day or today or snap.date()
    rawdir = OUT / "raw" / f"{day:%Y-%m-%d}"
    rawdir.mkdir(parents=True, exist_ok=True)
    for k, raw in raws:
        with gzip.open(rawdir / f"{snap:%H%M%S}_{k}.json.gz", "wb") as f:
            f.write(raw)
    append_day(day, df)
    return df.height, day


def prune_raw():
    cut = dt.date.today() - dt.timedelta(days=KEEP_RAW_DAYS)
    for d in (OUT / "raw").glob("????-??-??") if (OUT / "raw").exists() else []:
        try:
            if dt.date.fromisoformat(d.name) < cut:
                shutil.rmtree(d, ignore_errors=True)
        except ValueError:
            pass


def wait_until(t: dt.time):
    while True:
        left = (dt.datetime.combine(dt.date.today(), t) - dt.datetime.now()).total_seconds()
        if left <= 0:
            return
        time.sleep(min(left, 5))


def record():
    today = dt.date.today()
    if today.weekday() >= 5:
        log("weekend - nothing to record")
        return
    dns_fallback_on()
    cli = Client()
    now = dt.datetime.now().time()
    if now >= dt.time(9, 13):
        n, day = snapshot(cli, today)
        log(f"started late ({now:%H:%M:%S}): one snapshot of the final prices, {n} stocks, NSE date {day}")
        if day and day != today:
            log("  NSE's data is not today's - a holiday? nothing saved")
        prune_raw()
        return
    wait_until(dt.time(9, 0, 15))
    n, day, done, fails = 0, None, 0, 0
    while dt.datetime.now().time() < AUCTION_CLOSE:
        t0 = time.time()
        fast = dt.time(9, 5, 0) <= dt.datetime.now().time() < AUCTION_CLOSE
        n, day = snapshot(cli, today, quick=fast)
        if n:
            done += 1
            fails = 0
        else:
            fails += 1
        if done == 1 and n:
            log(f"first snapshot: {n} stocks, NSE time date {day}")
        if dt.datetime.now().time() > dt.time(9, 2, 30) and day is not None and day != today:
            log(f"NSE's pre-open data is from {day}, not today - market holiday? stopping")
            return
        if fails >= 6 and dt.datetime.now().time() > dt.time(9, 3):
            log("6 failed snapshots in a row - NSE is blocking or down; will still try the final prices")
            fails = 0
        fast = FAST_FROM <= dt.datetime.now().time() < FAST_TO
        wait = FAST_S if fast else EVERY_S
        if not n and dt.time(9, 5, 0) <= dt.datetime.now().time():
            wait = 2                                   # failed near the decision time: try again almost at once
        time.sleep(max(0.5, wait - (time.time() - t0)))
    for t in AFTER:
        wait_until(t)
        n, day = snapshot(cli, today)
        if n:
            done += 1
    log(f"done: {done} snapshots, {n} stocks in the last one -> {OUT / f'{today:%Y-%m-%d}.parquet'}")
    prune_raw()


# ---------------------------------------------------------------------------------------------------------------
# report: how well does the indicative price before 09:08 predict the final open?
# ---------------------------------------------------------------------------------------------------------------
def liquid_today(day: dt.date) -> set[str] | None:
    """Stocks with 60-day median turnover >= Rs 25 cr and price >= Rs 50 up to the day before (raw_ohlcv)."""
    try:
        p = C.file_path("raw_ohlcv", "master")
        d = (pl.scan_parquet(p).filter((pl.col("datetime") < dt.datetime.combine(day, dt.time()))
                                       & (pl.col("datetime") >= dt.datetime.combine(day - dt.timedelta(days=120), dt.time())))
             .select("company_id", "datetime", "close", "volume").collect().sort("company_id", "datetime"))
        g = d.group_by("company_id").agg((pl.col("close") * pl.col("volume")).tail(60).median().alias("t"),
                                         pl.col("close").last().alias("c"))
        return set(g.filter((pl.col("t") >= 25e7) & (pl.col("c") >= 50))["company_id"].to_list())
    except Exception:
        return None


def day_report(p: Path) -> dict | None:
    df = pl.read_parquet(p).filter(pl.col("iep").is_not_null() & (pl.col("prev_close") > 0))
    if df.is_empty():
        return None
    day = dt.date.fromisoformat(p.stem)
    liq = liquid_today(day)
    if liq:
        df = df.filter(pl.col("symbol").is_in(list(liq)))
    snaps = df.select("snap").unique().sort("snap")["snap"].to_list()
    after = [s for s in snaps if s.time() >= dt.time(9, 9)]
    if not after:
        return None
    fin = df.filter(pl.col("snap") == after[-1]).with_columns(
        pl.coalesce("final_price", "iep").alias("fin")).select("symbol", "fin", "prev_close")
    fin = fin.with_columns((pl.col("fin") / pl.col("prev_close") - 1).alias("gap_f"))
    out = {"day": day, "stocks": fin.height, "snaps": len(snaps)}
    before = [s for s in snaps if s.time() < dt.time(9, 8)]
    marks = {"09:05": dt.time(9, 5), "09:06": dt.time(9, 6), "09:06:50": dt.time(9, 6, 50), "09:07": dt.time(9, 7)}
    for lab, t in list(marks.items()) + [("last", None)]:
        cand = [s for s in before if t is None or s.time() <= t]
        if not cand:
            continue
        s = cand[-1]
        x = (df.filter(pl.col("snap") == s).select("symbol", (pl.col("iep") / pl.col("prev_close") - 1).alias("gap_i"))
             .join(fin, on="symbol"))
        if x.height < 2 * TOP:
            continue
        up_i = set(x.sort("gap_i", descending=True).head(TOP)["symbol"])
        up_f = set(x.sort("gap_f", descending=True).head(TOP)["symbol"])
        dn_i = set(x.sort("gap_i").head(TOP)["symbol"])
        dn_f = set(x.sort("gap_f").head(TOP)["symbol"])
        rc = x.select(pl.corr(pl.col("gap_i").rank(), pl.col("gap_f").rank())).item()
        err = x.select(((pl.col("gap_i") - pl.col("gap_f")).abs() * 1e4).mean()).item()
        out[lab] = {"at": s.strftime("%H:%M:%S"), "up": len(up_i & up_f), "dn": len(dn_i & dn_f), "rc": rc, "err": err}
    return out


def report():
    files = sorted(OUT.glob("????-??-??.parquet")) if OUT.exists() else []
    if not files:
        print(f"nothing recorded yet in {OUT}")
        return
    lines = [f"Pre-open: indicative price before the auction closes vs the final open price "
             f"(liquid stocks: 60-day median turnover >= Rs 25 cr, price >= Rs 50)",
             f"top-{TOP} overlap = how many of the top {TOP} gap-up (gap-down) stocks by the indicative price are also "
             f"in the top {TOP} by the final open; rank corr = Spearman of the gaps; err = average |indicative - final| gap in bps", ""]
    head = f"{'day':10s} {'stocks':>6s} " + " ".join(f"{lab + ' up/dn rc err':>26s}" for lab in ("09:05", "09:06", "09:06:50", "09:07", "last"))
    lines.append(head)
    agg = {lab: [] for lab in ("09:05", "09:06", "09:06:50", "09:07", "last")}
    for p in files:
        try:
            r = day_report(p)
        except Exception as ex:
            lines.append(f"{p.stem}  error: {ex}")
            continue
        if not r:
            lines.append(f"{p.stem}  (no final-price snapshot)")
            continue
        cells = []
        for lab in agg:
            v = r.get(lab)
            if v:
                agg[lab].append(v)
                cells.append(f"{v['at']} {v['up']:2d}/{v['dn']:2d} {v['rc']:.3f} {v['err']:5.1f}")
            else:
                cells.append("-")
        lines.append(f"{r['day']} {r['stocks']:6d} " + " ".join(f"{c:>26s}" for c in cells))
    lines.append("")
    for lab, v in agg.items():
        if v:
            lines.append(f"average {lab:8s}: top-{TOP} gap-up overlap {sum(a['up'] for a in v) / len(v):.1f}, gap-down "
                         f"{sum(a['dn'] for a in v) / len(v):.1f}, rank corr {sum(a['rc'] for a in v) / len(v):.3f}, "
                         f"error {sum(a['err'] for a in v) / len(v):.1f} bps  ({len(v)} days)")
    text = "\n".join(lines)
    print(text)
    rp = C.location("master") / "logs" / "preopen_report.txt"
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(text, encoding="utf-8")


def show():
    files = sorted(OUT.glob("????-??-??.parquet")) if OUT.exists() else []
    print(f"{OUT}: {len(files)} day(s)")
    for p in files:
        df = pl.read_parquet(p, columns=["snap", "symbol"])
        print(f"  {p.stem}  {df['snap'].n_unique():3d} snapshots  {df['symbol'].n_unique():5d} stocks  "
              f"{df['snap'].min():%H:%M:%S} .. {df['snap'].max():%H:%M:%S}")


def reparse(day: str):
    d = OUT / "raw" / day
    rows = []
    for f in sorted(d.glob("*.json.gz")):
        hhmmss, key = f.name.split("_", 1)
        snap = dt.datetime.combine(dt.date.fromisoformat(day), dt.datetime.strptime(hhmmss, "%H%M%S").time())
        rows += parse(gzip.open(f).read(), key.split(".")[0], snap)
    if not rows:
        print(f"no raw replies under {d}")
        return
    df = frame(rows).unique(subset=["snap", "symbol"], keep="first").sort(["snap", "symbol"])
    p = OUT / f"{day}.parquet"
    df.write_parquet(p)
    print(f"{p}: {df.height} rows from {len(list(d.glob('*.json.gz')))} raw replies")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["record", "once", "report", "show", "reparse"])
    ap.add_argument("day", nargs="?")
    a = ap.parse_args()
    if a.cmd == "record":
        try:
            record()
        except Exception as ex:
            log(f"record failed: {ex!r}")
            sys.exit(1)
    elif a.cmd == "once":
        dns_fallback_on()                          # also remembers NSE's address for the morning run
        n, day = snapshot(Client())
        log(f"one snapshot: {n} stocks, NSE date {day}" if n else "snapshot failed - see the messages above")
        if not n:
            sys.exit(1)
    elif a.cmd == "report":
        report()
    elif a.cmd == "show":
        show()
    elif a.cmd == "reparse":
        if not a.day:
            ap.error("reparse needs a day: YYYY-MM-DD")
        reparse(a.day)


if __name__ == "__main__":
    main()
