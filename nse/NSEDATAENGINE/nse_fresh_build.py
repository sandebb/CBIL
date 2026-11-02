"""
nse_fresh_build.py - build a fresh NSE equity dataset (default: last 5 years) straight from NSE,
with a full audit trail of every adjustment made to the data.

Why a second script
  nse_updater.py extends your existing historical file. This script starts from nothing: every price
  comes from NSE's own daily files, and every change made to those prices is logged. The result can be
  compared line by line with any other data source, and every difference traced to a logged decision.

Sources (for each day the first one that exists is used)
  1. UDiFF CM bhavcopy        NSE's format from 8 Jul 2024                   has ISIN
  2. Legacy CM bhavcopy       cmDDMONYYYYbhav.csv.zip, before July 2024      has ISIN
  3. Full bhavcopy            sec_bhavdata_full_DDMMYYYY.csv                 no ISIN (fallback only)
  + NSE daily index close file (Nifty 50, India VIX)
  + NSE symbol-change list and corporate-action announcements (renames, split/bonus ratios)
  + Yahoo Finance (yfinance) for S&P 500, FTSE, DAX, Nikkei, crude, gold, USD/INR

Files - all inside %NSEDATAENGINE_FRESH_DIR% (default <project>\\data_fresh; project = %NSEDATAENGINE_HOME%, or
the folder of this script). File names: contract\\schema.yaml.
  features_5y.parquet          same feature columns as features_output_clean.parquet (contract dataset `features`)
  raw_ohlcv.parquet            adjusted OHLCV + the ORIGINAL NSE values (nse_*), ISIN, series, source, adj_factor
  market.parquet               index / global columns per day
  audit\\audit_log.csv          one row per adjustment / anomaly (also audit_log.parquet)
  audit\\audit_summary.txt      human-readable summary of the audit log
  audit\\run_manifest.json      parameters, versions, counts, sources used
  cache\\                       every downloaded file (re-runs never re-download)
  logs\\build_log.txt           progress log

Audit event types (audit_log.csv, column event_type)
  CORPORATE_ACTION_ADJUSTED      split / bonus / consolidation: earlier prices x factor, volumes / factor.
                                 source = nse_announcement (ratio from NSE's notice, confirmed by the price
                                 gap) or price_gap_heuristic (NSE's list unavailable for that quarter)
  CORPORATE_ACTION_NOT_ADJUSTED  announced, but the price gap disagrees with the ratio, or a rights issue
  CORPORATE_ACTION_UNPARSED      split/bonus notice whose ratio could not be read (review manually)
  PREV_CLOSE_GAP                 NSE's previous close differs >2% from our last close (review)
  TICKER_RENAME                  same ISIN traded under a new symbol (or NSE symbol-change list)
  ISIN_CHANGE                    same symbol traded under a new ISIN
  SYMBOL_COLLISION               ISIN moved to a symbol that already belongs to another company
  NEW_LISTING                    first trading day of a company inside the build window
  LAST_TRADED                    company stopped trading before the end of the window (delisted / suspended)
  SERIES_CHANGE                  moved between EQ / BE / BZ (e.g. into trade-for-trade)
  DUPLICATE_SERIES               same company in two series on one day; first by priority EQ>BE>BZ kept
  OHLC_FIXED                     open/close outside the high-low range; high/low widened to contain them
  SUSPECT_MOVE                   >40% one-day move left after adjustment (bad print / unadjusted action / real)
  SPECIAL_SESSION                weekend trading session (Budget day, Muhurat)
  NO_DATA_DAY                    weekday with no NSE file (exchange holiday)
  SOURCE_FALLBACK                day built from a non-primary NSE file

Only equity shares are kept: ISIN INE....01.... in series EQ/BE/BZ (no ETFs, SME, rights entitlements).

Usage
  python nse_fresh_build.py build                     last 5 years up to today (or yesterday before 7pm IST)
  python nse_fresh_build.py build --years 3
  python nse_fresh_build.py build --start 2021-10-01 --end 2026-09-30
  python nse_fresh_build.py build --workers 6         parallel download threads (default 4)
Re-running `build` later downloads only the new days (everything else is cached) and regenerates the
dataset and the audit log from scratch, so the log always matches the data exactly. The start date is
remembered in the manifest, so the window grows rather than rolls; pass --start to change it.
"""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import argparse
import datetime as dt
import io
import json
import os
import platform
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Shared module next to this script (see the same note in nse_updater.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from nse_common import (  # noqa: E402  (nse_common installs missing packages on import)
    F, I, MKT, HEADERS, URL_UDIFF, URL_CA, GAP_LOG_THRESHOLD, HEURISTIC_RATIOS, HEURISTIC_TOL, HOME_ENV_VAR,
    HelpfulParser, parse_args_or_help,
    project_home, atomic_write, parse_date, all_days, default_end, resolve_chain, is_equity_share,
    parse_index_file, parse_ca_factor, is_price_action_subject, resolve_ca_group, global_markets, finish_market_columns,
    write_features, np, pl, C,
)

EXAMPLES = r"""
examples:
  build the last 5 years (first run downloads everything; later runs only what is new):
    python nse_fresh_build.py build

  a different window:
    python nse_fresh_build.py build --years 3
    python nse_fresh_build.py build --start 2021-10-01 --end 2026-09-30

  more parallel download threads (default 4; NSE is still limited to ~3 requests/second):
    python nse_fresh_build.py build --workers 6

  rebuild from files already downloaded, without touching the internet:
    python nse_fresh_build.py build --offline

  use another data folder / feature file:
    python nse_fresh_build.py --data-dir D:\nse\data_fresh build
    python nse_fresh_build.py --out D:\exports\features_5y.parquet build

  help for the build command:
    python nse_fresh_build.py build -h
"""

SCRIPT_VERSION = "1.4"
SERIES_PRIORITY = {"EQ": 0, "BE": 1, "BZ": 2}     # if a company appears in two series on one day, keep EQ first
SUSPECT_MOVE = 0.40                                # one-day moves above 40% are flagged for review
MIN_REQUEST_GAP = 0.3     # seconds between any two NSE requests, across ALL threads (polite, avoids blocking)
UDIFF_START = dt.date(2024, 7, 8)                  # NSE switched to the UDiFF file format on this date
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
URL_LEGACY = "https://nsearchives.nseindia.com/content/historical/EQUITIES/{y}/{m}/cm{dd}{m}{y}bhav.csv.zip"
URL_FULL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv"
URL_INDEX = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{d:%d%m%Y}.csv"
URL_SYMCHG = "https://nsearchives.nseindia.com/content/equities/symbolchange.csv"
AUDIT_SCHEMA = {'event_type': pl.String, 'date': pl.Date, 'company_id': pl.String, 'symbol_at_event': pl.String,
                'isin': pl.String, 'old_value': pl.String, 'new_value': pl.String, 'factor': F,
                'rows_affected': I, 'source': pl.String, 'details': pl.String}


class Paths:
    """Every file location, derived from one data folder so the project can be moved as a unit."""

    def __init__(self, data_dir, out=None):
        self.data = Path(data_dir)
        # file names come from the contract (contract\\schema.yaml, variant fresh)
        self.out = Path(out) if out else C.file_path('features', 'fresh', folder=self.data)
        self.raw = C.file_path('raw_ohlcv', 'fresh', folder=self.data)
        self.market = C.file_path('market', 'fresh', folder=self.data)
        self.cache = self.data / "cache"
        self.audit = self.data / "audit"
        self.logs = self.data / "logs"
        self.no_data = self.cache / "no_data.txt"          # (source, day) pairs NSE has no file for
        for p in (self.data, self.audit, self.logs, *(self.cache / s for s in ("udiff", "legacy", "full", "index", "ref"))):
            p.mkdir(parents=True, exist_ok=True)


def log(paths, msg):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    with open(paths.logs / "build_log.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")


# =====================================================================================================
# DOWNLOADER (thread-safe)
# -----------------------------------------------------------------------------------------------------
# Five years is ~1,800 calendar days x 2 files. Downloading is I/O-bound (waiting on the network), so
# several threads overlap the waiting. A shared lock enforces MIN_REQUEST_GAP between ANY two requests,
# so the parallelism hides latency without hitting NSE harder than ~3 requests per second.
# requests.Session is not guaranteed thread-safe, so each thread keeps its own session (threading.local).
# =====================================================================================================
class NSE:
    def __init__(self, paths, offline=False):
        self.paths, self.offline = paths, offline
        self._local = threading.local()
        self._lock = threading.Lock()
        self._last = 0.0
        self.no_data = set()
        if paths.no_data.exists():
            self.no_data = {x.strip() for x in paths.no_data.read_text().splitlines() if x.strip()}

    def _session(self, fresh=False):
        """Per-thread session; the home-page visit collects the cookies NSE's API checks for."""
        if fresh or getattr(self._local, "s", None) is None:
            import requests
            s = requests.Session()
            s.headers.update(HEADERS)
            try:
                s.get("https://www.nseindia.com/", timeout=20)
            except Exception:
                pass
            self._local.s = s
        return self._local.s

    def _throttle(self):
        """Global rate limit shared by all threads."""
        with self._lock:
            wait = self._last + MIN_REQUEST_GAP - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()

    def get(self, url, accept_json=False):
        """Bytes, None for 404 (file does not exist), exception after 4 failed attempts."""
        last = None
        for attempt in range(4):
            try:
                self._throttle()
                hdr = {"Accept": "application/json"} if accept_json else {}
                r = self._session(fresh=attempt > 0).get(url, timeout=60, headers=hdr)
                if r.status_code == 200 and r.content:
                    return r.content
                if r.status_code == 404:
                    return None
                last = f"HTTP {r.status_code}"
            except Exception as e:
                last = repr(e)
            time.sleep(3 * (attempt + 1))
        raise RuntimeError(f"download failed for {url}: {last}")

    def fetch(self, url, cache, key, day):
        """Cached download with holiday memory (only for days at least 3 days old - NSE can publish late).
        Files are written as .part and renamed, so a crash never leaves a truncated file in the cache."""
        if cache.exists():
            return cache.read_bytes()
        if self.offline or key in self.no_data:
            return None
        data = self.get(url)
        if data is None:
            if (dt.date.today() - day).days >= 3:
                with self._lock:
                    self.no_data.add(key)
                    with open(self.paths.no_data, "a") as f:
                        f.write(key + "\n")
            return None
        tmp = cache.with_name(cache.name + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, cache)
        return data

    def _sources(self, day):
        """The three stock-file sources in order of preference for this day."""
        udiff = ("udiff", URL_UDIFF.format(d=day), self.paths.cache / "udiff" / f"{day:%Y%m%d}.csv.zip")
        legacy = ("legacy", URL_LEGACY.format(y=day.year, m=MON[day.month - 1], dd=f"{day.day:02d}"),
                  self.paths.cache / "legacy" / f"{day:%Y%m%d}.csv.zip")
        full = ("full", URL_FULL.format(d=day), self.paths.cache / "full" / f"{day:%Y%m%d}.csv")
        return [udiff, legacy, full] if day >= UDIFF_START else [legacy, udiff, full]

    def stock_file(self, day):
        """(source_name, bytes) of the first source that has the day, or (None, None)."""
        for name, url, cache in self._sources(day):
            data = self.fetch(url, cache, f"{name}:{day.isoformat()}", day)
            if data is not None:
                return name, data
        return None, None

    def index_file(self, day):
        return self.fetch(URL_INDEX.format(d=day), self.paths.cache / "index" / f"{day:%Y%m%d}.csv",
                          f"index:{day.isoformat()}", day)


def _read_csv_text(data, zipped):
    """Read an NSE CSV (optionally zipped) as text columns with trimmed names and values."""
    if zipped:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            data = z.read(z.namelist()[0])
    df = pl.read_csv(data, infer_schema_length=0, truncate_ragged_lines=True)
    df = df.rename({c: c.strip() for c in df.columns})
    return df.with_columns(pl.col(pl.String).str.strip_chars())


def file_trade_date(source, data):
    """The trading date printed INSIDE an NSE stock file (None if it cannot be read).

    Why: for a holiday or weekend NSE's sec_bhavdata_full URL does not answer "not found" - it serves
    the previous trading day's file again (e.g. the URL for Sunday 6 Oct 2019 returns Friday 4 Oct's
    prices). Taken at face value that creates a fake trading day with copied prices: ~60 per year, which
    distorts returns, volatility, session counts and leaves the market table with empty Nifty values.
    The build therefore keeps a file only if the date inside it is the day it was requested for."""
    col = {"udiff": "TradDt", "legacy": "TIMESTAMP"}.get(source, "DATE1")
    try:
        df = _read_csv_text(data, source != "full")
        if col not in df.columns or df.height == 0:
            return None
        vals = df.get_column(col).drop_nulls().unique().to_list()
        dates = {parse_date(v) for v in vals[:5]} - {None}
        return dates.pop() if len(dates) == 1 else None
    except Exception:
        return None


def parse_stock_file(source, data):
    """Normalise any of the three NSE formats to: symbol, isin, series, open, high, low, close, prev_close,
    volume - equity shares only, sorted so EQ comes before BE/BZ for the same company."""
    num = lambda c: pl.col(c).cast(F, strict=False)
    if source == "udiff":
        df = _read_csv_text(data, True)
        if "FinInstrmTp" in df.columns:
            df = df.filter(pl.col("FinInstrmTp") == "STK")
        cols = dict(symbol="TckrSymb", isin="ISIN", series="SctySrs", open="OpnPric", high="HghPric", low="LwPric",
                    close="ClsPric", prev_close="PrvsClsgPric", volume="TtlTradgVol")
    elif source == "legacy":
        df = _read_csv_text(data, True)
        cols = dict(symbol="SYMBOL", isin="ISIN", series="SERIES", open="OPEN", high="HIGH", low="LOW",
                    close="CLOSE", prev_close="PREVCLOSE", volume="TOTTRDQTY")
    else:
        df = _read_csv_text(data, False)
        df = df.with_columns(pl.lit(None, dtype=pl.String).alias("ISIN"))   # this format has no ISIN
        cols = dict(symbol="SYMBOL", isin="ISIN", series="SERIES", open="OPEN_PRICE", high="HIGH_PRICE",
                    low="LOW_PRICE", close="CLOSE_PRICE", prev_close="PREV_CLOSE", volume="TTL_TRD_QNTY")
    missing = [v for v in cols.values() if v not in df.columns]
    if missing:
        raise RuntimeError(f"{source} file has an unexpected format, missing columns {missing}")
    out = df.select(
        pl.col(cols["symbol"]).alias("symbol"), pl.col(cols["isin"]).alias("isin"), pl.col(cols["series"]).alias("series"),
        num(cols["open"]).alias("open"), num(cols["high"]).alias("high"), num(cols["low"]).alias("low"),
        num(cols["close"]).alias("close"), num(cols["prev_close"]).alias("prev_close"),
        num(cols["volume"]).cast(I).alias("volume"),
    ).filter(pl.col("series").is_in(list(SERIES_PRIORITY)), pl.col("close") > 0)
    out = out.with_columns(pl.when(pl.col("isin") == "").then(None).otherwise(pl.col("isin")).alias("isin"))
    # Equity shares only (see nse_common.is_equity_share). Files without an ISIN can only drop rights
    # entitlements by their ticker pattern (XXX-RE, XXX-RE1).
    out = out.filter(pl.when(pl.col("isin").is_null())
                       .then(~pl.col("symbol").str.contains(r"-RE\d*$"))
                       .otherwise(is_equity_share(pl.col("isin"))))
    return out.with_columns(pl.col("series").replace_strict(SERIES_PRIORITY, default=9).alias("_p")) \
              .sort("_p", "symbol").drop("_p")


# =====================================================================================================
# REFERENCE DATA (best effort - the build still works if these cannot be downloaded)
# =====================================================================================================
def symbol_changes(nse, paths):
    """NSE's official symbol-change list -> [(date, old, new)]. Needed for days built from the ISIN-less
    fallback file, where a rename cannot be seen through the ISIN. Parsed loosely (last 3 columns are
    old symbol, new symbol, date) because NSE's header has varied."""
    cache = paths.cache / "ref" / "symbolchange.csv"
    if not cache.exists() and not nse.offline:
        try:
            data = nse.get(URL_SYMCHG)
            if data:
                cache.write_bytes(data)
        except Exception as e:
            log(paths, f"symbol-change list unavailable: {e!r}")
    if not cache.exists():
        return []
    out = []
    for line in cache.read_bytes().decode("utf-8", "replace").splitlines():
        parts = [p.strip().strip('"') for p in line.split(",")]
        if len(parts) < 4:
            continue
        d = parse_date(parts[-1])
        old, new = parts[-3].upper(), parts[-2].upper()
        if d and old and new and old != new:
            out.append((d, old, new))
    return out


def corporate_actions(nse, paths, start, end):
    """NSE corporate-action announcements -> (DataFrame[symbol, isin, ex_date, subject], failed quarters).
    One API query per calendar quarter; completed quarters are cached."""
    rows, failed = [], []
    a = dt.date(start.year, ((start.month - 1) // 3) * 3 + 1, 1)
    while a <= end:
        nxt = dt.date(a.year + (1 if a.month == 10 else 0), 1 if a.month == 10 else a.month + 3, 1)
        b = nxt - dt.timedelta(days=1)
        cache = paths.cache / "ref" / f"corporate_actions_{a:%Y%m%d}.json"
        data = cache.read_bytes() if cache.exists() else None
        if data is None and not nse.offline:
            try:
                data = nse.get(URL_CA.format(a=a, b=b), accept_json=True)
                if data and data.lstrip().startswith(b"[") and b < dt.date.today() - dt.timedelta(days=7):
                    cache.write_bytes(data)
            except Exception as e:
                log(paths, f"corporate-action list {a} .. {b} unavailable: {e!r}")
                data = None
        try:
            items = json.loads(data) if data else None
        except Exception:
            items = None
        if not isinstance(items, list):
            failed.append((a, b))
        else:
            for r in items:
                d = parse_date(str(r.get("exDate", "")))
                if d:
                    rows.append({"symbol": str(r.get("symbol", "")).strip().upper(), "isin": r.get("isin") or None,
                                 "ex_date": d, "subject": str(r.get("subject", "")).strip()})
        a = nxt
    df = pl.DataFrame(rows, schema={"symbol": pl.String, "isin": pl.String, "ex_date": pl.Date, "subject": pl.String})
    return df.unique(), failed


# =====================================================================================================
# BUILD
# =====================================================================================================
def cmd_build(args, paths):
    t0 = time.time()
    lg = lambda m: log(paths, m)
    manifest_path = paths.audit / "run_manifest.json"
    prev = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    end = dt.date.fromisoformat(args.end) if args.end else default_end()
    # Start date: --start, else the one remembered from the last build (window grows), else end - N years.
    if args.start:
        start = dt.date.fromisoformat(args.start)
    elif prev.get("start"):
        start = dt.date.fromisoformat(prev["start"])
    else:
        try:
            start = end.replace(year=end.year - args.years)
        except ValueError:                       # end is 29 Feb and the target year is not a leap year
            start = end.replace(year=end.year - args.years, day=28)
    days = list(all_days(start, end))
    nse = NSE(paths, offline=args.offline)
    log(paths, f"build {start} .. {end}: {len(days)} calendar days, {args.workers} download threads")

    # ---- 1. download everything in parallel (cached files are skipped instantly) ----------------------
    stock, index_raw = {}, {}

    def job(day):
        return day, nse.stock_file(day), nse.index_file(day)

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(job, d) for d in days]
        for fut in as_completed(futs):
            day, (src, data), idx = fut.result()
            stock[day] = (src, data)
            index_raw[day] = idx
            done += 1
            if done % 100 == 0 or done == len(days):
                log(paths, f"  downloaded/checked {done}/{len(days)} days")

    symchg = symbol_changes(nse, paths)
    ca_ann, ca_failed = corporate_actions(nse, paths, start, end)
    log(paths, f"reference data: {len(symchg)} NSE symbol changes, {ca_ann.height} corporate-action announcements"
               + (f"; corporate-action list unavailable for {len(ca_failed)} quarter(s)" if ca_failed else ""))

    # ---- 2. process days in date order: identity, sanity checks, audit events ---------------------------
    # Processing must be sequential (each day depends on what was seen before), but it is fast: the files
    # are already on disk.
    audit = []

    def ev(event_type, day, cid, sym=None, isin=None, old=None, new=None, factor=None, rows=None, source=None, details=None):
        """Append one audit event (see the event list in the module docstring)."""
        audit.append(dict(event_type=event_type, date=day, company_id=cid, symbol_at_event=sym or cid, isin=isin,
                          old_value=None if old is None else str(old), new_value=None if new is None else str(new),
                          factor=factor, rows_affected=rows, source=source, details=details))

    symchg_by_new = {}
    for d, old, new in symchg:
        symchg_by_new.setdefault(new, []).append((d, old))

    # Identity state. ISIN = stable identity; company_id = ticker, which can change.
    cid2isin, isin2cid, last_close, last_series, first_seen, renames = {}, {}, {}, {}, {}, {}
    rows, sources_used = [], {}
    for day in days:
        src, data = stock[day]
        if src is None:
            if day.weekday() < 5:
                ev("NO_DATA_DAY", day, None, details="no NSE file for this weekday (holiday)")
            continue
        inside = file_trade_date(src, data)
        if inside is not None and inside != day:
            # a non-trading day for which NSE served another day's file - not a session
            ev("STALE_FILE_SKIPPED", day, None, source=src,
               details=f"file requested for {day} contains {inside} prices - not a trading day, skipped")
            continue
        if day.weekday() >= 5:
            ev("SPECIAL_SESSION", day, None, source=src, details=f"weekend trading session ({day:%A})")
        primary = "udiff" if day >= UDIFF_START else "legacy"
        if src != primary:
            ev("SOURCE_FALLBACK", day, None, source=src, details=f"primary format '{primary}' not available")
        sources_used[src] = sources_used.get(src, 0) + 1
        b = parse_stock_file(src, data)
        ts = dt.datetime(day.year, day.month, day.day)
        seen = {}
        for sym, isin, series, o, h, l, c, pc, v in b.iter_rows():
            # ---------- identity: which company history does this row belong to? ----------
            if isin and isin in isin2cid:
                cid = isin2cid[isin]
                if sym != cid:
                    if sym in cid2isin:
                        # the new ticker already belongs to another company - keep the old name, flag it
                        ev("SYMBOL_COLLISION", day, cid, sym, isin, cid, sym,
                           details=f"ISIN {isin} now trades as {sym}, a symbol already used by another company; kept as {cid}")
                    else:
                        renames[cid] = sym
                        ev("TICKER_RENAME", day, sym, sym, isin, cid, sym, source=src, details="detected by ISIN")
                        cid2isin[sym] = cid2isin.pop(cid, isin)
                        for dct in (last_close, last_series, first_seen):
                            if cid in dct:
                                dct[sym] = dct.pop(cid)
                        isin2cid = {i: (sym if x == cid else x) for i, x in isin2cid.items()}
                        cid = sym
            elif sym in cid2isin:
                cid = sym
                old = cid2isin.get(sym)
                if isin and old and old != isin:
                    ev("ISIN_CHANGE", day, sym, sym, isin, old, isin, source=src)
                    isin2cid.pop(old, None)
                if isin:
                    cid2isin[sym] = isin
                    isin2cid[isin] = sym
            else:
                cid = sym
                # Unknown ticker: a rename per NSE's symbol-change list (needed when the file has no ISIN)?
                for d0, old in sorted(symchg_by_new.get(sym, []), reverse=True):
                    if old in cid2isin and d0 <= day + dt.timedelta(days=7):
                        renames[old] = sym
                        ev("TICKER_RENAME", day, sym, sym, isin or cid2isin.get(old), old, sym, source=src,
                           details=f"NSE symbol-change list (effective {d0})")
                        cid2isin[sym] = isin or cid2isin.pop(old, None)
                        cid2isin.pop(old, None)
                        for dct in (last_close, last_series, first_seen):
                            if old in dct:
                                dct[sym] = dct.pop(old)
                        isin2cid = {i: (sym if x == old else x) for i, x in isin2cid.items()}
                        break
                cid2isin.setdefault(sym, isin)
                if isin:
                    cid2isin[sym] = isin
                    isin2cid[isin] = sym
            # ---------- one row per company per day ----------
            if cid in seen:
                ev("DUPLICATE_SERIES", day, cid, sym, isin, seen[cid], series,
                   details=f"also traded in {series}; kept {seen[cid]}")
                continue
            seen[cid] = series
            if cid not in first_seen:
                first_seen[cid] = day
                if day > days[0] + dt.timedelta(days=7):    # not just "first day of the window"
                    ev("NEW_LISTING", day, cid, sym, isin, new=series, source=src)
            if cid in last_series and last_series[cid] != series:
                ev("SERIES_CHANGE", day, cid, sym, isin, last_series[cid], series, source=src)
            last_series[cid] = series
            # ---------- NSE's prev close vs ours: logged for review only ----------
            lc = last_close.get(cid)
            if lc and pc and lc > 0 and abs(pc / lc - 1) > GAP_LOG_THRESHOLD:
                ev("PREV_CLOSE_GAP", day, cid, sym, isin, lc, pc, factor=pc / lc, source=src,
                   details=f"NSE prev close differs {(pc / lc - 1) * 100:+.2f}% from our last close")
            last_close[cid] = c
            # ---------- OHLC sanity: high/low must contain open and close ----------
            hi, lo = max(o, h, l, c), min(o, h, l, c)
            if hi != h or lo != l:
                ev("OHLC_FIXED", day, cid, sym, isin, f"O={o} H={h} L={l} C={c}", f"H={hi} L={lo}", source=src)
            # adjusted columns (changed later by corporate actions) + the original NSE values, kept untouched
            rows.append((cid, isin, series, src, ts, o, hi, lo, c, v, o, h, l, c, pc))
    if not rows:
        sys.exit("no stock data found for the requested window - check the log for download errors")

    raw = pl.DataFrame(rows, orient='row', schema={
        'company_id': pl.String, 'isin': pl.String, 'series': pl.String, 'source': pl.String, 'datetime': pl.Datetime('ns'),
        'open': F, 'high': F, 'low': F, 'close': F, 'volume': I,
        'nse_open': F, 'nse_high': F, 'nse_low': F, 'nse_close': F, 'nse_prev_close': F})
    ren = resolve_chain(renames)
    if ren:
        raw = raw.with_columns(pl.col('company_id').replace(ren))   # whole history under the latest ticker
    # latest ISIN per company onto every row (rows from ISIN-less files get it too)
    isin_map = raw.filter(pl.col('isin').is_not_null()).sort('datetime').group_by('company_id').agg(pl.col('isin').last().alias('_i'))
    raw = raw.join(isin_map, on='company_id', how='left').with_columns(pl.coalesce('isin', '_i').alias('isin')).drop('_i')
    raw = raw.unique(['company_id', 'datetime'], keep='first')

    # ---- 3. corporate-action back-adjustment (method explained in nse_updater.apply_corporate_actions) --
    raw = raw.sort('company_id', 'datetime').with_columns(prev_close=pl.col('close').shift(1).over('company_id'))
    latest_isin = raw.filter(pl.col('isin').is_not_null()).group_by('company_id').agg(pl.col('isin').last())
    isin2final = dict(zip(latest_isin['isin'], latest_isin['company_id']))
    companies = set(raw['company_id'].unique().to_list())
    px_by = {c: (g['datetime'].to_numpy(), g['open'].to_numpy(), g['prev_close'].to_numpy())
             for (c,), g in raw.select('company_id', 'datetime', 'open', 'prev_close')
                               .partition_by('company_id', as_dict=True).items()}

    def observed(cid, exd):
        """open on the ex-date / previous close - the gap the market actually showed."""
        if cid not in px_by:
            return None
        d, o, pc = px_by[cid]
        i = np.searchsorted(d, np.datetime64(dt.datetime(exd.year, exd.month, exd.day), 'ns'))
        if i >= len(d) or pc[i] is None or np.isnan(pc[i]) or not pc[i]:
            return None
        return float(o[i] / pc[i])

    events = []      # (company_id, ex_date, factor, symbol, isin, method, description, observed)
    # All notices of one company on one ex-date are decided together (bonus + split on the same day
    # multiply; preference-share bonuses ignored; demergers valued from the price gap) - see
    # nse_common.resolve_ca_group.
    groups = {}
    for sym, isin, exd, subj in ca_ann.sort('ex_date').iter_rows():
        if not (start < exd <= end):
            continue
        cid = (isin2final.get(isin) if isin else None) or ren.get(sym, sym)
        groups.setdefault((cid, exd), [sym, isin, []])[2].append(subj)
    for (cid, exd), (sym, isin, subjs) in groups.items():
        allsub = " | ".join(subjs)
        obs = observed(cid, exd)
        r = resolve_ca_group(subjs, obs)
        if r is None:
            if cid in companies and any("rights" in x.lower() for x in subjs):
                ev("CORPORATE_ACTION_NOT_ADJUSTED", exd, cid, sym, isin,
                   details=f"rights issue - prices not adjusted: {allsub}")
            continue
        status, f, desc, method = r
        if status == "unparsed_subject":
            ev("CORPORATE_ACTION_UNPARSED", exd, cid, sym, isin, details=f"could not read a ratio from: {allsub}")
            continue
        if cid not in companies:
            continue
        if status == "applied":
            events.append((cid, exd, f, sym, isin, method, f"{desc} | NSE: {allsub}", obs))
            continue
        gap = "n/a" if obs is None else f"{obs:.4f}"
        ev("CORPORATE_ACTION_NOT_ADJUSTED", exd, cid, sym, isin, factor=f,
           new=None if obs is None else round(obs, 6),
           details=f"{status}: {desc or ''}; open / previous close {gap} - review: {allsub}")
    for qa, qb in ca_failed:                      # fallback detector for quarters without NSE's list
        lo, hi = max(qa, start + dt.timedelta(days=1)), min(qb, end)
        cand = (raw.filter(pl.col('datetime') >= pl.lit(dt.datetime(lo.year, lo.month, lo.day)),
                           pl.col('datetime') <= pl.lit(dt.datetime(hi.year, hi.month, hi.day)), pl.col('prev_close') > 0)
                   .with_columns(r=pl.col('open') / pl.col('prev_close')).filter(pl.col('r') < 0.7)
                   .select('company_id', 'datetime', 'isin', 'r'))
        for cid, ts, isin, r in cand.iter_rows():
            best = min(HEURISTIC_RATIOS, key=lambda q: abs(r / q - 1))
            if abs(r / best - 1) <= HEURISTIC_TOL:
                events.append((cid, ts.date(), best, cid, isin, "price_gap_heuristic",
                               f"open/prev close {r:.4f} ~ standard ratio {best:.4f} (NSE list unavailable)", r))
    raw = raw.drop('prev_close').with_columns(pl.lit(1.0).alias('adj_factor'))
    if events:
        # cumulative factor of all later events, attached to each row with an as-of join (low memory)
        evd = (pl.DataFrame([e[:3] for e in events], schema={'company_id': pl.String, 'ex_date': pl.Date, 'factor': F},
                            orient='row').unique(['company_id', 'ex_date'], keep='first')
                 .with_columns(pl.col('ex_date').cast(pl.Datetime('ns')).alias('ex_ts'))
                 .sort('company_id', 'ex_ts', descending=[False, True])
                 .with_columns(cumf=pl.col('factor').cum_prod().over('company_id')))
        raw = (raw.sort('datetime')
                  .join_asof(evd.select('company_id', 'ex_ts', 'cumf').sort('ex_ts'), left_on='datetime',
                             right_on='ex_ts', by='company_id', strategy='forward', allow_exact_matches=False)
                  .with_columns(pl.col('cumf').fill_null(1.0).alias('adj_factor')).drop('ex_ts', 'cumf')
                  # price / volume columns to adjust come from the contract (`adjust:` in contract\\schema.yaml)
                  .with_columns([(pl.col(x) * pl.col('adj_factor')).alias(x)
                                 for x in C.columns('raw_ohlcv', 'fresh', adjust='price') if x in raw.columns]
                                + [(pl.col(x) / pl.col('adj_factor')).round(0).cast(I).alias(x)
                                   for x in C.columns('raw_ohlcv', 'fresh', adjust='volume') if x in raw.columns])
                  .sort('company_id', 'datetime'))
        seen_ev = set()
        for cid, exd, f, sym, isin, method, desc, obs in events:
            if (cid, exd) in seen_ev:
                continue
            seen_ev.add((cid, exd))
            d = px_by[cid][0]
            n = int(np.searchsorted(d, np.datetime64(dt.datetime(exd.year, exd.month, exd.day), 'ns')))
            ev("CORPORATE_ACTION_ADJUSTED", exd, cid, sym, isin, None if obs is None else round(obs, 6), round(f, 8),
               factor=round(f, 8), rows=n, source=method,
               details=f"prices before {exd} x{f:.6f}, volumes /{f:.6f}; {desc}")
        log(paths, f"corporate actions back-adjusted: {len(seen_ev)} "
                   f"({sum(1 for e in events if e[5] == 'price_gap_heuristic')} by the price-gap fallback)")
    del px_by

    raw = raw.sort('company_id', 'datetime')

    # ---- 4. post-adjustment anomaly scan ------------------------------------------------------------------
    # A >40% move that reverses the next day is almost always a bad print in the source; a persistent one
    # is a real event or a corporate action NSE's list did not contain.
    chk = raw.select('company_id', 'isin', 'datetime', 'close').with_columns(
        r=pl.col('close').pct_change().over('company_id'),
        nxt=pl.col('close').pct_change().shift(-1).over('company_id'),
        prv=pl.col('close').pct_change().shift(1).over('company_id'))
    big = chk.filter(pl.col('r').abs() > SUSPECT_MOVE)
    revert = lambda a, b: a is not None and b is not None and a * b < 0 and abs((1 + a) * (1 + b) - 1) < 0.15
    for cid, isin, d0, c, r, nx, pv in big.iter_rows():
        if revert(r, nx):
            kind = "reverted next day (likely bad print)"
        elif revert(pv, r):
            kind = "reversal of the previous day's spike"
        else:
            kind = "persistent (corporate action not adjusted, or real move)"
        ev("SUSPECT_MOVE", d0.date(), cid, None, isin, None, round(c, 4), factor=round(1 + r, 6),
           details=f"one-day move {r * 100:+.1f}%, {kind}")
    final_day = raw['datetime'].max()
    last = raw.group_by('company_id').agg(pl.col('datetime').max().alias('d'), pl.col('isin').last(), pl.len())
    trading_days = raw.select(pl.col('datetime').unique()).sort('datetime')['datetime'].to_list()
    cutoff = trading_days[-10] if len(trading_days) >= 10 else trading_days[0]   # silent for 10+ sessions
    for cid, d0, isin, n in last.filter(pl.col('d') < cutoff).iter_rows():
        ev("LAST_TRADED", d0.date(), cid, None, isin, details=f"no trades after this date ({n} rows in window)")

    # ---- 5. market table ----------------------------------------------------------------------------------
    mrows = []
    for t in trading_days:
        data = index_raw.get(t.date())
        vals = parse_index_file(data) if data else {}
        mrows.append({'datetime': t, 'nifty_close': vals.get('nifty_close'), 'vix': vals.get('vix')})
    mk = pl.DataFrame(mrows, schema={'datetime': pl.Datetime('ns'), 'nifty_close': F, 'vix': F})
    g = None if args.offline else global_markets(start, end, lg)
    if g is not None:
        mk = mk.sort('datetime').join_asof(g, on='datetime', strategy='backward')
    mk = finish_market_columns(mk)

    # ---- 6. save raw + market + audit -----------------------------------------------------------------
    # columns and their order come from the contract (raw_ohlcv, variant fresh). This is a full rebuild from
    # NSE's files, so working columns the contract does not list are not written (keep_unknown=False).
    atomic_write(C.conform(raw, 'raw_ohlcv', 'fresh', lg, keep_unknown=False), paths.raw)
    atomic_write(mk, paths.market)
    au = (pl.DataFrame(audit, schema=AUDIT_SCHEMA) if audit else pl.DataFrame(schema=AUDIT_SCHEMA))
    if ren:   # show the final ticker everywhere; symbol_at_event keeps the ticker used at the time
        au = au.with_columns(pl.col('company_id').replace(ren))
    au = au.sort('date', 'event_type', 'company_id', nulls_last=True)
    au.write_csv(paths.audit / "audit_log.csv")
    atomic_write(au, paths.audit / "audit_log.parquet")

    counts = dict(au.group_by('event_type').len().sort('event_type').iter_rows())
    manifest = {
        "script": "nse_fresh_build.py", "script_version": SCRIPT_VERSION,
        "built_at": dt.datetime.now().isoformat(timespec="seconds"),
        "start": start.isoformat(), "end": end.isoformat(),
        "first_trading_day": trading_days[0].date().isoformat(), "last_trading_day": final_day.date().isoformat(),
        "trading_days": len(trading_days), "rows": raw.height, "companies": raw['company_id'].n_unique(),
        "series_kept": list(SERIES_PRIORITY), "prev_close_gap_log_threshold": GAP_LOG_THRESHOLD,
        "heuristic_tolerance": HEURISTIC_TOL,
        "corporate_action_list_unavailable_quarters": [f"{a}..{b}" for a, b in ca_failed],
        "suspect_move_threshold": SUSPECT_MOVE,
        "source_files_used": sources_used, "audit_event_counts": counts,
        "nse_symbol_changes_loaded": len(symchg), "nse_ca_announcements_loaded": ca_ann.height,
        "global_markets": "yfinance" if g is not None else "unavailable",
        "contract_version": C.version(),
        "notes": ["prices are back-adjusted for splits/bonus/consolidation only (not dividends)",
                  "nse_* columns in raw_ohlcv.parquet hold the original unadjusted NSE values",
                  "adjusted price = nse price x adj_factor; adjusted volume = nse volume / adj_factor",
                  "columns are defined by contract\\schema.yaml"],
        "versions": {"python": platform.python_version(), "polars": pl.__version__, "numpy": np.__version__},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    write_summary(paths, manifest, au)
    log(paths, f"raw store: {raw.height:,} rows, {manifest['companies']} companies, "
               f"{manifest['first_trading_day']} .. {manifest['last_trading_day']}; audit events: {au.height:,}")

    # ---- 7. features (nse_common.write_features) -------------------------------------------------------
    write_features(raw.select('company_id', 'datetime', 'open', 'high', 'low', 'close', 'volume'), mk, paths.out, lg)
    log(paths, f"done in {time.time() - t0:.0f}s")


def write_summary(paths, m, au):
    """audit_summary.txt: counts per event type plus the lists most worth reading."""
    L = [f"NSE fresh build - audit summary   (built {m['built_at']}, script v{m['script_version']})", "=" * 90,
         f"Window      : {m['start']} .. {m['end']}  (trading days {m['first_trading_day']} .. {m['last_trading_day']}, "
         f"{m['trading_days']} days)",
         f"Data        : {m['rows']:,} rows, {m['companies']:,} companies, series {', '.join(m['series_kept'])}",
         "Sources     : " + ", ".join(f"{k}={v}" for k, v in m['source_files_used'].items()),
         f"Global mkts : {m['global_markets']}", "", "Event counts"]
    for k, v in m['audit_event_counts'].items():
        L.append(f"  {k:32s}{v:>8,}")
    for et, title, n in [("CORPORATE_ACTION_ADJUSTED", "Corporate actions applied (old = observed gap, new = factor)", 400),
                         ("TICKER_RENAME", "Ticker renames", 400),
                         ("CORPORATE_ACTION_NOT_ADJUSTED", "Announced but not adjusted (review)", 200),
                         ("CORPORATE_ACTION_UNPARSED", "Announcements whose ratio could not be read (review)", 200),
                         ("SUSPECT_MOVE", "Suspect one-day moves (review)", 200),
                         ("LAST_TRADED", "Stopped trading inside the window", 200)]:
        sub = au.filter(pl.col('event_type') == et)
        if sub.height == 0:
            continue
        L += ["", f"{title} ({sub.height})"]
        for r in sub.head(n).iter_rows(named=True):
            parts = [str(r['date']), f"{r['company_id'] or '':12s}"]
            if r['old_value'] or r['new_value']:
                parts.append(f"{r['old_value'] or ''} -> {r['new_value'] or ''}")
            if r['factor'] is not None and et == "CORPORATE_ACTION_ADJUSTED":
                parts.append(f"factor {r['factor']:.6f}, {r['rows_affected']} rows")
            if r['details']:
                parts.append(r['details'])
            L.append("  " + "  ".join(parts))
        if sub.height > n:
            L.append(f"  ... {sub.height - n} more in audit_log.csv")
    L += ["", "Notes"] + [f"  - {n_}" for n_ in m['notes']]
    (paths.audit / "audit_summary.txt").write_text("\n".join(L), encoding="utf-8")


# =====================================================================================================
# COMMAND LINE
# =====================================================================================================
def main():
    ap = HelpfulParser(description=__doc__, epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=None, help="data folder (default: %%NSEDATAENGINE_FRESH_DIR%%, else %%NSEDATAENGINE_HOME%%\\data_fresh)")
    ap.add_argument("--out", default=None, help="feature file (default: <data-dir>\\features_5y.parquet)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build", help="download (if needed) and build the dataset + audit trail",
                       epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--years", type=int, default=5)
    p.add_argument("--start", help="YYYY-MM-DD (overrides --years and the remembered start)")
    p.add_argument("--end", help="YYYY-MM-DD (default: today after 7pm IST, else yesterday)")
    p.add_argument("--workers", type=int, default=4, help="parallel download threads")
    p.add_argument("--offline", action="store_true", help="use cached files only (testing)")
    args = parse_args_or_help(ap)        # no arguments -> full help with examples
    paths = Paths(args.data_dir or C.location('fresh'), args.out)
    try:
        cmd_build(args, paths)
    except SystemExit:
        raise
    except Exception as e:
        log(paths, f"ERROR: {e!r}")
        raise


if __name__ == "__main__":
    main()
