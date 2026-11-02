"""
nse_alt_data.py - NSE data beyond prices, for the strategies and NSEMLENGINE (contract datasets, data_ref\\*.parquet).

  dataset          what                                                     history        source (NSE)
  delivery         delivery qty / % of traded qty, per stock per day        from --start   sec_bhavdata_full_DDMMYYYY.csv
  deals            bulk + block deals (client, buy/sell, qty, price)        from --start   /api/historical/{bulk,block}-deals
  insider          insider / promoter trades (SEBI PIT disclosures)         from --start   /api/corporates-pit
  surveillance     stocks under ASM / GSM surveillance (avoid-list)         from today on  /api/reportASM, /api/reportGSM
  shareholding     promoter / public % per quarter (+ change vs last qtr)   as NSE lists   /api/corporate-share-holdings-master
  pledge           promoter shares pledged                                  as NSE lists   /api/corporate-pledgedata
  index_changes    index member additions / removals, seen day by day       from today on  index constituent lists
  short_selling    short sales reported by institutions, qty per stock/day  from --start   /api/historicalOR/bulk-block-short-deals
  mtf              margin trading (broker-funded) qty / Rs per stock/day    from --start   CM - Margin Trading Disclosure

POINT IN TIME: every event row carries the day the PUBLIC learned of it (`disclosed`). Backtests must use that
date, never the trade date - insiders report days after they trade.

Usage
  python nse_alt_data.py daily                    everything, incremental (run_update.bat runs this nightly)
  python nse_alt_data.py backfill --start 2019-01-01      history for delivery, deals, insider, short_selling, mtf (first run,
                                                          ~30-60 minutes; resumable - downloads are cached). Again later:
                                                          only the missing days (+ the last 7) are read; --redo = all
  python nse_alt_data.py daily --only deals,insider       some datasets only
  python nse_alt_data.py show                     rows / last date per dataset

NSE's website APIs are not an official API and change now and then: a source that fails is logged and skipped,
the others still run, and every raw reply is kept under data_ref\\cache\\alt so a changed layout can be re-parsed
without downloading again. Tickers are mapped to company_id through NSEDATAENGINE's rename list.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import datetime as dt
import io
import json
import time
from pathlib import Path

import nse_common as NC  # noqa: F401  (installs missing packages on import)
import nse_contract as C
from nse_common import HEADERS, atomic_write, resolve_chain, pl

API = "https://www.nseindia.com/api"
URL_FULL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv"
INDEX_LISTS = {
    "NIFTY 50": "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
    "NIFTY NEXT 50": "https://nsearchives.nseindia.com/content/indices/ind_niftynext50list.csv",
    "NIFTY 100": "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv",
    "NIFTY 200": "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv",
    "NIFTY 500": "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "NIFTY MIDCAP 150": "https://nsearchives.nseindia.com/content/indices/ind_niftymidcap150list.csv",
    "NIFTY SMALLCAP 250": "https://nsearchives.nseindia.com/content/indices/ind_niftysmallcap250list.csv",
    "NIFTY200 MOMENTUM 30": "https://www.niftyindices.com/IndexConstituent/ind_nifty200Momentum30_list.csv",
    "NIFTY100 LOW VOLATILITY 30": "https://www.niftyindices.com/IndexConstituent/ind_Nifty100LowVolatility30list.csv",
}
SERIES = {"EQ", "BE", "BZ"}
REF = C.location("ref")
CACHE = REF / "cache" / "alt"
LOG = REF / "logs" / "alt_data_log.txt"


def log(msg: str) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------------------------------------------
# NSE client: browser headers, home-page cookies, 0.35 s between requests, retries with a fresh session
# ---------------------------------------------------------------------------------------------------------------
class NSE:
    def __init__(self):
        self.s, self.last = None, 0.0

    def session(self, fresh=False):
        if fresh or self.s is None:
            import requests
            self.s = requests.Session()
            self.s.headers.update(HEADERS)
            try:
                self.s.get("https://www.nseindia.com/", timeout=20)
            except Exception:
                pass
        return self.s

    def get(self, url, params=None, json_reply=False):
        err = None
        for attempt in range(2):
            wait = self.last + 0.35 - time.time()
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            try:
                r = self.session(fresh=attempt > 0).get(url, params=params, timeout=40,
                                                        headers={"Accept": "application/json"} if json_reply else {})
                if r.status_code == 404:
                    return None
                if r.status_code == 200 and r.content:
                    return r.json() if json_reply else r.content
                err = f"HTTP {r.status_code}"
            except Exception as ex:
                err = repr(ex)
            time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"{url}: {err}")


def cached_json(nse: NSE, name: str, url: str, params: dict | None, keep: bool):
    """JSON reply, saved under cache/alt/<name>.json (re-used when keep=True, i.e. for closed past periods)."""
    p = CACHE / f"{name}.json"
    if keep and p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    d = nse.get(url, params, json_reply=True)
    if d is not None:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d), encoding="utf-8")
    return d


# ---------------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------------
def name_map(nse) -> dict:
    """normalised company name -> NSE symbol, from NSE's equity list (EQUITY_L.csv, cached daily) + sectors.parquet."""
    out = {}
    p = CACHE / f"EQUITY_L_{dt.date.today():%Y%m%d}.csv"
    try:
        if not p.exists():
            data = nse.get("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv")
            if data:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
        if p.exists():
            df = pl.read_csv(p, infer_schema_length=0)
            df = df.rename({c: c.strip() for c in df.columns})
            for sym, name in zip(df["SYMBOL"].to_list(), df["NAME OF COMPANY"].to_list()):
                out[norm_name(name)] = sym.strip()
    except Exception as ex:
        log(f"  equity list: {ex}")
    sp = C.file_path("sectors", "ref")
    if sp.exists():
        s = pl.read_parquet(sp, columns=["company_id", "company_name"]).drop_nulls()
        for sym, name in zip(s["company_id"].to_list(), s["company_name"].to_list()):
            out.setdefault(norm_name(name), sym)
    return out


def norm_name(name) -> str:
    n = " ".join(str(name or "").lower().replace("&", " and ").replace(".", " ").replace(",", " ").replace("-", " ").split())
    for suf in (" limited", " ltd", " ltd."):
        if n.endswith(suf):
            n = n[: -len(suf)]
    return n.strip()


def renames() -> dict:
    p = C.location("master") / "logs" / "symbol_changes.csv"
    if not p.exists():
        return {}
    try:
        df = pl.read_csv(p, infer_schema_length=0)
        return resolve_chain(dict(zip(df["old_ticker"].str.strip_chars().to_list(),
                                      df["new_ticker"].str.strip_chars().to_list())))
    except Exception:
        return {}


def pick(d: dict, *names, default=None):
    """First present key, case- and punctuation-insensitive (NSE renames fields now and then)."""
    norm = {"".join(ch for ch in k.lower() if ch.isalnum()): v for k, v in d.items()}
    for n in names:
        v = norm.get("".join(ch for ch in n.lower() if ch.isalnum()))
        if v not in (None, "", "-", "NIL", "Nil"):
            return v
    return default


def num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def day(v) -> dt.date | None:
    """A date from NSE's many formats ('14-Oct-2026', '14-Oct-2026 18:05:11', '14-10-2026', '2026-10-14')."""
    if v is None:
        return None
    s = str(v).strip()
    for text in (s, s.split(" ")[0]):
        for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y", "%Y-%m-%d",
                    "%d %b %Y", "%d/%m/%Y", "%d-%B-%Y", "%Y-%m-%dT%H:%M:%S"):
            try:
                return dt.datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None


def rows_of(reply) -> list[dict]:
    """The list of records in an NSE JSON reply (a list, or the first list under data / Data / rows)."""
    if isinstance(reply, list):
        return [r for r in reply if isinstance(r, dict)]
    if isinstance(reply, dict):
        for k in ("data", "Data", "rows", "result"):
            if isinstance(reply.get(k), list):
                return [r for r in reply[k] if isinstance(r, dict)]
    return []


def save(dataset: str, new: pl.DataFrame, key: list[str]) -> int:
    path = C.file_path(dataset, "ref")
    new = C.conform(new, dataset, keep_unknown=False)
    if path.exists():
        old = C.conform(pl.read_parquet(path), dataset, keep_unknown=False)
        new = pl.concat([old, new], how="vertical_relaxed")
    new = new.unique(subset=key, keep="last", maintain_order=True).sort(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(new, path)
    return new.height


def last_date(dataset: str, col: str) -> dt.date | None:
    path = C.file_path(dataset, "ref")
    if not path.exists():
        return None
    v = pl.scan_parquet(path).select(pl.col(col).max()).collect().item()
    return v if isinstance(v, dt.date) else None


def rows_in(dataset: str) -> int:
    path = C.file_path(dataset, "ref")
    return pl.scan_parquet(path).select(pl.len()).collect().item() if path.exists() else 0


def have_dates(dataset: str, col: str) -> set:
    """Every date already in a dataset's file."""
    path = C.file_path(dataset, "ref")
    if not path.exists():
        return set()
    return set(pl.scan_parquet(path).select(pl.col(col).unique()).collect().to_series().to_list())


def read_from(dataset: str, col: str, start: dt.date) -> dt.date:
    """Where a run has to start reading: the whole period on `backfill --redo` or when the file does not reach back to
    `start` yet (the missing days are then filled - see the callers); otherwise only the last 7 days (catches late or
    corrected files). So daily and backfill are both incremental."""
    have = last_date(dataset, col)
    if REDO or not have:
        return start
    path = C.file_path(dataset, "ref")
    first = pl.scan_parquet(path).select(pl.col(col).min()).collect().item()
    if isinstance(first, dt.date) and first <= start + dt.timedelta(days=45):
        return max(start, have - dt.timedelta(days=7))
    return start


def sessions(start: dt.date, end: dt.date) -> list[dt.date]:
    lf = C.read("raw_ohlcv", columns=["datetime"], lazy=True)
    s = lf.select(pl.col("datetime").dt.date().unique()).collect().to_series().to_list()
    return sorted(d for d in s if start <= d <= end)


def months(start: dt.date, end: dt.date):
    a = start
    while a <= end:
        b = min((a.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1), end)
        yield a, b
        a = b + dt.timedelta(days=1)


# ---------------------------------------------------------------------------------------------------------------
# datasets
# ---------------------------------------------------------------------------------------------------------------
def do_delivery(nse, start, end, ren):
    have = last_date("delivery", "date")
    a = max(start, have + dt.timedelta(days=1)) if have else start
    days = sessions(a, end)
    log(f"delivery: {len(days)} session(s) to fetch ({a} .. {end})")
    caches = [CACHE / "full"] + [C.location(v) / "cache" / "full" for v in ("fresh", "fresh2019")]
    out = []
    for i, d in enumerate(days):
        data = None
        for c in caches:
            p = c / f"{d:%Y%m%d}.csv"
            if p.exists():
                data = p.read_bytes()
                break
        if data is None:
            data = nse.get(URL_FULL.format(d=d))
            if data is None:
                continue
            (CACHE / "full").mkdir(parents=True, exist_ok=True)
            (CACHE / "full" / f"{d:%Y%m%d}.csv").write_bytes(data)
        try:
            try:
                df = pl.read_csv(io.BytesIO(data), infer_schema_length=0, truncate_ragged_lines=True)
            except Exception:
                df = pl.read_csv(io.BytesIO(data.decode("latin-1").encode("utf-8")), infer_schema_length=0,
                                 truncate_ragged_lines=True)
            df = df.rename({c: c.strip() for c in df.columns}).with_columns(pl.col(pl.String).str.strip_chars())
        except Exception as ex:
            log(f"  delivery {d}: unreadable ({ex})")
            continue
        if "DATE1" not in df.columns or "DELIV_QTY" not in df.columns:
            continue
        df = df.filter(pl.col("SERIES").is_in(list(SERIES)))
        if df.height == 0 or day(df["DATE1"][0]) != d:            # NSE serves the previous file on holidays
            continue
        out.append(df.select(
            pl.col("SYMBOL").alias("symbol"), pl.col("SERIES").alias("series"), pl.lit(d).alias("date"),
            pl.col("TTL_TRD_QNTY").cast(pl.Float64, strict=False).alias("traded_qty"),
            pl.col("DELIV_QTY").cast(pl.Float64, strict=False).alias("deliv_qty"),
            pl.col("DELIV_PER").cast(pl.Float64, strict=False).alias("deliv_pct"),
            pl.col("NO_OF_TRADES").cast(pl.Float64, strict=False).alias("trades")))
        if (i + 1) % 100 == 0:
            log(f"  delivery: {i + 1}/{len(days)}")
    if not out:
        path = C.file_path("delivery", "ref")
        return pl.scan_parquet(path).select(pl.len()).collect().item() if path.exists() else 0
    df = pl.concat(out).with_columns(pl.col("symbol").replace(ren).alias("company_id"))
    df = df.sort("series").unique(subset=["company_id", "date"], keep="first")       # EQ before BE / BZ
    return save("delivery", df, ["company_id", "date"])


DEALS_CAP = 70                                       # NSE's deals API returns at most 70 rows per request


def deals_csv(nse, kind, a, b, closed):
    """The same window through the page's 'Download (.csv)' (no 70-row limit). {'data': [records]} or None."""
    p = CACHE / f"{kind}_{a:%Y%m%d}_{b:%Y%m%d}.csv"
    if closed and p.exists():
        data = p.read_bytes()
    else:
        try:
            data = nse.get(f"{API}/historicalOR/bulk-block-short-deals",
                           {"optionType": f"{kind}_deals", "from": f"{a:%d-%m-%Y}", "to": f"{b:%d-%m-%Y}", "csv": "true"})
        except Exception:
            return None
        if not isinstance(data, (bytes, bytearray)) or b"," not in data[:400] or data.lstrip()[:1] in (b"{", b"["):
            return None
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    try:
        import pandas as pd
        df = pd.read_csv(io.BytesIO(data.lstrip(b"\xef\xbb\xbf")), dtype=str, encoding="utf-8", encoding_errors="replace")
    except Exception:
        return None
    df.columns = [c.strip().lstrip("\ufeff") for c in df.columns]
    m = {"date": "BD_DT_DATE", "symbol": "BD_SYMBOL", "securityname": "BD_SCRIP_NAME", "clientname": "BD_CLIENT_NAME",
         "buysell": "BD_BUY_SELL", "quantitytraded": "BD_QTY_TRD", "remarks": "BD_REMARKS"}
    ren = {}
    for c in df.columns:
        k = "".join(ch for ch in c.lower() if ch.isalnum())
        if k in m:
            ren[c] = m[k]
        elif k.startswith("tradeprice") or "wghtavg" in k:
            ren[c] = "BD_TP_WATP"
    df = df.rename(columns=ren)
    if "BD_SYMBOL" not in df.columns:
        return None
    return {"data": df.to_dict("records"), "complete": True}


def deals_window(nse, kind, a, b, closed):
    """(reply, error) for one date window: the CSV download (complete), else the JSON API (max 70 rows) - current
    endpoint first, the old one as fallback."""
    rep = deals_csv(nse, kind, a, b, closed)
    if rep is not None:
        return rep, None
    err = None
    for url, params in ((f"{API}/historicalOR/bulk-block-short-deals",
                         {"optionType": f"{kind}_deals", "from": f"{a:%d-%m-%Y}", "to": f"{b:%d-%m-%Y}"}),
                        (f"{API}/historical/{kind}-deals", {"from": f"{a:%d-%m-%Y}", "to": f"{b:%d-%m-%Y}"})):
        try:
            rep = cached_json(nse, f"{kind}_{a:%Y%m%d}_{b:%Y%m%d}", url, params, keep=closed)
            if rep is not None:
                return rep, None
        except Exception as ex:
            err = ex
    return None, err


def do_deals(nse, start, end, ren):
    a = read_from("deals", "date", start)
    recs = []
    for kind in ("bulk", "block"):
        fails = 0
        for m0, m1 in months(a, end):
            closed = m1 < dt.date.today() - dt.timedelta(days=3)
            rep, err = deals_window(nse, kind, m0, m1, closed)
            if rep is None:
                fails += 1
                log(f"  {kind} deals {m0:%Y-%m}: {err}")
                if fails >= 3:
                    log(f"  {kind} deals: 3 months failed in a row - NSE's endpoint has probably changed; skipped")
                    break
                continue
            fails = 0
            if not rep.get("complete") and len(rows_of(rep)) >= DEALS_CAP:   # JSON reply cut at 70: day by day
                rows = []
                d0 = m0
                while d0 <= m1:
                    if d0.weekday() < 5:
                        r1, _ = deals_window(nse, kind, d0, d0, closed)
                        rows += rows_of(r1)
                        if not (r1 or {}).get("complete") and len(rows_of(r1)) >= DEALS_CAP:
                            log(f"  {kind} deals {d0}: {DEALS_CAP} rows on one day - NSE may have cut it")
                    d0 += dt.timedelta(days=1)
                rep = {"data": rows}
            for r in rows_of(rep):
                sym = pick(r, "BD_SYMBOL", "symbol")
                d = day(pick(r, "BD_DT_DATE", "date", "mTIMESTAMP"))
                if not sym or not d:
                    continue
                qty, px = num(pick(r, "BD_QTY_TRD", "qty", "quantity")), num(pick(r, "BD_TP_WATP", "watp", "price"))
                recs.append({"date": d, "disclosed": d, "kind": kind, "symbol": str(sym).strip(),
                             "client": str(pick(r, "BD_CLIENT_NAME", "clientName", "client", default="")).strip(),
                             "side": str(pick(r, "BD_BUY_SELL", "buySell", default="")).strip().upper()[:4],
                             "qty": qty, "price": px, "value": qty * px if qty and px else None,
                             "remarks": str(pick(r, "BD_REMARKS", "remarks", default="") or "")})
    if not recs:
        log("deals: no rows (layout changed? raw replies in cache\\alt)")
        return 0
    df = pl.DataFrame(recs, infer_schema_length=None).with_columns(pl.col("symbol").replace(ren).alias("company_id"))
    return save("deals", df, ["date", "kind", "symbol", "client", "side", "qty", "price"])


def do_insider(nse, start, end, ren):
    a = read_from("insider", "disclosed", start)
    recs, unknown, ins_fail = [], 0, 0
    for m0, m1 in months(a, end):
        closed = m1 < dt.date.today() - dt.timedelta(days=3)
        try:
            rep = cached_json(nse, f"pit_{m0:%Y%m%d}_{m1:%Y%m%d}", f"{API}/corporates-pit",
                              {"index": "equities", "from_date": f"{m0:%d-%m-%Y}", "to_date": f"{m1:%d-%m-%Y}"},
                              keep=closed)
        except Exception as ex:
            log(f"  insider {m0:%Y-%m}: {ex}")
            ins_fail += 1
            if ins_fail >= 3:
                log("  insider: 3 months failed in a row - NSE's endpoint has probably changed; skipped")
                break
            continue
        ins_fail = 0
        for r in rows_of(rep):
            sym = pick(r, "symbol")
            disc = day(pick(r, "date", "broadcastDate", "intimDt"))
            if not sym or not disc:
                unknown += 1
                continue
            recs.append({
                "disclosed": disc, "intimated": day(pick(r, "intimDt")), "symbol": str(sym).strip(),
                "person": str(pick(r, "acqName", default="")).strip(),
                "category": str(pick(r, "personCategory", default="")).strip(),
                "sec_type": str(pick(r, "secType", default="")).strip(),
                "side": str(pick(r, "tdpTransactionType", "acqType", default="")).strip(),
                "mode": str(pick(r, "acqMode", default="")).strip(),
                "qty": num(pick(r, "secAcq")), "value": num(pick(r, "secVal")),
                "before_pct": num(pick(r, "befAcqSharesPer")), "after_pct": num(pick(r, "afterAcqSharesPer")),
                "trade_from": day(pick(r, "acqfromDt")), "trade_to": day(pick(r, "acqtoDt"))})
    if unknown:
        log(f"insider: {unknown} rows without symbol / date skipped")
    if not recs:
        log("insider: no rows (layout changed? raw replies in cache\\alt)")
        return 0
    df = pl.DataFrame(recs, infer_schema_length=None).with_columns(pl.col("symbol").replace(ren).alias("company_id"))
    return save("insider", df, ["disclosed", "symbol", "person", "side", "qty", "trade_from"])


def _walk(obj, path=""):
    """(list name, record) for every dict with a symbol in a nested NSE reply."""
    if isinstance(obj, dict):
        if pick(obj, "symbol") and not any(isinstance(v, (list, dict)) for v in obj.values()):
            yield path, obj
        else:
            for k, v in obj.items():
                yield from _walk(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, path)


def do_surveillance(nse, start, end, ren):
    today = dt.date.today()
    recs = []
    for name, url in (("asm", f"{API}/reportASM"), ("gsm", f"{API}/reportGSM")):
        try:
            rep = cached_json(nse, f"{name}_{today:%Y%m%d}", url, None, keep=False)
        except Exception as ex:
            log(f"  {name}: {ex}")
            continue
        for path, r in _walk(rep):
            recs.append({"date": today, "symbol": str(pick(r, "symbol")).strip(),
                         "list": f"{name}:{path}" if path else name,
                         "stage": str(pick(r, "asmSurvIndicator", "gsmStage", "stage", "survDesc", default="") or "")})
    if not recs:
        log("surveillance: no rows (layout changed? raw replies in cache\\alt)")
        return 0
    df = pl.DataFrame(recs).with_columns(pl.col("symbol").replace(ren).alias("company_id"))
    return save("surveillance", df, ["date", "symbol", "list"])


def do_shareholding(nse, start, end, ren):
    n = 0
    try:
        rep = cached_json(nse, f"shp_{dt.date.today():%Y%m%d}", f"{API}/corporate-share-holdings-master",
                          {"index": "equities"}, keep=False)
        recs = []
        for r in rows_of(rep):
            sym, per = pick(r, "symbol"), day(pick(r, "date", "asOnDate"))
            if not sym or not per:
                continue
            recs.append({"symbol": str(sym).strip(), "period_end": per,
                         "disclosed": day(pick(r, "broadcastDate", "submissionDate", "date")),
                         "promoter_pct": num(pick(r, "pr_and_prgrp", "promoterAndPromoterGroup")),
                         "public_pct": num(pick(r, "public_val", "public")),
                         "employee_trust_pct": num(pick(r, "employeeTrusts"))})
        if recs:
            df = pl.DataFrame(recs, infer_schema_length=None).with_columns(pl.col("symbol").replace(ren).alias("company_id"))
            path = C.file_path("shareholding", "ref")
            if path.exists():
                df = pl.concat([C.conform(pl.read_parquet(path), "shareholding", keep_unknown=False),
                                C.conform(df, "shareholding", keep_unknown=False)], how="vertical_relaxed")
            df = df.unique(subset=["company_id", "period_end"], keep="last").sort(["company_id", "period_end"])
            df = df.with_columns((pl.col("promoter_pct") - pl.col("promoter_pct").shift(1).over("company_id"))
                                 .round(4).alias("promoter_pct_change"))
            n = save("shareholding", df, ["company_id", "period_end"])
        else:
            log("shareholding: no rows (layout changed? raw replies in cache\\alt)")
    except Exception as ex:
        log(f"  shareholding: {ex}")
    try:
        rep = cached_json(nse, f"pledge_{dt.date.today():%Y%m%d}", f"{API}/corporate-pledgedata", {"index": "equities"},
                          keep=False)
        names, unmatched = name_map(nse), 0
        recs = []
        for r in rows_of(rep):
            sym = pick(r, "symbol")
            if not sym and pick(r, "comName"):
                sym = names.get(norm_name(pick(r, "comName")))
                unmatched += sym is None
            disc = day(pick(r, "broadcastDt", "disclosureDate", "date"))
            if not sym or not disc:
                continue
            recs.append({"symbol": str(sym).strip(), "disclosed": disc,
                         "promoter_holding_pct": num(pick(r, "percPromoterHolding", "totPromoterHolding")),
                         "pledged_pct_of_promoter": num(pick(r, "percPromoterShares")),
                         "pledged_pct_of_total": num(pick(r, "percTotShares"))})
        if unmatched:
            log(f"  pledge: {unmatched} companies not matched to a symbol (name differs from NSE's equity list)")
        path = C.file_path("pledge", "ref")
        if path.exists():                                   # drop rows keyed by company NAME (before this fix)
            old = pl.read_parquet(path)
            bad = old.filter(pl.col("company_id").str.contains(" ") | (pl.col("company_id") != pl.col("company_id").str.to_uppercase()))
            if bad.height:
                atomic_write(old.join(bad.select("company_id", "disclosed"), on=["company_id", "disclosed"], how="anti"), path)
        if recs:
            df = pl.DataFrame(recs, infer_schema_length=None).with_columns(pl.col("symbol").replace(ren).alias("company_id"))
            save("pledge", df, ["company_id", "disclosed"])
        else:
            log("pledge: no rows (layout changed? raw replies in cache\\alt)")
    except Exception as ex:
        log(f"  pledge: {ex}")
    return n


def do_indexes(nse, start, end, ren):
    import pandas as pd
    today = dt.date.today()
    state_p = CACHE / "index_members.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {}
    recs = []
    for idx, url in INDEX_LISTS.items():
        try:
            data = nse.get(url)
            df = pd.read_csv(io.BytesIO(data), dtype=str) if data else None
        except Exception as ex:
            log(f"  index {idx}: {ex}")
            continue
        cols = {c.strip().lower(): c for c in df.columns} if df is not None else {}
        if "symbol" not in cols:
            log(f"  index {idx}: no list")
            continue
        now = sorted({str(s).strip().upper() for s in df[cols["symbol"]].dropna()})
        before = set(state.get(idx, []))
        if not before:
            recs += [{"date": today, "index": idx, "symbol": s, "change": "baseline"} for s in now]
        else:
            recs += [{"date": today, "index": idx, "symbol": s, "change": "added"} for s in sorted(set(now) - before)]
            recs += [{"date": today, "index": idx, "symbol": s, "change": "removed"} for s in sorted(before - set(now))]
        state[idx] = now
    state_p.parent.mkdir(parents=True, exist_ok=True)
    state_p.write_text(json.dumps(state), encoding="utf-8")
    real = [r for r in recs if r["change"] != "baseline"]
    if real:
        log("index changes: " + ", ".join(f"{r['index']} {r['change']} {r['symbol']}" for r in real))
    if not recs:
        return 0
    df = pl.DataFrame(recs).with_columns(pl.col("symbol").replace(ren).alias("company_id"))
    return save("index_changes", df, ["date", "index", "symbol", "change"])


# ---------------------------------------------------------------------------------------------------------------
# short selling + margin trading (MTF)  - added 18 Oct 2026
# ---------------------------------------------------------------------------------------------------------------
def _norm(k) -> str:
    return "".join(ch for ch in str(k).lower() if ch.isalnum())


def _csv_records(data: bytes) -> list[dict] | None:
    """Records from an NSE CSV download (None if the reply is not a CSV table)."""
    if not isinstance(data, (bytes, bytearray)) or data.lstrip()[:1] in (b"{", b"[") or b"," not in data[:600]:
        return None
    try:
        import pandas as pd
        df = pd.read_csv(io.BytesIO(data.lstrip(b"\xef\xbb\xbf")), dtype=str, encoding="utf-8", encoding_errors="replace")
    except Exception:
        return None
    df.columns = [str(c).strip().lstrip("﻿") for c in df.columns]
    return df.to_dict("records")


def do_short_selling(nse, start, end, ren):
    """Short sales reported by institutional investors, per stock per day (SEBI: institutions disclose upfront; NSE
    publishes the day's total per stock). Same NSE endpoint as bulk / block deals, optionType=short_selling."""
    a = read_from("short_selling", "date", start)
    have = last_date("short_selling", "date")
    done = {(d.year, d.month) for d in have_dates("short_selling", "date")} if (have and not REDO) else set()
    recs, fails, skipped = [], 0, 0
    for m0, m1 in months(a, end):
        closed = m1 < dt.date.today() - dt.timedelta(days=3)
        if closed and (m0.year, m0.month) in done and m1 < have - dt.timedelta(days=7):
            skipped += 1                      # a finished month already in the file
            continue
        rows, err = None, None
        p = CACHE / f"short_{m0:%Y%m%d}_{m1:%Y%m%d}.csv"
        try:
            data = p.read_bytes() if (closed and p.exists()) else nse.get(
                f"{API}/historicalOR/bulk-block-short-deals",
                {"optionType": "short_selling", "from": f"{m0:%d-%m-%Y}", "to": f"{m1:%d-%m-%Y}", "csv": "true"})
            rows = _csv_records(data)
            if rows is not None and not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
        except Exception as ex:
            err = ex
        if rows is None:                      # JSON fallback (may be capped like the deals API)
            try:
                rows = rows_of(cached_json(nse, f"short_{m0:%Y%m%d}_{m1:%Y%m%d}",
                                           f"{API}/historicalOR/bulk-block-short-deals",
                                           {"optionType": "short_selling", "from": f"{m0:%d-%m-%Y}",
                                            "to": f"{m1:%d-%m-%Y}"}, keep=closed))
            except Exception as ex:
                err = ex
        if not rows:
            fails += 1
            log(f"  short selling {m0:%Y-%m}: {err or 'no rows'}")
            if fails >= 3 and not recs:
                log("  short selling: 3 months failed in a row - endpoint changed? raw replies in cache\\alt; skipped")
                break
            continue
        fails = 0
        for r in rows:
            sym = pick(r, "SS_SYMBOL", "symbol")
            d = day(pick(r, "SS_DATE", "date", "mTIMESTAMP"))
            q = num(pick(r, "SS_QTY", "quantity", "qty", "quantitytraded", "shortsellqty"))
            if sym and d and q is not None:
                recs.append({"date": d, "disclosed": d, "symbol": str(sym).strip(), "qty": q,
                             "name": str(pick(r, "SS_NAME", "securityname", "name", default="") or "")})
    if skipped:
        log(f"  short selling: {skipped} finished month(s) already in the file - not read again")
    if not recs:
        if skipped:
            return rows_in("short_selling")
        log("short_selling: no rows (layout changed? raw replies in cache\\alt)")
        return 0
    df = (pl.DataFrame(recs, infer_schema_length=None)
          .group_by(["date", "disclosed", "symbol"]).agg(pl.col("qty").sum(), pl.col("name").first())
          .with_columns(pl.col("symbol").replace(ren).alias("company_id")))
    return save("short_selling", df, ["date", "symbol"])


MTF_REPORT = '[{"name":"CM - Margin Trading Disclosure","type":"archives","category":"capital-market","section":"equity"}]'


def _mtf_file(nse, d: dt.date, closed: bool) -> bytes | None:
    """NSE's daily Margin Trading Disclosure for day d (zip / csv / xls bytes), cached under cache\\alt\\mtf."""
    p = CACHE / "mtf" / f"{d:%Y%m%d}.bin"
    if p.exists():
        return p.read_bytes()
    tries = [(f"{API}/reports", {"archives": MTF_REPORT, "date": f"{d:%d-%b-%Y}", "type": "equity", "mode": "single"}),
             (f"https://nsearchives.nseindia.com/content/equities/mrg_trading_{d:%d%m%y}.zip", None),
             (f"https://nsearchives.nseindia.com/archives/equities/mrg_trading/mrg_trading_{d:%d%m%y}.zip", None),
             (f"https://nsearchives.nseindia.com/content/equities/mrg_trading_{d:%d%m%y}.csv", None)]
    for url, params in tries:
        try:
            data = nse.get(url, params)
        except Exception:
            continue
        if data and len(data) > 200 and data.lstrip()[:1] not in (b"{", b"[", b"<"):
            if closed:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            return data
    return None


def _mtf_table(data: bytes):
    """pandas DataFrame of the per-stock table inside the file (zip of csv/xls, csv or xls), header row found by
    looking for 'Symbol'."""
    import zipfile
    import pandas as pd
    blobs = []
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            blobs = [(n, z.read(n)) for n in z.namelist() if not n.endswith("/")]
    else:
        blobs = [("file", data)]
    for name, b in blobs:
        raw = None
        try:
            if name.lower().endswith((".xls", ".xlsx")) or b[:4] in (b"\xd0\xcf\x11\xe0", b"PK\x03\x04"):
                raw = pd.read_excel(io.BytesIO(b), header=None, dtype=str)
            else:                              # csv: start reading at the header line (title lines above it)
                lines = b.decode("latin-1").splitlines()
                h = next((j for j, ln in enumerate(lines[:60]) if "symbol" in [_norm(x) for x in ln.split(",")]), None)
                if h is None:
                    continue
                t = pd.read_csv(io.StringIO("\n".join(lines[h:])), dtype=str)
                t.columns = [str(x).strip() for x in t.columns]
                return t
        except Exception:
            continue
        for i in range(min(len(raw), 40)):
            cells = [_norm(x) for x in raw.iloc[i].tolist()]
            if "symbol" in cells:
                t = raw.iloc[i + 1:].copy()
                t.columns = [str(x).strip() for x in raw.iloc[i].tolist()]
                return t
    return None


def do_mtf(nse, start, end, ren):
    """Margin Trading Facility: shares bought with broker funding and still carried, per stock, end of each day
    (NSE 'CM - Margin Trading Disclosure'). Published after the day -> disclosed = next session."""
    a = read_from("mtf", "date", start)
    have = last_date("mtf", "date")
    allday = sessions(a, end)
    nexts = {d: allday[i + 1] for i, d in enumerate(allday[:-1])}       # disclosed = the next session
    got = have_dates("mtf", "date") if (have and not REDO) else set()
    days = [d for d in allday if d not in got or d >= have - dt.timedelta(days=7)]
    log(f"mtf: {len(days)} session(s) to fetch ({a} .. {end}; {len(allday) - len(days)} already in the file)")
    recs, misses = [], 0
    for i, d in enumerate(days):
        closed = d < dt.date.today() - dt.timedelta(days=3)
        data = _mtf_file(nse, d, closed)
        t = _mtf_table(data) if data else None
        if t is None:
            misses += 1
            if misses in (1, 5) or misses % 50 == 0:
                log(f"  mtf {d}: no file / table ({misses} missing so far)")
            if misses >= 10 and not recs:
                log("  mtf: 10 days in a row without a file - NSE's report name / URL has changed; skipped")
                break
            continue
        cols = {c: _norm(c) for c in t.columns}
        def col(*must, avoid=()):
            for c, n in cols.items():
                if all(m in n for m in must) and not any(x in n for x in avoid):
                    return c
            return None
        c_sym = col("symbol")
        c_qty = col("qty", "end") or col("quantity", "end") or col("qty", avoid=("begin", "fresh", "liquid"))
        c_amt = col("amt", "end") or col("amount", "end") or col("amt", avoid=("begin", "fresh", "liquid")) \
            or col("amount", avoid=("begin", "fresh", "liquid"))
        c_fresh, c_liq = col("fresh"), col("liquid")
        if not c_sym or not (c_qty or c_amt):
            if misses < 3:
                log(f"  mtf {d}: unknown layout, columns {list(t.columns)[:12]}")
            misses += 1
            continue
        nxt = nexts.get(d, d + dt.timedelta(days=1))
        for r in t.to_dict("records"):
            sym = str(r.get(c_sym) or "").strip()
            if not sym or sym.lower() in ("nan", "total", "symbol"):
                continue
            recs.append({"date": d, "disclosed": nxt, "symbol": sym,
                         "qty_financed": num(r.get(c_qty)) if c_qty else None,
                         "amount_financed_lakh": num(r.get(c_amt)) if c_amt else None,
                         "fresh_exposure": num(r.get(c_fresh)) if c_fresh else None,
                         "exposure_liquidated": num(r.get(c_liq)) if c_liq else None})
        if (i + 1) % 100 == 0:
            log(f"  mtf: {i + 1}/{len(days)} days")
    if not recs:
        if got and not days:
            return rows_in("mtf")
        log("mtf: no rows (NSE file name / layout? raw files in cache\\alt\\mtf)")
        return 0
    df = pl.DataFrame(recs, infer_schema_length=None).with_columns(pl.col("symbol").replace(ren).alias("company_id"))
    return save("mtf", df, ["date", "symbol"])



TASKS = {"delivery": do_delivery, "deals": do_deals, "insider": do_insider, "surveillance": do_surveillance,
         "shareholding": do_shareholding, "indexes": do_indexes, "short_selling": do_short_selling, "mtf": do_mtf}
HISTORY = ("delivery", "deals", "insider", "short_selling", "mtf")          # the ones with a backfill
REDO = False                                         # backfill --redo: re-read the whole period, not only what is missing


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["daily", "backfill", "show"])
    ap.add_argument("--start", default=None, help="backfill start YYYY-MM-DD (default 2019-01-01)")
    ap.add_argument("--only", default="", help="comma list: " + ",".join(TASKS))
    ap.add_argument("--redo", action="store_true", help="backfill: re-read the whole period (default: only the days "
                                                         "missing from the files, plus the last 7)")
    a = ap.parse_args(argv)
    names = [x.strip() for x in a.only.split(",") if x.strip()] or list(TASKS)
    if a.command == "show":
        for ds in ("delivery", "deals", "insider", "surveillance", "shareholding", "pledge", "index_changes",
                   "short_selling", "mtf"):
            p = C.file_path(ds, "ref")
            print(f"{ds:14s} " + (f"{pl.scan_parquet(p).select(pl.len()).collect().item():>10,} rows  {p}"
                                   if p.exists() else "not built yet"))
        return 0
    end = dt.date.today()
    if a.command == "backfill":
        global REDO
        REDO = a.redo
        start = dt.date.fromisoformat(a.start or "2019-01-01")
        names = [n for n in names if n in HISTORY]
    else:
        start = dt.date.today() - dt.timedelta(days=45)           # daily: recent window (cheap, catches late files)
    nse, ren, bad = NSE(), renames(), 0
    for n in names:
        t0 = time.time()
        try:
            rows = TASKS[n](nse, start, end, ren)
            log(f"{n}: done, {rows:,} rows in the file ({time.time() - t0:.0f}s)")
        except Exception as ex:
            bad += 1
            log(f"{n}: FAILED ({type(ex).__name__}: {ex}) - the other datasets still run")
    return 1 if bad == len(names) else 0


if __name__ == "__main__":
    raise SystemExit(main())
