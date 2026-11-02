"""
nse_updater.py - keep the NSE equity daily dataset up to date.

What it does
  Your historical dataset (1991 -> Sep 2024) is extended day by day with NSE's official end-of-day files.
  Each run:
    1. re-downloads the last stored day (replaces it with NSE's official numbers - protects against a
       partial intraday row) and every calendar day after it, up to today
    2. keeps only equity shares (EQ / BE / BZ series, ISIN INE....01....) - no ETFs, SME, rights entitlements
    3. follows ticker renames through the ISIN (e.g. ZOMATO -> ETERNAL keeps its full history)
    4. back-adjusts history for splits / bonuses / consolidations using NSE's announcements
    5. refreshes the market table (Nifty 50, India VIX from NSE; global markets from Yahoo Finance)
    6. rebuilds every indicator column (formulas explained in nse_common.py)
  Running it daily, weekly or monthly gives the same result - it fills whatever is missing.

Data sources (all free)
  Stocks : NSE CM bhavcopy, UDiFF format (nsearchives.nseindia.com)
  Indices: NSE daily index close file
  Global : Yahoo Finance via yfinance (installed automatically if missing)

Files - all inside %NSEDATAENGINE_DATA_DIR% (default <project>\\data; project = %NSEDATAENGINE_HOME%, or the
folder of this script). File names: contract\\schema.yaml.
  raw_ohlcv.parquet               one row per stock per day (adjusted OHLCV + ISIN) - the master store
  market.parquet                  one row per trading day (index / global columns)
  features_output_clean.parquet   the full feature file, rebuilt from the two above on every update
  cache\\                          downloaded NSE files (re-runs never download the same file twice)
  logs\\update_log.txt             what each run did
  logs\\corporate_actions.csv      every split/bonus/consolidation seen: applied or not, and why
  logs\\symbol_changes.csv         ticker renames (old -> new, ISIN)
  logs\\prev_close_gaps.csv        days where NSE's previous close differs >2% from ours (for review)
  logs\\suspect_moves.csv          one-day moves >40% left in the new data (for review)

Usage
  python nse_updater.py init --source ${NSE_ROOT}/data/features_output_clean.parquet   (one time; --force to start over)
  python nse_updater.py update                      download missing days + rebuild features
  python nse_updater.py update --until 2025-03-31   stop at a date
  python nse_updater.py update --no-features        only refresh the raw store and market table
  python nse_updater.py features                    rebuild the feature file only
  python nse_updater.py check                       show where the project lives and what data it has
  Run it without arguments to see every option and more examples.
"""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import argparse
import datetime as dt
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

# The shared module lives next to this script. Adding this script's folder to the import path makes the
# import work no matter which folder the script is started from (Task Scheduler starts in System32).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from nse_common import (  # noqa: E402  (nse_common installs missing packages on import)
    F, I, MKT, HEADERS, URL_UDIFF, URL_INDEX, URL_CA, SERIES, GAP_LOG_THRESHOLD, HEURISTIC_RATIOS, HEURISTIC_TOL,
    HOME_ENV_VAR, HelpfulParser, parse_args_or_help, project_home, atomic_write, append_csv, parse_date, all_days, default_end, resolve_chain,
    is_equity_share, parse_index_file, parse_ca_factor, is_price_action_subject, resolve_ca_group, global_markets,
    finish_market_columns, write_features, np, pl, C,
)

EXAMPLES = r"""
examples:
  first time (imports the historical file, then downloads everything missing):
    python nse_updater.py init --source ${NSE_ROOT}/data\features_output_clean.parquet
    python nse_updater.py update

  start again from the historical file (old logs are archived):
    python nse_updater.py init --source ${NSE_ROOT}/data\features_output_clean.parquet --force

  normal run - daily, weekly or monthly (this is what run_update.bat does):
    python nse_updater.py update

  update only up to a date / refresh the stored prices without rebuilding the feature file:
    python nse_updater.py update --until 2025-03-31
    python nse_updater.py update --no-features

  rebuild the feature file only (e.g. on a new PC):
    python nse_updater.py features

  where is the project and what data does it hold:
    python nse_updater.py check

  use another data folder / feature file:
    python nse_updater.py --data-dir D:\nse\data update
    python nse_updater.py --out D:\exports\features.parquet features

  help for one command:
    python nse_updater.py update -h
"""

REQUEST_PAUSE = 0.4     # seconds between NSE requests - stay well below anything that looks like abuse

# Master store columns come from the column contract (contract\\schema.yaml, dataset raw_ohlcv, variant
# master). `isin` is null for history that came from the original file (it had no ISINs); it is filled in
# for every company the first time the company appears in an NSE file.
VARIANT = 'master'
RAW_COLS = C.columns('raw_ohlcv', VARIANT)
# The columns this script itself fills from an NSE bhavcopy. Any other contract column is written as null
# for new days until code that produces it is added (nse_contract.conform warns about it in the log).
NEW_ROW_SCHEMA = {'company_id': pl.String, 'isin': pl.String, 'datetime': pl.Datetime('ns'),
                  'open': F, 'high': F, 'low': F, 'close': F, 'volume': I}


class Paths:
    """Every file location, derived from one data folder so the project can be moved as a unit."""

    def __init__(self, data_dir, out=None):
        self.data = Path(data_dir)
        # file names come from the contract (contract\\schema.yaml), never from this script
        self.raw = C.file_path('raw_ohlcv', VARIANT, folder=self.data)
        self.market = C.file_path('market', VARIANT, folder=self.data)
        self.out = Path(out) if out else C.file_path('features', VARIANT, folder=self.data)
        self.cache = self.data / "cache"
        self.logs = self.data / "logs"
        self.no_data = self.cache / "no_data_dates.txt"   # days NSE has no file for (holidays, weekends)
        for p in (self.data, self.cache / "bhav", self.cache / "index", self.cache / "ref", self.logs):
            p.mkdir(parents=True, exist_ok=True)


def log(paths, msg):
    """Print a timestamped line and append it to logs\\update_log.txt (the scheduled task has no console)."""
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    with open(paths.logs / "update_log.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")


# =====================================================================================================
# DOWNLOADING FROM NSE
# =====================================================================================================
class NSE:
    """Downloads NSE files with caching, retries and holiday memory.

    * Cache: every file is saved under data\\cache the first time; later runs read it from disk.
    * Holiday memory: when NSE says a file does not exist (HTTP 404) for a day at least 3 days in the
      past, the day is written to no_data_dates.txt and never requested again. Recent days are not
      remembered, because NSE sometimes publishes late and we must ask again next run.
    * Retries: other errors (timeouts, 403 "blocked") are retried 4 times with growing pauses and a fresh
      session, because NSE's servers are known to reject requests intermittently."""

    def __init__(self, paths, offline=False):
        self.paths, self.offline, self.s = paths, offline, None
        self.no_data = set()
        if paths.no_data.exists():
            self.no_data = {x.strip() for x in paths.no_data.read_text().splitlines() if x.strip()}

    def _session(self):
        """One HTTP session (keeps the connection open). Visiting the home page first collects the
        cookies NSE's API checks for; without them the corporate-action API answers 401/403."""
        if self.s is None:
            import requests
            self.s = requests.Session()
            self.s.headers.update(HEADERS)
            try:
                self.s.get("https://www.nseindia.com/", timeout=20)
            except Exception:
                pass
        return self.s

    def _get(self, url, accept_json=False):
        """File bytes, or None if NSE says it does not exist (holiday / not published yet)."""
        last = None
        for attempt in range(4):
            try:
                hdr = {"Accept": "application/json, text/plain, */*"} if accept_json else None
                r = self._session().get(url, timeout=60, headers=hdr)
                time.sleep(REQUEST_PAUSE)
                if r.status_code == 200 and r.content:
                    return r.content
                if r.status_code == 404:
                    return None
                last = f"HTTP {r.status_code}"
            except Exception as e:
                last = repr(e)
            time.sleep(3 * (attempt + 1))      # 3, 6, 9, 12 s back-off
            self.s = None                      # new session + new cookies on the next attempt
        raise RuntimeError(f"download failed for {url}: {last}")

    def _fetch(self, url, cache, day, kind):
        """Cached download. `kind` + date form the key used in the holiday memory."""
        if cache.exists():
            return cache.read_bytes()
        key = f"{kind}:{day.isoformat()}"
        if self.offline or key in self.no_data:
            return None
        data = self._get(url)
        if data is None:
            if (dt.date.today() - day).days >= 3:
                self.no_data.add(key)
                with open(self.paths.no_data, "a") as f:
                    f.write(key + "\n")
            return None
        cache.write_bytes(data)
        return data

    def bhavcopy(self, day):
        """NSE's end-of-day file for `day`, reduced to equity shares: symbol, isin, OHLC, prev_close, volume.
        Returns None when there was no trading session that day."""
        data = self._fetch(URL_UDIFF.format(d=day), self.paths.cache / "bhav" / f"{day:%Y%m%d}.csv.zip", day, "bhav")
        if data is None:
            return None
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            raw = z.read(z.namelist()[0])
        df = pl.read_csv(raw, infer_schema_length=0)       # read everything as text, convert explicitly below
        df = df.rename({c: c.strip() for c in df.columns})
        df = df.with_columns(pl.col(pl.String).str.strip_chars())
        need = {"TckrSymb", "SctySrs", "ISIN", "OpnPric", "HghPric", "LwPric", "ClsPric", "PrvsClsgPric", "TtlTradgVol"}
        missing = need - set(df.columns)
        if missing:   # NSE changed the format - stop loudly rather than store wrong numbers
            raise RuntimeError(f"bhavcopy {day}: unexpected format, missing columns {sorted(missing)}")
        if "FinInstrmTp" in df.columns:
            df = df.filter(pl.col("FinInstrmTp") == "STK")  # cash-market securities (not derivatives)
        df = df.filter(pl.col("SctySrs").is_in(SERIES), is_equity_share(pl.col("ISIN")))
        num = lambda c: pl.col(c).cast(F, strict=False)    # strict=False: a stray "-" becomes null, not a crash
        return df.select(
            pl.col("TckrSymb").alias("symbol"), pl.col("ISIN").alias("isin"),
            num("OpnPric").alias("open"), num("HghPric").alias("high"), num("LwPric").alias("low"),
            num("ClsPric").alias("close"), num("PrvsClsgPric").alias("prev_close"),
            num("TtlTradgVol").cast(I).alias("volume"),
        ).filter(pl.col("close") > 0)

    def index_close(self, day):
        """{'nifty_close': ..., 'vix': ...} for `day`, or {} if NSE has no index file for it."""
        data = self._fetch(URL_INDEX.format(d=day), self.paths.cache / "index" / f"{day:%Y%m%d}.csv", day, "index")
        return parse_index_file(data) if data else {}


def fetch_corporate_actions(nse, paths, start, end):
    """NSE corporate-action announcements between start and end.

    Queried one calendar quarter at a time (NSE's API limits the date range). Completed quarters are
    cached in cache\\ref, so they are downloaded once; the current quarter is always re-queried because
    new announcements keep arriving. Returns (DataFrame[symbol, isin, ex_date, subject],
    list of (from, to) quarters that could not be fetched - those fall back to the price-gap detector)."""
    rows, failed = [], []
    a = dt.date(start.year, ((start.month - 1) // 3) * 3 + 1, 1)          # first day of start's quarter
    while a <= end:
        nxt = dt.date(a.year + (1 if a.month == 10 else 0), 1 if a.month == 10 else a.month + 3, 1)
        b = nxt - dt.timedelta(days=1)                                     # last day of the quarter
        cache = paths.cache / "ref" / f"corporate_actions_{a:%Y%m%d}.json"
        data = cache.read_bytes() if cache.exists() else None
        if data is None and not nse.offline:
            try:
                data = nse._get(URL_CA.format(a=a, b=b), accept_json=True)
                if data and data.lstrip().startswith(b"[") and b < dt.date.today() - dt.timedelta(days=7):
                    cache.write_bytes(data)
            except Exception as e:
                log(paths, f"corporate-action list {a} .. {b} unavailable: {e!r}")
                data = None
        try:
            items = json.loads(data) if data else None
        except Exception:
            items = None                       # e.g. an HTML error page instead of JSON
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
# INIT - one-time import of the historical dataset
# =====================================================================================================
def cmd_init(args, paths):
    """Create the master store and market table from the cleaned historical feature file.

    Only the contract's raw_ohlcv columns are kept (contract\\schema.yaml); every indicator is recomputed later, so
    nothing computed in the old file is trusted. The market table takes, for each date, the most common
    value of each market column (the original file sometimes had 2-3 different values on one date)."""
    src = Path(args.source)
    if paths.raw.exists() and not args.force:
        sys.exit(f"{paths.raw} already exists - add --force to rebuild it from {src}")
    # Move the previous build's logs into an archive folder, so the new logs describe only the new build
    # (and the corporate-action ledger does not think adjustments were already applied).
    old_logs = [p for p in paths.logs.glob("*") if p.is_file()]
    if old_logs:
        arch = paths.logs / f"archive_{dt.datetime.now():%Y%m%d_%H%M%S}"
        arch.mkdir()
        for p in old_logs:
            os.replace(p, arch / p.name)
    lf = pl.scan_parquet(src)
    have = list(C._schema(lf))
    take = [c for c in RAW_COLS if c in have and c != 'isin']     # only contract columns are imported
    raw = (lf.select(take)
             .with_columns(pl.lit(None, dtype=pl.String).alias('isin'))
             .collect()
             .unique(['company_id', 'datetime'], keep='first').sort('company_id', 'datetime'))
    mk = (lf.group_by('datetime').agg([pl.col(m).drop_nulls().mode().sort().first() for m in MKT if m in have])
            .collect().sort('datetime'))
    atomic_write(C.conform(raw, 'raw_ohlcv', VARIANT, lambda m: log(paths, m)), paths.raw)
    atomic_write(finish_market_columns(mk), paths.market)
    log(paths, f"init: {raw.height:,} stock rows ({raw['company_id'].n_unique()} tickers) up to "
               f"{raw['datetime'].max():%Y-%m-%d}; market table {mk.height:,} days")


# =====================================================================================================
# CORPORATE ACTIONS - back-adjust history for splits / bonuses / consolidations
# =====================================================================================================
def apply_corporate_actions(raw, nse, paths, first_new, last_new, ren, gaps):
    """Scale price history so splits and bonuses do not look like crashes.

    For every split/bonus/consolidation/demerger with an ex-date inside the newly added days:
      1. read the ratio from NSE's announcement (parse_ca_factor), e.g. split Rs 10 -> Rs 2 = factor 0.2.
         All notices of one company on one ex-date are combined first (bonus + split on the same day
         multiply); preference-share "bonuses" are ignored; demergers take the factor from the price gap
         (all in nse_common.resolve_ca_group)
      2. confirm it against the market: open on the ex-date / previous close should be close to the
         factor. If it is off by more than 25% something is wrong (misparsed notice, wrong company) and
         the event is logged as "not_applied_price_gap_differs" instead of being applied
      3. multiply all prices BEFORE the ex-date by the factor and divide volumes by it
    Quarters whose announcement list could not be downloaded use the fallback detector: an opening gap
    matching a standard ratio (1/2, 1/5, 2/3 ...) within 8%.

    A ledger (logs\\corporate_actions.csv) records every decision. Events already marked "applied" in the
    ledger are never applied twice, so re-running an update is safe."""
    ledger_path = paths.logs / "corporate_actions.csv"
    done = set()
    if ledger_path.exists():
        try:
            led = pl.read_csv(ledger_path, infer_schema_length=0)
            done = {(r[0], r[1]) for r in led.filter(pl.col('status') == 'applied')
                    .select('company_id', 'ex_date').iter_rows()}
        except Exception:
            pass
    d0, d1 = first_new.date(), last_new.date()
    # Only ex-dates strictly after the first new day: anything on/before it is already reflected in the
    # stored history (Yahoo-adjusted original data, or a previous run).
    ann, failed = fetch_corporate_actions(nse, paths, d0 + dt.timedelta(days=1), d1)
    if failed:
        log(paths, f"WARNING: NSE corporate-action list unavailable for {len(failed)} quarter(s) - "
                   f"using the price-gap detector there: " + ", ".join(f"{a}..{b}" for a, b in failed))
    latest = raw.sort('datetime').group_by('company_id').agg(pl.col('isin').drop_nulls().last())
    isin2cid = {i: c for c, i in latest.iter_rows() if i}
    companies = set(latest['company_id'].to_list())
    # Opening price vs previous close for each company in the new period, kept as numpy arrays per
    # company for fast look-ups (only the new period, to keep memory low).
    px = (raw.filter(pl.col('datetime') >= first_new - dt.timedelta(days=15))
             .select('company_id', 'datetime', 'open', 'close').sort('company_id', 'datetime')
             .with_columns(prev_close=pl.col('close').shift(1).over('company_id')))
    px_by = {c: (g['datetime'].to_numpy(), g['open'].to_numpy(), g['prev_close'].to_numpy())
             for (c,), g in px.partition_by('company_id', as_dict=True).items()}

    def observed(cid, exd):
        """open on the first trading day on/after the ex-date / the close before it (None if unknown)."""
        if cid not in px_by:
            return None
        d, o, pc = px_by[cid]
        i = np.searchsorted(d, np.datetime64(dt.datetime(exd.year, exd.month, exd.day), 'ns'))
        if i >= len(d) or not pc[i] or np.isnan(pc[i]):
            return None
        return float(o[i] / pc[i])

    events, ledger = [], []   # ledger row: company, symbol, isin, ex_date, factor, description, subject,
    #                                       method, status, observed gap, rows adjusted
    # Group the announcements by (company, ex-date): a bonus and a split on the same day are separate
    # notices whose factors must be multiplied (resolve_ca_group explains why and how).
    groups = {}
    for sym, isin, exd, subj in ann.sort('ex_date').iter_rows():
        if not (d0 < exd <= d1):
            continue
        # Match the announcement to our company: by ISIN if possible, else by symbol (through renames).
        cid = (isin2cid.get(isin) if isin else None) or ren.get(sym, sym)
        groups.setdefault((cid, exd), [sym, isin, []])[2].append(subj)
    for (cid, exd), (sym, isin, subjs) in groups.items():
        subj = " | ".join(subjs)
        if cid not in companies:
            r = resolve_ca_group(subjs, None)
            if r is not None:
                ledger.append((cid, sym, isin, exd, r[1], r[2], subj, r[3], "not_in_data", None, 0))
            continue
        if (cid, exd.isoformat()) in done:
            r = resolve_ca_group(subjs, None)
            if r is not None:
                ledger.append((cid, sym, isin, exd, r[1], r[2], subj, r[3], "already_applied", None, 0))
            continue
        obs = observed(cid, exd)
        r = resolve_ca_group(subjs, obs)
        if r is None:
            continue                            # dividends, AGMs etc. do not change the price history here
        status, f, desc, method = r
        # statuses: applied | not_applied_price_gap_differs | unparsed_subject | not_equity_shares |
        #           demerger_no_price_drop | demerger_no_price_data
        if status == "applied":
            events.append((cid, exd, f))
        ledger.append((cid, sym, isin, exd, f, desc, subj, method, status, obs, None if status == "applied" else 0))

    # Fallback for quarters without NSE's list.
    for a, b in failed:
        lo, hi = max(a, d0 + dt.timedelta(days=1)), min(b, d1)
        cand = px.filter(pl.col('datetime') >= pl.lit(dt.datetime(lo.year, lo.month, lo.day)),
                         pl.col('datetime') <= pl.lit(dt.datetime(hi.year, hi.month, hi.day)),
                         pl.col('prev_close') > 0).with_columns(r=pl.col('open') / pl.col('prev_close'))
        cand = cand.filter(pl.col('r') < 0.7)                 # only gaps of more than -30% are candidates
        for cid, ts, o, c, pc, r in cand.iter_rows():
            best = min(HEURISTIC_RATIOS, key=lambda q: abs(r / q - 1))   # nearest standard ratio
            if abs(r / best - 1) <= HEURISTIC_TOL:
                exd = ts.date()
                if (cid, exd.isoformat()) in done:
                    continue
                events.append((cid, exd, best))
                ledger.append((cid, cid, None, exd, best, f"price gap {r:.4f} ~ ratio {best:.4f}", None,
                               "price_gap_heuristic", "applied", r, None))

    if events:
        # Cumulative factor per row = product of the factors of all events AFTER that row's date.
        # Sort each company's events newest first and take a running product, then give every row the
        # value of the first event strictly after it with an as-of join. This uses little memory even
        # for millions of rows (no row x event cross join).
        ev = (pl.DataFrame(events, schema={'company_id': pl.String, 'ex_date': pl.Date, 'factor': F}, orient='row')
                .unique(['company_id', 'ex_date'], keep='first')
                .with_columns(pl.col('ex_date').cast(pl.Datetime('ns')).alias('ex_ts'))
                .sort('company_id', 'ex_ts', descending=[False, True])
                .with_columns(cumf=pl.col('factor').cum_prod().over('company_id')))
        raw = (raw.sort('datetime')
                  .join_asof(ev.select('company_id', 'ex_ts', 'cumf').sort('ex_ts'), left_on='datetime',
                             right_on='ex_ts', by='company_id', strategy='forward', allow_exact_matches=False)
                  .with_columns(pl.col('cumf').fill_null(1.0).alias('_k')).drop('ex_ts', 'cumf'))
        # Prices x factor, volume / factor: the traded value (price x volume) of each day is unchanged.
        # Which columns are prices / volumes comes from the contract (`adjust:`), so a price or volume
        # column added later is adjusted too without touching this code.
        px_cols = [x for x in C.columns('raw_ohlcv', VARIANT, adjust='price') if x in raw.columns]
        vol_cols = [x for x in C.columns('raw_ohlcv', VARIANT, adjust='volume') if x in raw.columns]
        raw = (raw.with_columns([(pl.col(x) * pl.col('_k')).alias(x) for x in px_cols]
                                + [(pl.col(x) / pl.col('_k')).round(0).cast(raw.schema[x]).alias(x) for x in vol_cols])
                  .drop('_k').sort('company_id', 'datetime'))
        # Number of rows adjusted per event (rows of that company dated before the ex-date), for the ledger.
        dates_by = {c: g['datetime'].to_numpy() for (c,), g in
                    raw.filter(pl.col('company_id').is_in(ev['company_id'].unique().to_list()))
                       .select('company_id', 'datetime').partition_by('company_id', as_dict=True).items()}

        def n_before(cid, exd):
            d = dates_by.get(cid)
            return int(np.searchsorted(d, np.datetime64(dt.datetime(exd.year, exd.month, exd.day), 'ns'))) if d is not None else 0
        ledger = [r[:10] + ((n_before(r[0], r[3]) if r[8] == "applied" else r[10]),) for r in ledger]
    if ledger:
        append_csv(pl.DataFrame(ledger, orient='row', schema={
            'company_id': pl.String, 'symbol_at_event': pl.String, 'isin': pl.String, 'ex_date': pl.Date,
            'factor': F, 'description': pl.String, 'nse_subject': pl.String, 'method': pl.String,
            'status': pl.String, 'observed_open_vs_prev_close': F, 'rows_adjusted': I}), ledger_path)
    n_app = sum(1 for r in ledger if r[8] == "applied")
    log(paths, f"corporate actions: {n_app} applied, {len(ledger) - n_app} logged but not applied "
               f"(logs/corporate_actions.csv)")

    # Review files. Neither changes the data; they point at days worth a look.
    explained = {(c, d) for c, d, _ in events}
    if gaps:
        # NSE's previous close vs ours: large differences usually mean a missed session, a corporate
        # action or a bad print in the source - explained_by_corporate_action marks the ones we adjusted.
        g = pl.DataFrame(gaps, orient='row', schema={'company_id': pl.String, 'symbol_at_event': pl.String,
                         'isin': pl.String, 'date': pl.Date, 'our_last_close': F, 'nse_prev_close': F,
                         'open': F, 'close': F}).with_columns(
            company_id=pl.col('company_id').replace(ren),
            gap_pct=((pl.col('nse_prev_close') / pl.col('our_last_close') - 1) * 100).round(2))
        g = g.with_columns(explained_by_corporate_action=pl.struct('company_id', 'date').map_elements(
            lambda x: (x['company_id'], x['date']) in explained, return_dtype=pl.Boolean))
        append_csv(g, paths.logs / "prev_close_gaps.csv")
    # One-day moves above 40% that are still there after adjusting: real crashes / rallies, bad prints, or
    # a corporate action NSE's list did not contain.
    mv = (raw.select('company_id', 'datetime', 'close').sort('company_id', 'datetime')
             .with_columns(ret=pl.col('close').pct_change().over('company_id'))
             .filter(pl.col('datetime') > first_new, pl.col('ret').abs() > 0.4))
    if mv.height:
        append_csv(mv.with_columns(pl.col('datetime').dt.date().alias('date'), (pl.col('ret') * 100).round(1).alias('move_pct'))
                     .select('company_id', 'date', 'close', 'move_pct'), paths.logs / "suspect_moves.csv")
        log(paths, f"{mv.height} one-day moves over 40% remain in the new data (logs/suspect_moves.csv) - review")
    return raw


# =====================================================================================================
# UPDATE
# =====================================================================================================
def cmd_update(args, paths):
    if not paths.raw.exists():
        sys.exit("raw store not found - run `init` first")
    raw = pl.read_parquet(paths.raw)
    mk = pl.read_parquet(paths.market)
    last = raw['datetime'].max().date()
    until = dt.date.fromisoformat(args.until) if args.until else default_end()
    nse = NSE(paths, offline=args.offline)
    log(paths, f"update: last stored day {last} (re-fetched), fetching up to {until}")

    # --- identity state, built from history BEFORE the re-fetched last day -------------------------
    # The ISIN is the stable identity of a security; the ticker (company_id) can change. These maps let
    # each row in a new file be attached to the right company history.
    prior = (raw.select('company_id', 'datetime', 'isin', 'close')
               .filter(pl.col('datetime') < pl.lit(dt.datetime(last.year, last.month, last.day))))
    st = (prior.sort('datetime').group_by('company_id', maintain_order=True)
               .agg(pl.col('isin').drop_nulls().last(), pl.col('close').last().alias('last_close')))
    cid2isin = dict(zip(st['company_id'], st['isin']))
    isin2cid = {i: c for c, i in cid2isin.items() if i}
    last_close = dict(zip(st['company_id'], st['last_close']))
    del prior, st

    # --- read every new day ------------------------------------------------------------------------
    new_rows, gaps, renames, sessions = [], [], {}, []
    for day in all_days(last, until):
        b = nse.bhavcopy(day)
        if b is None:
            continue                                      # holiday / weekend without a session
        sessions.append(day)
        if day.weekday() >= 5:
            log(paths, f"  special weekend session found: {day} ({day:%A})")
        ts = dt.datetime(day.year, day.month, day.day)
        seen = set()
        for sym, isin, o, h, l, c, pc, v in b.iter_rows():
            if isin and isin in isin2cid:
                # Known security. Same ISIN under a new ticker = rename (e.g. ZOMATO -> ETERNAL).
                cid = isin2cid[isin]
                if sym != cid and sym not in cid2isin:
                    renames[cid] = sym
                    append_csv(pl.DataFrame({'date': [day], 'old_ticker': [cid], 'new_ticker': [sym], 'isin': [isin]}),
                               paths.logs / 'symbol_changes.csv')
                    cid2isin[sym] = cid2isin.pop(cid, isin)
                    last_close[sym] = last_close.pop(cid, None)
                    isin2cid = {i: (sym if x == cid else x) for i, x in isin2cid.items()}
                    cid = sym
            elif sym in cid2isin:
                # Known ticker with a new or first-seen ISIN (history from the original file has no ISIN).
                cid = sym
                old = cid2isin.get(sym)
                if old and old != isin:
                    isin2cid.pop(old, None)
                cid2isin[sym] = isin
                isin2cid[isin] = sym
            else:
                # Neither ISIN nor ticker known: a new listing.
                cid = sym
                cid2isin[sym] = isin
                isin2cid[isin] = sym
            if cid in seen:
                continue                                  # same company twice in one file - keep the first
            seen.add(cid)
            # NSE's previous close vs ours - logged only (NSE does not adjust it for splits in this format).
            lc = last_close.get(cid)
            if lc and pc and lc > 0 and abs(pc / lc - 1) > GAP_LOG_THRESHOLD:
                gaps.append((cid, sym, isin, day, lc, pc, o, c))
            last_close[cid] = c
            # Keep OHLC consistent: high must be the day's maximum and low the minimum of open/high/low/close.
            hi, lo = max(o, h, l, c), min(o, h, l, c)
            new_rows.append((cid, isin, ts, o, hi, lo, c, v))
        if len(sessions) % 25 == 0:
            log(paths, f"  ... {day}: {len(sessions)} sessions, {len(new_rows):,} rows")

    # --- merge into the master store -------------------------------------------------------------
    if not new_rows:
        log(paths, "stocks: nothing new")
    else:
        add = pl.DataFrame(new_rows, schema=NEW_ROW_SCHEMA, orient='row')
        first_new = add['datetime'].min()
        raw = raw.filter(pl.col('datetime') < first_new)  # the re-fetched day is replaced by NSE's official file
        ren = resolve_chain(renames)
        if ren:                                           # move history to the new ticker names
            raw = raw.with_columns(pl.col('company_id').replace(ren))
            add = add.with_columns(pl.col('company_id').replace(ren))
        # back-fill the ISIN onto the company's older rows
        isin_map = pl.DataFrame({'company_id': list(cid2isin), 'isin_new': list(cid2isin.values())},
                                schema={'company_id': pl.String, 'isin_new': pl.String})
        raw = (raw.join(isin_map, on='company_id', how='left')
                  .with_columns(pl.coalesce('isin', 'isin_new').alias('isin')).drop('isin_new'))
        # diagonal concat: columns the stored file has but the new rows do not (added to the contract
        # later, or unknown) are kept - null for the new days - instead of being dropped
        raw = (pl.concat([raw, add], how='diagonal_relaxed')
                 .unique(['company_id', 'datetime'], keep='last').sort('company_id', 'datetime'))
        raw = apply_corporate_actions(raw, nse, paths, first_new, add['datetime'].max(), ren, gaps)
        atomic_write(C.conform(raw, 'raw_ohlcv', VARIANT, lambda m: log(paths, m)), paths.raw)
        log(paths, f"stocks: {len(new_rows):,} rows over {len(sessions)} sessions ({sessions[0]} .. {sessions[-1]}) "
                   f"-> {raw['company_id'].n_unique()} tickers, {len(ren)} ticker renames")

    # --- market table: refresh from the last stored market day (inclusive) to the last stock day ------
    stock_days = raw.select(pl.col('datetime').unique()).sort('datetime')
    todo = stock_days.filter(pl.col('datetime') >= pl.lit(mk['datetime'].max()))['datetime'].to_list()
    if todo:
        new = pl.DataFrame([{'datetime': t, 'nifty_close': None, 'vix': None, **nse.index_close(t.date())} for t in todo],
                           schema={'datetime': pl.Datetime('ns'), 'nifty_close': F, 'vix': F})
        g = None if args.offline else global_markets(todo[0].date(), todo[-1].date(), lambda m: log(paths, m))
        if g is not None:
            # as-of join: each Indian trading day gets the latest global close on or before that date
            new = new.sort('datetime').join_asof(g, on='datetime', strategy='backward')
        new = finish_market_columns(new, quiet=True)   # merged with the stored values just below
        # keep the old value wherever the new download has nothing (never replace data with blanks)
        merged = new.join(mk, on='datetime', how='left', suffix='_old')
        merged = (merged.with_columns([pl.coalesce(c, c + '_old').alias(c) for c in MKT if c + '_old' in merged.columns])
                        .drop([c for c in merged.columns if c.endswith('_old')]))
        mk = finish_market_columns(pl.concat([mk.join(new.select('datetime'), on='datetime', how='anti'), merged],
                                             how='diagonal_relaxed').sort('datetime'))
        atomic_write(mk, paths.market)
        miss = merged.filter(pl.col('nifty_close').is_null()).height
        log(paths, f"market: refreshed {len(todo)} days ({todo[0]:%Y-%m-%d} .. {todo[-1]:%Y-%m-%d})"
                   + (f", {miss} days without a Nifty close" if miss else ""))

    if not args.no_features:
        cmd_features(args, paths)


def cmd_features(args, paths):
    """Rebuild the feature file from the master store + market table (see nse_common.write_features)."""
    write_features(pl.read_parquet(paths.raw), pl.read_parquet(paths.market), paths.out, lambda m: log(paths, m))


def cmd_check(args, paths):
    """Print where everything is and what it contains - the first thing to run on a new PC."""
    print(f"project folder : {project_home()}   (env {HOME_ENV_VAR}={os.environ.get(HOME_ENV_VAR, '<not set>')})")
    for k, _ in C.env_assignments():
        if k != HOME_ENV_VAR:
            print(f"env {k:27s}= {os.environ.get(k, '<not set - default used>')}")
    print(f"data folder    : {paths.data}")
    print(f"python         : {sys.executable}  (polars {pl.__version__})")
    for name, p in (("raw store", paths.raw), ("market table", paths.market), ("feature file", paths.out)):
        if p.exists():
            lf = pl.scan_parquet(p)
            info = lf.select(pl.len().alias('n'), pl.col('datetime').min().alias('a'), pl.col('datetime').max().alias('b')).collect()
            print(f"{name:15s}: {info['n'][0]:,} rows, {info['a'][0]:%Y-%m-%d} .. {info['b'][0]:%Y-%m-%d}  ({p.stat().st_size / 1e6:,.0f} MB)")
        else:
            print(f"{name:15s}: missing ({p})")
    n_cache = sum(1 for _ in (paths.cache / "bhav").glob("*.zip"))
    print(f"cache          : {n_cache} NSE daily files")
    probs = C.validate()
    print(f"contract       : {C.version()} ({C.CONTRACT_PATH}) - "
          + ("all files match" if not probs else f"{len(probs)} problem(s):"))
    for p in probs:
        print(f"                 {p}")


# =====================================================================================================
# COMMAND LINE
# =====================================================================================================
def main():
    ap = HelpfulParser(description=__doc__, epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=None, help="data folder (default: %%NSEDATAENGINE_DATA_DIR%%, else %%NSEDATAENGINE_HOME%%\\data)")
    ap.add_argument("--out", default=None, help="feature file (default: <data-dir>\\features_output_clean.parquet)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="one-time import of the historical feature file",
                       epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", required=True)
    p.add_argument("--force", action="store_true", help="overwrite an existing raw store")
    p = sub.add_parser("update", help="download missing days, adjust, rebuild features",
                       epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--until", help="YYYY-MM-DD (default: today after 7pm IST, else yesterday)")
    p.add_argument("--no-features", action="store_true")
    p.add_argument("--offline", action="store_true", help="use cached files only (testing)")
    sub.add_parser("features", help="rebuild the feature file only")
    sub.add_parser("check", help="show project location and data status")
    args = parse_args_or_help(ap)        # no arguments -> full help with examples
    paths = Paths(args.data_dir or C.location(VARIANT), args.out)
    try:
        {"init": cmd_init, "update": cmd_update, "features": cmd_features, "check": cmd_check}[args.cmd](args, paths)
    except SystemExit:
        raise
    except Exception as e:
        log(paths, f"ERROR: {e!r}")      # also lands in the log file, so scheduled-run failures are visible
        raise


if __name__ == "__main__":
    main()
