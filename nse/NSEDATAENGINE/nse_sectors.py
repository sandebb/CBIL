"""
nse_sectors.py - the NSE sector of every stock in the database (contract dataset `sectors`, data_ref\\sectors.parquet).

Why
  The swing book's sector cap, the planned news sector filter and NSEMLENGINE all need ONE fixed list of sector
  names. NSE's own classification is used everywhere, so the names are always the same:
     macro_sector  >  sector (about 22, e.g. "Financial Services", "Capital Goods")  >  industry  >  basic_industry
  `sector` is the name the rest of the system uses.

Sources (both NSE; the PC needs internet, as for the daily update)
  1. index constituent lists (Nifty Total Market ~750 stocks, Nifty 500, Smallcap 250, Microcap 250): Symbol,
     ISIN, company name and Industry (= NSE sector)
  2. NSE's quote API for every other stock traded in the last 30 sessions of raw_ohlcv (industryInfo: macro,
     sector, industry, basic industry), at most --api-max (300) stocks per run, ~3 per second (~2 minutes).
     Stocks still missing are looked up on the next daily runs until every traded stock has a sector.
  A stock already in the file and not found this run keeps its old row (marked with its old `updated` date).

Outputs
  data_ref\\sectors.parquet                         one row per company_id (contract dataset `sectors`)
  NSEALGOENGINE\\config\\universes\\sectors.csv       Symbol, Industry - NSEALGOENGINE's sector map for every stock

Usage
  python nse_sectors.py                 full refresh weekly; on other days only stocks still missing a sector
                                        (run_update.bat calls this daily, after the price update)
  python nse_sectors.py --force         refresh now
  python nse_sectors.py --show          sector counts
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
from nse_common import HEADERS, atomic_write, pl

INDEX_LISTS = [
    ("total_market", "https://www.niftyindices.com/IndexConstituent/ind_niftytotalmarket_list.csv"),
    ("nifty500", "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"),
    ("smallcap250", "https://nsearchives.nseindia.com/content/indices/ind_niftysmallcap250list.csv"),
    ("microcap250", "https://nsearchives.nseindia.com/content/indices/ind_niftymicrocap250_list.csv"),
]
QUOTE_API = "https://www.nseindia.com/api/quote-equity?symbol={sym}"


def log(msg: str) -> None:
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}", flush=True)


def session():
    import requests
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get("https://www.nseindia.com/", timeout=20)          # cookies NSE's API checks for
    except Exception:
        pass
    return s


def from_index_lists(s) -> dict:
    """symbol -> {isin, company_name, sector, source}"""
    import pandas as pd
    out = {}
    for name, url in INDEX_LISTS:
        try:
            r = s.get(url, timeout=30)
            if r.status_code != 200 or b"Symbol" not in r.content[:500]:
                log(f"  {name}: HTTP {r.status_code} - skipped")
                continue
            df = pd.read_csv(io.BytesIO(r.content), dtype=str)
        except Exception as ex:
            log(f"  {name}: {ex} - skipped")
            continue
        cols = {c.strip().lower(): c for c in df.columns}
        sym, ind = cols.get("symbol"), cols.get("industry")
        if not (sym and ind):
            continue
        n = 0
        for _, r in df.iterrows():
            k = str(r[sym]).strip().upper()
            if k and k not in out and str(r[ind]).strip() not in ("", "nan"):
                out[k] = {"isin": str(r.get(cols.get("isin code", ""), "") or "").strip() or None,
                          "company_name": str(r.get(cols.get("company name", ""), "") or "").strip() or None,
                          "sector": str(r[ind]).strip(), "source": f"index:{name}"}
                n += 1
        log(f"  {name}: {n} new stocks")
    return out


def from_quote_api(s, symbols, limit: int) -> dict:
    out, fails = {}, 0
    for i, sym in enumerate(symbols[:limit]):
        try:
            time.sleep(0.35)
            r = s.get(QUOTE_API.format(sym=sym), timeout=20, headers={"Accept": "application/json"})
            if r.status_code in (401, 403):
                s = session()
                r = s.get(QUOTE_API.format(sym=sym), timeout=20, headers={"Accept": "application/json"})
            d = r.json() if r.status_code == 200 else {}
        except Exception:
            d = {}
        ii = d.get("industryInfo") or {}
        info = d.get("info") or {}
        if ii.get("sector"):
            out[sym] = {"isin": info.get("isin"), "company_name": info.get("companyName"),
                        "macro_sector": ii.get("macro"), "sector": ii.get("sector"), "industry": ii.get("industry"),
                        "basic_industry": ii.get("basicIndustry"), "source": "quote_api"}
        else:
            fails += 1
        if (i + 1) % 100 == 0:
            log(f"  quote API: {i + 1}/{min(limit, len(symbols))} ({fails} without data)")
    return out


def recent_symbols(days: int = 30) -> list[str]:
    lf = C.read("raw_ohlcv", columns=["company_id", "datetime"], lazy=True)
    last = lf.select(pl.col("datetime").max()).collect().item()
    cut = last - dt.timedelta(days=int(days * 1.5))
    return sorted(lf.filter(pl.col("datetime") >= cut).select("company_id").unique().collect()["company_id"].to_list())


def export_for_algo(df: pl.DataFrame) -> None:
    for c in (C.location("algo_engine") / "config" / "universes",):
        if c.parent.exists():
            c.mkdir(parents=True, exist_ok=True)
            out = df.filter(pl.col("sector").is_not_null()).select(
                pl.col("company_id").alias("Symbol"), pl.col("sector").alias("Industry")).sort("Symbol")
            out.write_csv(c / "sectors.csv")
            log(f"NSEALGOENGINE sector map: {c / 'sectors.csv'} ({out.height} stocks)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="refresh even if the file is recent")
    ap.add_argument("--max-age-days", type=int, default=7)
    ap.add_argument("--api-max", type=int, default=300, help="quote-API lookups per run (stocks not in index lists)")
    ap.add_argument("--show", action="store_true", help="print sector counts and exit")
    a = ap.parse_args(argv)
    path = C.file_path("sectors", "ref")
    old = pl.read_parquet(path) if path.exists() else None
    if a.show:
        if old is None:
            print("no sectors file yet - run: python nse_sectors.py --force")
            return 1
        print(old.group_by("sector").len().sort("len", descending=True))
        return 0
    try:
        want = recent_symbols()
    except Exception as ex:
        log(f"raw_ohlcv not readable ({ex}) - index lists only")
        want = []
    have = set(old["company_id"].to_list()) if old is not None else set()
    fresh = old is not None and (dt.datetime.now() - dt.datetime.fromtimestamp(path.stat().st_mtime)).days < a.max_age_days
    s = session()
    got = {}
    if fresh and not a.force:
        missing = [x for x in want if x not in have]
        if not missing:
            log("sectors.parquet is recent and complete - nothing to do (--force to refresh)")
            return 0
        log(f"sectors.parquet is recent; {len(missing)} traded stocks still without a sector -> quote API "
            f"(max {a.api_max} per run)")
    else:
        log("sector refresh: index lists ...")
        got = from_index_lists(s)
        missing = [x for x in want if x not in got]
        log(f"{len(want)} stocks traded recently; {len(missing)} not in the index lists -> quote API "
            f"(max {a.api_max} per run; the rest on the next runs)")
    tried = path.parent / "cache" / "sectors_not_found.json"      # stocks the API had no sector for
    seen = json.loads(tried.read_text()) if tried.exists() else {}
    cutoff = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    missing = [x for x in missing if seen.get(x, "") < cutoff]       # retry those only after 30 days
    if missing:
        found = from_quote_api(s, missing, a.api_max)
        got.update(found)
        for x in missing[:a.api_max]:
            if x not in found:
                seen[x] = dt.date.today().isoformat()
        tried.parent.mkdir(parents=True, exist_ok=True)
        tried.write_text(json.dumps(seen, indent=0))
    if not got:
        if fresh and not a.force:
            log("no new sectors found this run (those stocks are retried after 30 days)")
            return 0
        log("nothing downloaded (no internet / NSE blocked?) - the old file is kept")
        return 1
    today = dt.date.today()
    rows = [{"company_id": k, **v, "updated": today} for k, v in got.items()]
    new = pl.DataFrame(rows, infer_schema_length=None)
    if old is not None:
        keep = old.filter(~pl.col("company_id").is_in(new["company_id"].to_list()))
        new = pl.concat([C.conform(new, "sectors", keep_unknown=False),
                         C.conform(keep, "sectors", keep_unknown=False)], how="vertical_relaxed")
    new = C.conform(new, "sectors", log=log, keep_unknown=False).sort("company_id")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(new, path)
    log(f"wrote {path}: {new.height} stocks, {new['sector'].n_unique()} sectors "
        f"({sum(1 for x in want if x not in set(new['company_id'].to_list()))} recent stocks still without one)")
    export_for_algo(new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
