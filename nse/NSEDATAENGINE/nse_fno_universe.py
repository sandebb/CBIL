"""
nse_fno_universe.py - the exact list of NSE F&O stocks for every trading day, from NSE's own F&O files.

Why
  v3.11 trades several strategies (turtle, resid_reversion, orb, forced_flow's auction fade ...) only in
  stocks that have futures & options. Which stocks those are changes several times a year (SEBI's
  eligibility rules, NSE circulars). The backtest used to approximate it with "top 200 by turnover",
  which is wrong for many names. NSE's daily F&O bhavcopy lists every contract traded that day, so the
  stocks with a stock-futures contract in it ARE the F&O universe of that day - no guessing.

Sources (downloaded once, cached under <data-dir>\\cache)
  * from 8 Jul 2024: UDiFF F&O bhavcopy  BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip
        instrument type column FinInstrmTp: STF = stock futures, STO = stock options (IDF/IDO = index)
  * before:          legacy F&O bhavcopy  foDDMONYYYYbhav.csv.zip
        instrument column INSTRUMENT: FUTSTK / OPTSTK (FUTIDX / OPTIDX = index)
  A day whose file does not exist (holiday, weekend) is remembered in cache\\no_data.txt and not asked
  for again (only for days at least 3 days old - NSE sometimes publishes late).

Output: <data-dir>\\fno_universe.parquet  (kept SEPARATE from the price files - nothing else is changed)
  date           trading day
  symbol         the F&O symbol on that day (as NSE printed it)
  company_id     the same stock under its LATEST symbol (NSE's symbol-change list applied), which is the
                 key used in raw_ohlcv.parquet - so the backtest can match the two
  fut_oi         open interest summed over all futures expiries (from the file, units as NSE gives them)
  fut_volume     traded volume summed over all futures expiries (legacy: contracts; UDiFF: as NSE gives)
  lot_size       market lot (UDiFF files only; null before Jul 2024)
  n_expiries     futures expiries traded / open that day
  source         udiff_fo | legacy_fo

Re-running is cheap: files already in the cache are not downloaded again, new days are added.
"""
import argparse
import datetime as dt
import io
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import nse_common as NC  # noqa: E402  (installs missing packages on import)
import nse_contract as C  # noqa: E402  column contract (contract\\schema.yaml, dataset fno_universe)
from nse_common import (HelpfulParser, parse_args_or_help, project_home, atomic_write, all_days, default_end,  # noqa: E402
                        resolve_chain, parse_date, HOME_ENV_VAR, pl)
import nse_fresh_build as FB  # noqa: E402  (re-uses its rate-limited, cached, thread-safe NSE downloader)

URL_FO_UDIFF = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
URL_FO_LEGACY = "https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{y}/{m}/fo{dd}{m}{y}bhav.csv.zip"
UDIFF_START = dt.date(2024, 7, 8)
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
STOCK_FUT, STOCK_OPT = {"STF", "FUTSTK"}, {"STO", "OPTSTK"}

EXAMPLES = r"""
examples:
  build / extend the F&O list from 2019 to today (first run downloads ~1,900 files, later runs are quick):
    python nse_fno_universe.py build

  another period / more download threads:
    python nse_fno_universe.py build --start 2016-01-01 --workers 6

  what is in the file (stocks per year, first / last day):
    python nse_fno_universe.py check

  use it in the backtest instead of the "top 200 by turnover" approximation:
    python nse_backtest.py run --fno-universe exact
"""


class Paths:
    def __init__(self, data_dir):
        self.data = Path(data_dir)
        self.out = C.file_path("fno_universe", "ref", folder=self.data)   # name from contract\\schema.yaml
        self.cache = self.data / "cache"
        self.logs = self.data / "logs"
        self.no_data = self.cache / "no_data.txt"       # used by FB.NSE for holiday memory
        for p in (self.data, self.logs, self.cache / "fo_udiff", self.cache / "fo_legacy", self.cache / "ref"):
            p.mkdir(parents=True, exist_ok=True)


def log(paths, msg):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    with open(paths.logs / "fno_log.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def sources(paths, day):
    """(name, url, cache file) of the F&O file for this day."""
    u = ("udiff_fo", URL_FO_UDIFF.format(d=day), paths.cache / "fo_udiff" / f"{day:%Y%m%d}.csv.zip")
    lg = ("legacy_fo", URL_FO_LEGACY.format(y=day.year, m=MON[day.month - 1], dd=f"{day.day:02d}"),
          paths.cache / "fo_legacy" / f"{day:%Y%m%d}.csv.zip")
    # only the format NSE published that day: trying both would double the requests for every holiday
    return [u] if day >= UDIFF_START else [lg]


def fetch_day(nse, paths, day):
    for name, url, cache in sources(paths, day):
        data = nse.fetch(url, cache, f"{name}:{day.isoformat()}", day)
        if data is not None:
            return name, data
    return None, None


def _col(cols, *names):
    """First column whose upper-cased, stripped name is one of `names` (NSE's headers vary a little)."""
    up = {c.strip().upper(): c for c in cols}
    for n in names:
        if n in up:
            return up[n]
    return None


def parse_fo(data, day, source):
    """One F&O bhavcopy (zip or plain csv bytes) -> one row per stock with futures (or options only, if a
    file has no futures rows at all). Returns a polars DataFrame or None if the file is unreadable."""
    try:
        if data[:2] == b"PK":
            z = zipfile.ZipFile(io.BytesIO(data))
            data = z.read(z.namelist()[0])
        df = pl.read_csv(io.BytesIO(data), infer_schema_length=0, truncate_ragged_lines=True)
    except Exception:
        return None
    c_typ = _col(df.columns, "FININSTRMTP", "INSTRUMENT")
    c_sym = _col(df.columns, "TCKRSYMB", "SYMBOL")
    if c_typ is None or c_sym is None:
        return None
    c_oi = _col(df.columns, "OPNINTRST", "OPEN_INT")
    c_vol = _col(df.columns, "TTLTRADGVOL", "CONTRACTS")
    c_lot = _col(df.columns, "NEWBRDLOTQTY")
    c_exp = _col(df.columns, "XPRYDT", "EXPIRY_DT")
    num = lambda c: (pl.col(c).str.strip_chars().str.replace_all(",", "").cast(pl.Float64, strict=False)
                     if c else pl.lit(None, pl.Float64))
    d = df.select(typ=pl.col(c_typ).str.strip_chars().str.to_uppercase(),
                  symbol=pl.col(c_sym).str.strip_chars().str.to_uppercase(),
                  oi=num(c_oi), vol=num(c_vol), lot=num(c_lot),
                  exp=pl.col(c_exp).str.strip_chars() if c_exp else pl.lit(None, pl.String))
    fut = d.filter(pl.col("typ").is_in(list(STOCK_FUT)))
    if fut.height == 0:                              # should not happen, but keep the day via options
        fut = d.filter(pl.col("typ").is_in(list(STOCK_OPT)))
        if fut.height == 0:
            return None
    return (fut.group_by("symbol")
               .agg(fut_oi=pl.col("oi").sum(), fut_volume=pl.col("vol").sum(),
                    lot_size=pl.col("lot").max(), n_expiries=pl.col("exp").n_unique().cast(pl.Int32))
               .with_columns(date=pl.lit(day), source=pl.lit(source)))


def rename_map(nse, paths):
    """NSE symbol-change list -> {old: latest} (chains collapsed), same list the price builds use."""
    cache = paths.cache / "ref" / "symbolchange.csv"
    if not cache.exists() and not nse.offline:
        try:
            data = nse.get(FB.URL_SYMCHG)
            if data:
                cache.write_bytes(data)
        except Exception as e:
            log(paths, f"symbol-change list unavailable ({e!r}) - symbols kept as printed")
    m = {}
    if cache.exists():
        rows = []
        for line in cache.read_bytes().decode("utf-8", "replace").splitlines():
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) >= 4:
                d = parse_date(parts[-1])
                old, new = parts[-3].upper(), parts[-2].upper()
                if d and old and new and old != new:
                    rows.append((d, old, new))
        for d, old, new in sorted(rows):              # later changes win if a symbol was reused
            m[old] = new
    return resolve_chain(m)


def cmd_build(args, paths):
    t0 = time.time()
    end = dt.date.fromisoformat(args.end) if args.end else default_end()
    start = dt.date.fromisoformat(args.start)
    have = set()
    old = None
    if paths.out.exists() and not args.force:
        old = pl.read_parquet(paths.out)
        have = set(old["date"].unique().to_list())
    days = [d for d in all_days(start, end) if d not in have]
    nse = FB.NSE(paths, offline=args.offline)
    log(paths, f"F&O universe {start} .. {end}: {len(days)} calendar days to check "
               f"({len(have)} trading days already in {paths.out.name}), {args.workers} threads")
    parts, bad, n = [], [], 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_day, nse, paths, d): d for d in days}
        for fut in as_completed(futs):
            day = futs[fut]
            try:
                src, data = fut.result()
            except Exception as e:                    # NSE refused 4 times: report, keep going
                bad.append(day)
                log(paths, f"  {day}: download failed ({e})")
                src, data = None, None
            if data is not None:
                p = parse_fo(data, day, src)
                if p is None:
                    bad.append(day)
                    log(paths, f"  {day}: {src} file could not be read")
                else:
                    parts.append(p)
            n += 1
            if n % 200 == 0 or n == len(days):
                log(paths, f"  checked {n}/{len(days)} days, {len(parts)} trading days found")
    ren = rename_map(nse, paths)
    if parts:
        new = pl.concat(parts, how="vertical_relaxed")
        new = new.with_columns(company_id=pl.col("symbol").replace(ren))
        new = C.conform(new, "fno_universe", keep_unknown=False)
        if old is not None:
            # re-apply renames to the old rows too (the symbol-change list may have grown). Diagonal concat
            # keeps any column the stored file has that this script does not produce (null for new days).
            old = old.with_columns(company_id=pl.col("symbol").replace(ren))
            new = pl.concat([old, new], how="diagonal_relaxed")
        new = new.unique(["date", "symbol"], keep="last").sort("date", "symbol")
        atomic_write(C.conform(new, "fno_universe", log=lambda m: log(paths, m)), paths.out)
        log(paths, f"wrote {paths.out}: {new.height:,} rows, {new['date'].n_unique()} trading days, "
                   f"{new['company_id'].n_unique()} different stocks ({time.time() - t0:.0f}s)")
    else:
        log(paths, "no new trading days")
    if bad:
        log(paths, f"WARNING: {len(bad)} day(s) failed - run again later to retry: "
                   + ", ".join(str(d) for d in sorted(bad)[:20]) + (" ..." if len(bad) > 20 else ""))
    cmd_check(args, paths)


def cmd_check(args, paths):
    if not paths.out.exists():
        print(f"{paths.out} does not exist yet - run: python nse_fno_universe.py build")
        return
    f = pl.read_parquet(paths.out)
    per_day = f.group_by("date").agg(pl.len().alias("n")).sort("date")
    print(f"{paths.out}\n  {f.height:,} rows; {per_day.height} trading days; {per_day['date'].min()} .. {per_day['date'].max()}")
    y = (per_day.with_columns(year=pl.col("date").dt.year()).group_by("year")
                .agg(days=pl.len(), min_stocks=pl.col("n").min(), max_stocks=pl.col("n").max()).sort("year"))
    s = (f.with_columns(year=pl.col("date").dt.year()).group_by("year")
          .agg(different_stocks=pl.col("company_id").n_unique()))
    print(y.join(s, on="year"))
    # sanity: trading days with an unusually short list usually mean a damaged / partial file
    med = per_day["n"].median()
    odd = per_day.filter(pl.col("n") < 0.8 * med)
    if odd.height:
        print(f"  {odd.height} day(s) with fewer than 80% of the usual {med:.0f} stocks - check: "
              + ", ".join(str(d) for d in odd["date"].to_list()[:10]))


def main():
    ap = HelpfulParser(description=__doc__, epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=None, help="folder for the file and its cache (default: %%NSEDATAENGINE_REF_DIR%%, else %%NSEDATAENGINE_HOME%%\\data_ref)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    fmt = dict(epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    p = sub.add_parser("build", help="download (if needed) and build / extend fno_universe.parquet", **fmt)
    p.add_argument("--start", default="2019-01-01", help="YYYY-MM-DD (default 2019-01-01)")
    p.add_argument("--end", help="YYYY-MM-DD (default: today after 7pm IST, else yesterday)")
    p.add_argument("--workers", type=int, default=4, help="parallel download threads (default 4)")
    p.add_argument("--force", action="store_true", help="re-read every day from the cache instead of only new ones")
    p.add_argument("--offline", action="store_true", help="use cached files only")
    sub.add_parser("check", help="summary of the file", **fmt)
    args = parse_args_or_help(ap)
    paths = Paths(args.data_dir or C.location("ref"))
    {"build": cmd_build, "check": cmd_check}[args.cmd](args, paths)


if __name__ == "__main__":
    main()
