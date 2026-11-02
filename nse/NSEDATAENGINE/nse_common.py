"""
nse_common.py - code shared by nse_updater.py and nse_fresh_build.py.

Keeping this in one module means every formula and every rule is written (and explained) once, and both
scripts always compute the dataset the same way. It contains:

  1. Package bootstrap   - installs missing Python packages before anything else is imported
  2. Project location    - finds the project folder (environment variable NSEDATAENGINE_HOME, or this folder)
  3. Shared constants    - NSE URLs, the output column list, market columns
  4. Small helpers       - safe file writing, CSV appending, date parsing
  5. NSE file parsing    - index close file, corporate-action subject -> price factor
  6. Global markets      - Yahoo Finance download for S&P 500, FTSE, DAX, Nikkei, crude, gold, USD/INR
  7. Feature engine      - every indicator column, with the formula and the idea behind it

This file must stay in the same folder as the two scripts (they import it by name).
"""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import datetime as dt
import importlib
import importlib.util
import os
import re
import subprocess
import sys
import time
from pathlib import Path


# =====================================================================================================
# 1. PACKAGE BOOTSTRAP
# -----------------------------------------------------------------------------------------------------
# Why: the scripts are meant to run unattended (Windows Task Scheduler) and to survive a move to a new
# PC. Instead of failing with "ModuleNotFoundError" at 7 pm with nobody watching, they check for each
# package and install what is missing with pip, *into the same Python that is running the script*
# (sys.executable). That matters on Windows, where several Pythons are often installed side by side:
# a plain "pip install" in a terminal could install into a different one than the scheduled task uses.
# =====================================================================================================

# import name -> name on PyPI (they happen to be identical here, but often are not, e.g. sklearn/scikit-learn)
REQUIRED_PACKAGES = {
    "polars": "polars",       # fast multi-threaded dataframes - all the heavy lifting
    "pyarrow": "pyarrow",     # parquet writer used to stream the feature file to disk batch by batch
    "numpy": "numpy",         # array maths for the two indicators that need a loop (Parabolic SAR, CCI)
    "requests": "requests",   # HTTP downloads from NSE
    "yaml": "PyYAML",         # reads contract\\schema.yaml, the column contract (nse_contract.py)
}
# Optional: only the global market columns depend on it, so a failed install must not stop the update.
OPTIONAL_PACKAGES = {"yfinance": "yfinance"}


def _pip_install(pip_name):
    """Install one package into the running Python. Returns True on success.

    First tries a normal install. If that fails (typically "access denied" because Python lives in
    C:\\Program Files and the script is not running as administrator) it retries with --user, which
    installs into the user's own profile folder and never needs admin rights."""
    base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--quiet"]
    for extra in ([], ["--user"]):
        try:
            # timeout: a stuck download must not hang a scheduled run forever (15 minutes is generous)
            r = subprocess.run(base + extra + [pip_name], capture_output=True, text=True, timeout=900)
        except Exception as e:
            print(f"[setup] could not run pip for {pip_name}: {e!r}", flush=True)
            return False
        if r.returncode == 0:
            return True
        err = (r.stderr or r.stdout).strip().splitlines()
        print(f"[setup] pip install {pip_name}{' --user' if extra else ''} failed: "
              f"{err[-1] if err else r.returncode}", flush=True)
    return False


def ensure_packages():
    """Make sure every package is importable; install the missing ones.

    importlib.util.find_spec() asks "could this be imported?" without actually importing it, so the
    check is cheap and has no side effects. After an install, invalidate_caches() makes Python notice the
    newly installed files within the same run."""
    # Some minimal Python installs ship without pip; ensurepip (part of the standard library) restores it.
    if importlib.util.find_spec("pip") is None:
        subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"], capture_output=True)
    missing_required = []
    for group, required in ((REQUIRED_PACKAGES, True), (OPTIONAL_PACKAGES, False)):
        for mod, pip_name in group.items():
            if importlib.util.find_spec(mod) is not None:
                continue                                   # already installed - nothing to do
            print(f"[setup] '{pip_name}' is not installed - installing it now...", flush=True)
            ok = _pip_install(pip_name)
            importlib.invalidate_caches()
            if ok and importlib.util.find_spec(mod) is not None:
                print(f"[setup] installed {pip_name}", flush=True)
            elif required:
                missing_required.append(pip_name)
            else:
                print(f"[setup] could not install optional package {pip_name} - "
                      f"global market columns will stay empty for new days", flush=True)
    if missing_required:
        # Stop with the exact command to run by hand - for the exact Python that needs it.
        sys.exit(f"[setup] required packages could not be installed: {', '.join(missing_required)}. "
                 f"Install them manually:  \"{sys.executable}\" -m pip install {' '.join(missing_required)}")


# Runs at import time, i.e. before numpy / polars are imported just below.
ensure_packages()

import numpy as np    # noqa: E402  (deliberately imported after ensure_packages)
import polars as pl   # noqa: E402
import warnings       # noqa: E402
import nse_contract as C  # noqa: E402  column contract: contract\\schema.yaml is the source of truth

# polars warns that it cannot verify sort order in an as-of join "by" company; the inputs are sorted
# explicitly right before every such join, so the warning is noise in the logs.
warnings.filterwarnings("ignore", message="Sortedness of columns cannot be checked")


# =====================================================================================================
# 2. PROJECT LOCATION
# -----------------------------------------------------------------------------------------------------
# Every path the scripts use is relative to one project folder, so the whole project can be moved to
# another drive or another PC and keep working. The folder is found in this order:
#   1. --data-dir on the command line (explicit, wins over everything - used for tests)
#   2. the environment variable of that folder (NSEDATAENGINE_DATA_DIR, ... - see `locations:` in
#      contract\schema.yaml), set by migrate\register_project.ps1 / unpack_project.ps1
#   3. <project root>\<dir from the contract>; project root = NSEDATAENGINE_HOME, else the folder of this file
# Folder and file names are never written in the scripts: nse_contract.location() / file_path() resolve them.
# Layout inside the project folder:
#   <home>\nse_common.py, nse_updater.py, nse_fresh_build.py, run_update.bat, project_manifest.json
#   <home>\data\         nse_updater.py data (raw store, market table, feature file, cache, logs)
#   <home>\data_fresh\   nse_fresh_build.py data (5-year build, cache, audit trail)
#   <home>\migrate\      pack / unpack / register scripts for moving to a new PC
# =====================================================================================================
HOME_ENV_VAR = C.HOME_ENV_VAR          # NSEDATAENGINE_HOME
SCRIPT_DIR = Path(__file__).resolve().parent


def project_home():
    """Project root: %NSEDATAENGINE_HOME% if it is set and exists (old name NSE_ALGO_HOME still honoured),
    otherwise the folder of this file."""
    return C.project_home()


# =====================================================================================================
# COMMAND-LINE HELP
# -----------------------------------------------------------------------------------------------------
# argparse normally prints a one-line "error: ..." when a command or a required argument is missing.
# HelpfulParser prints the full help - every command, every option and example commands - instead, so
# running a script with no arguments (or the wrong ones) always shows how to use it.
# Sub-command parsers inherit this class automatically (argparse creates them with type(parent)).
# =====================================================================================================
import argparse  # noqa: E402


class HelpfulParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_help(sys.stderr)
        sys.stderr.write(f"\nerror: {message}\n")
        sys.exit(2)


def parse_args_or_help(parser):
    """Parse sys.argv; with no arguments at all, print the full help (including the examples, which
    each script passes as the parser's epilog) and exit without doing anything."""
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)
    return parser.parse_args()


# =====================================================================================================
# 3. SHARED CONSTANTS
# =====================================================================================================
# NSE blocks requests that do not look like a browser, so we send browser-like headers. The Referer tells
# NSE the request comes "from" its own site, which the archive and API servers expect.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
# Daily "bhavcopy" = NSE's official end-of-day price file for every security. UDiFF is the format NSE
# has used since 8 July 2024.
URL_UDIFF = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
# Daily closing values of every NSE index (we take Nifty 50 and India VIX from it).
URL_INDEX = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{d:%d%m%Y}.csv"
# NSE's corporate-action announcements (splits, bonuses, dividends...) as JSON, one query per quarter.
URL_CA = ("https://www.nseindia.com/api/corporates-corporateActions?index=equities"
          "&from_date={a:%d-%m-%Y}&to_date={b:%d-%m-%Y}")

# NSE series kept. EQ = normal equity, BE = trade-for-trade (no intraday netting, often for surveillance),
# BZ = trade-for-trade for non-compliant companies. SME platforms (SM/ST) and everything else are dropped.
SERIES = ["EQ", "BE", "BZ"]

# Today's bhavcopy is normally published by early evening; before this time (IST) "today" is not
# requested so a scheduled run never records a still-missing file as a holiday.
BHAV_READY_IST = dt.time(19, 0)

# NSE's previous-close vs our last close: differences above this are written to a review log.
GAP_LOG_THRESHOLD = 0.02

# Fallback split/bonus detector (used only for quarters where NSE's announcement list could not be
# downloaded): if a stock opens at one of these fractions of its previous close (+-8%), it is treated as a
# split/bonus with that ratio. 1/2 = split 1:2 or bonus 1:1, 1/5 = split 1:5, 2/3 = bonus 1:2, etc.
HEURISTIC_RATIOS = [1/2, 1/3, 1/4, 1/5, 1/10, 1/20, 2/3, 3/5, 2/5]
HEURISTIC_TOL = 0.08

# Yahoo Finance tickers for the global columns.
YF_TICKERS = {"sp500": "^GSPC", "ftse100": "^FTSE", "dax": "^GDAXI", "nikkei225": "^N225",
              "crude_oil": "CL=F", "gold": "GC=F", "usd_inr": "INR=X"}
# Market value columns (everything except the date) come from the column contract, contract\\schema.yaml.
# The duplicate columns of the old Kaggle layout (nifty50, global_close, inr_usd, crude_oil_2, gold_2) were
# retired on 2026-10-03. To add a market column: add it to the contract, then produce it in global_markets
# or parse_index_file; until then it is written as null.
MKT = [c for c in C.columns('market') if c != 'datetime']

F, I = pl.Float64, pl.Int64
# The feature file's columns are defined by the contract (dataset `features` in contract\\schema.yaml).
# features() below computes a superset; nse_contract.conform() keeps only the contract's columns.


# =====================================================================================================
# 4. SMALL HELPERS
# =====================================================================================================
def atomic_write(df, path):
    """Write a parquet file safely: write to <name>.tmp first, then rename over the real file.
    If the PC crashes or the run is killed half way, the previous good file is still intact
    (a rename is atomic on the same disk; a half-written parquet file would be unreadable)."""
    tmp = path.with_name(path.name + ".tmp")
    df.write_parquet(tmp, compression="zstd")   # zstd: good compression, fast to read back
    os.replace(tmp, path)


def append_csv(df, path):
    """Append rows to a CSV log, writing the header only when the file is new. Used for the review logs
    so that several runs accumulate into one file."""
    if df.height == 0:
        return
    if path.exists():
        with open(path, "a", encoding="utf-8", newline="") as f:
            f.write(df.write_csv(include_header=False))
    else:
        df.write_csv(path)


def parse_date(s):
    """Parse the date formats NSE uses in its files and APIs ("10-Jul-2024", "10-07-2024", ...)."""
    s = (s or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %b %Y", "%d-%B-%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def all_days(start, end):
    """Every calendar day from start to end, weekends included.
    Why weekends: NSE occasionally trades on a Saturday or Sunday (Union Budget day, e.g. 1 Feb 2025 and
    1 Feb 2026; Diwali Muhurat sessions). Skipping weekends would miss those sessions, and the next day's
    price would then look like a jump. A weekend with no file simply returns nothing and is remembered."""
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def default_end():
    """Last day to fetch: today if it is already past BHAV_READY_IST in India, otherwise yesterday."""
    ist = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)
    return ist.date() if ist.time() >= BHAV_READY_IST else ist.date() - dt.timedelta(days=1)


def resolve_chain(m):
    """Collapse rename chains: {A: B, B: C} -> {A: C, B: C}, so a ticker renamed twice (A -> B -> C)
    ends up under its latest name. `seen` guards against a (theoretical) circular rename."""
    out = {}
    for k in m:
        v, seen = m[k], {k}
        while v in m and v not in seen:
            seen.add(v)
            v = m[v]
        out[k] = v
    return out


# =====================================================================================================
# 5. NSE FILE PARSING
# =====================================================================================================
def is_equity_share(isin_col):
    """Polars expression: True for ordinary equity shares.
    An Indian ISIN is IN + E (company) + 4-character issuer code + 2-digit security type + serial + check
    digit. Security type "01" = equity shares. This one rule removes ETFs and mutual-fund units (INF...),
    partly-paid shares (IN9...) and rights entitlements (type "20", tickers like ADANI-RE), which trade in
    the same EQ/BE series as shares but are not stocks."""
    return isin_col.str.starts_with("INE") & (isin_col.str.slice(7, 2) == "01")


def parse_index_file(data):
    """NSE daily index file (bytes) -> {'nifty_close': ..., 'vix': ...}. Column names are matched loosely
    because NSE has changed their exact spelling over the years."""
    df = pl.read_csv(data, infer_schema_length=0, truncate_ragged_lines=True)
    df = df.rename({c: c.strip() for c in df.columns})
    name_col = next(c for c in df.columns if c.lower().startswith("index name"))
    close_col = next(c for c in df.columns if "closing" in c.lower())
    out = {}
    for name, val in df.select(name_col, close_col).iter_rows():
        n = (name or "").strip().lower()
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        if n == "nifty 50":
            out["nifty_close"] = v
        elif n == "india vix":
            out["vix"] = v
    return out


def parse_ca_factor(subject):
    """Turn an NSE corporate-action subject into the factor that past prices must be multiplied by.

    Why it is needed: after a split or bonus the share price drops mechanically (a 1:5 split turns a
    Rs 1,000 share into five Rs 200 shares). Unless earlier prices are scaled down by the same factor,
    every indicator sees a fake 80% crash. NSE's daily file does not adjust the previous close for this,
    so the ratio is read from NSE's announcement instead:
      * "Bonus a:b"  (a new shares for every b held): holdings grow from b to a+b shares,
                       so the price factor is b / (a + b).  Bonus 1:1 -> 0.5, bonus 1:2 -> 2/3.
      * "Face value split / sub-division from Rs X to Rs Y": each share becomes X/Y shares,
                       so the factor is Y / X.  Rs 10 -> Rs 2 -> 0.2.
      * "Consolidation from Rs X to Rs Y" (reverse split): same formula, factor > 1.
    Both can appear in one notice ("Bonus 1:2 and face value split from Rs 10 to Rs 5"); the factors
    multiply. Returns (factor, description) or (None, None) if the subject is not one of these
    (dividends, AGMs, rights issues, buy-backs...)."""
    s = (subject or "").lower()
    f, kinds = 1.0, []
    for a, b in re.findall(r"bonus[^0-9]{0,20}(\d+)\s*:\s*(\d+)", s):
        a, b = int(a), int(b)
        if a > 0 and b > 0:
            f *= b / (a + b)
            kinds.append(f"bonus {a}:{b}")
    # "split ... from Rs 10 ... to Rs 2" - tolerant of NSE's spelling variants ("Fv Splt Rs.10 To Rs.1")
    m = re.search(r"(split|splt|sub-?division|consolidat)[^|]*?(?:from\s*(?:rs|re|inr)?\.?|(?:rs|re|inr)\.?)"
                  r"\s*([\d.]+)[^|]*?to\s*(?:rs|re|inr)?\.?\s*([\d.]+)", s)
    if m:
        old, new = float(m.group(2)), float(m.group(3))
        if old > 0 and new > 0 and old != new:
            f *= new / old
            kinds.append(f"face value {old:g} -> {new:g}")
    return (f, "; ".join(kinds)) if kinds else (None, None)


def is_price_action_subject(subject):
    """True for announcements that should change the price history (used to flag unparsed ones)."""
    low = (subject or "").lower()
    return any(k in low for k in ("split", "splt", "bonus", "consolidat", "sub-division", "subdivision"))


# Announcements about instruments other than the equity share. "Scheme Of Arrangement - Bonus Ncrps 4:1"
# (TVSMOTOR 2025) issues bonus *preference* shares: the equity share count does not change, so equity
# prices must NOT be scaled even though the subject contains "Bonus 4:1".
CA_NOT_EQUITY_WORDS = ("ncrps", "preference", "redeemable", "debenture", "ncd")
# Demerger ex-date: the parent's price falls by the value of the business handed to shareholders as new
# shares. NSE's notice carries no ratio, so the factor is taken from the market (see resolve_ca_group).
DEMERGER_WORDS = ("demerg", "spin off", "spin-off")
CA_PRICE_TOL = 0.25            # announced factor vs observed open / previous close: max relative difference


def resolve_ca_group(subjects, obs, tol=CA_PRICE_TOL):
    """Decide the price factor for ALL announcements of one company with the same ex-date.

    Why grouped: NSE files a bonus and a split on the same day as two separate notices. BAJFINANCE on
    16 Jun 2025 had "Bonus 4:1" (factor 0.2) and "Face value split Rs 2 -> Re 1" (factor 0.5); the share
    price fell to 0.1x. Checked one at a time, neither 0.2 nor 0.5 matches the observed 0.1, so both were
    rejected and a fake -90% crash stayed in the data. The factors of different actions multiply.

    Steps
      1. drop notices about preference shares / debentures (CA_NOT_EQUITY_WORDS) - no equity effect
      2. parse each remaining notice (parse_ca_factor); identical actions announced twice count once
      3. combined factor = product of the different actions. Check it against obs (open on the ex-date /
         previous close). If it does not match, try each action alone (covers a duplicate notice worded
         differently). Nothing matches -> not applied, logged for review.
      4. demerger: no ratio in the notice, so factor = obs itself (the market's own valuation of what was
         spun off, as NSE does in the special pre-open session), applied only if the price really fell.
         Any split/bonus on the same day is already inside obs.
    obs may be None (no price on the ex-date): an announced factor is then applied unchecked; a demerger
    cannot be valued and is only logged.

    Returns None (nothing that changes prices) or (status, factor, description, method)."""
    parts, seen, demerger, unparsed, not_equity = [], set(), False, [], []
    for s in subjects:
        low = (s or "").lower()
        if any(w in low for w in CA_NOT_EQUITY_WORDS):
            not_equity.append(s)
            continue
        if any(w in low for w in DEMERGER_WORDS):
            demerger = True
            continue
        f, d = parse_ca_factor(s)
        if f is None:
            if is_price_action_subject(s):
                unparsed.append(s)
            continue
        if d not in seen:                      # the same action announced twice is applied once
            seen.add(d)
            parts.append((f, d))
    combined = float(np.prod([f for f, _ in parts])) if parts else 1.0
    desc_all = "; ".join(d for _, d in parts)
    if demerger:
        desc = "; ".join(x for x in ("demerger", desc_all) if x)
        if obs is None:
            return ("demerger_no_price_data", None, desc, "demerger_price_gap")
        # the price must have fallen (beyond what any split/bonus explains) and not absurdly
        if 0.02 * combined <= obs < 0.995 * combined:   # KESORAMIND 2025 fell to 0.05x - real
            return ("applied", obs, f"{desc} (factor = open / previous close {obs:.4f})", "demerger_price_gap")
        return ("demerger_no_price_drop", None, desc, "demerger_price_gap")
    if not parts:
        if unparsed:
            return ("unparsed_subject", None, None, "nse_announcement")
        if not_equity:
            return ("not_equity_shares", None, "preference shares / debentures - equity prices unchanged",
                    "nse_announcement")
        return None
    if obs is None:
        return ("applied", combined, desc_all, "nse_announcement")
    candidates = [(combined, desc_all, "nse_announcement")]
    if len(parts) > 1:
        candidates += [(f, d, "nse_announcement_partial") for f, d in parts]
    for f, d, m in candidates:
        if abs(obs / f - 1) <= tol:
            return ("applied", f, d, m)
    return ("not_applied_price_gap_differs", combined, desc_all, "nse_announcement")


# =====================================================================================================
# 6. GLOBAL MARKETS (Yahoo Finance)
# =====================================================================================================
def global_markets(start, end, log):
    """Daily closes for the global columns from Yahoo Finance; None if unavailable.

    Each series is downloaded separately (one failing ticker does not lose the others), from 10 days
    before `start` so the first Indian day always has a previous value to carry forward. Markets have
    different holidays, so after joining them each series is forward-filled: on an Indian trading day
    when the US was closed, the last US close is used. That is the value an Indian trader would actually
    have seen that day."""
    try:
        import yfinance as yf
    except ImportError:
        log("yfinance is not available (automatic install failed) - global columns left empty")
        return None
    frames = []
    for col, tk in YF_TICKERS.items():
        try:
            h = yf.download(tk, start=start - dt.timedelta(days=10), end=end + dt.timedelta(days=1),
                            progress=False, auto_adjust=False, threads=False)
            if h is None or len(h) == 0:
                log(f"yfinance {tk}: no data")
                continue
            s = h["Close"]
            if hasattr(s, "columns"):          # newer yfinance returns a one-column frame
                s = s.iloc[:, 0]
            s = s.dropna()
            frames.append(pl.DataFrame({
                "datetime": [dt.datetime(x.year, x.month, x.day) for x in s.index],   # drop time zone
                col: [float(x) for x in s.to_list()],
            }).with_columns(pl.col("datetime").cast(pl.Datetime('ns'))))
        except Exception as e:
            log(f"yfinance {tk} failed: {e!r}")
    if not frames:
        return None
    g = frames[0]
    for f in frames[1:]:
        g = g.join(f, on="datetime", how="full", coalesce=True)
    return g.sort("datetime").with_columns(pl.exclude("datetime").forward_fill())


def finish_market_columns(mk, quiet=False):
    """Shape a market table as the contract says: every contract column present (null if not produced),
    contract types and order; columns the contract does not know are kept at the end.
    quiet=True: no warnings (used on a partial frame that is merged with the stored table afterwards)."""
    return C.conform(mk, 'market', log=(lambda m: None) if quiet else None)


# =====================================================================================================
# 7. FEATURE ENGINE
# -----------------------------------------------------------------------------------------------------
# Golden rule: every rolling / lagged indicator is computed PER COMPANY (".over('company_id')") on rows
# sorted by date. The original file broke this rule - many indicators were computed straight down the
# file, mixing ~20 different stocks into each value - which is why everything is recomputed here.
#
# Windows are counted in trading days (rows), not calendar days. The first N-1 rows of each company
# have no value for an N-day indicator (null) - there is not enough history yet.
# =====================================================================================================
def _psar(h, l, step=0.02, mx=0.2):
    """Parabolic SAR (Wilder) - a trailing stop that follows the trend.

    Idea: in an uptrend the SAR sits below price and moves up every day by
    acceleration_factor x (extreme_point - SAR), where extreme_point = highest high of the trend. Each
    new high raises the acceleration factor by `step` (0.02) up to `mx` (0.20), so the stop tightens as
    the trend matures. When price falls through the SAR the trend flips: the SAR jumps to the extreme
    point and starts trailing from above. The SAR may never sit inside the last two bars' range.
    This needs a day-by-day loop (each value depends on the previous one), hence numpy and not polars."""
    n = len(h)
    out = np.full(n, np.nan)
    if n < 2:
        return out
    up, af, ep, sar = True, step, h[0], l[0]           # assume an uptrend starting from the first bar
    out[0] = sar
    for i in range(1, n):
        sar = sar + af * (ep - sar)                     # move toward the extreme point
        if up:
            sar = min(sar, l[i - 1], l[i - 2] if i > 1 else l[i - 1])   # never above the last 2 lows
            if l[i] < sar:                              # price broke below: reverse to downtrend
                up, sar, ep, af = False, ep, l[i], step
            elif h[i] > ep:                             # new high: extend trend, accelerate
                ep, af = h[i], min(af + step, mx)
        else:
            sar = max(sar, h[i - 1], h[i - 2] if i > 1 else h[i - 1])   # never below the last 2 highs
            if h[i] > sar:                              # price broke above: reverse to uptrend
                up, sar, ep, af = True, ep, h[i], step
            elif l[i] < ep:                             # new low: extend trend, accelerate
                ep, af = l[i], min(af + step, mx)
        out[i] = sar
    return out


def _cci(tp, n=20):
    """Commodity Channel Index (Lambert): (TP - SMA_n(TP)) / (0.015 x mean absolute deviation_n).

    TP = typical price (high + low + close) / 3. CCI measures how far price is from its recent average in
    units of its usual deviation; the 0.015 constant makes roughly 70-80% of values fall between -100 and
    +100, so readings above +100 / below -100 flag unusually strong moves. The mean absolute deviation is
    not a built-in rolling function, so it is computed on sliding windows with numpy."""
    from numpy.lib.stride_tricks import sliding_window_view as swv
    out = np.full(len(tp), np.nan)
    if len(tp) < n:
        return out
    w = swv(tp, n)                                      # every n-day window as a row (no copying)
    m = w.mean(1)
    md = np.abs(w - m[:, None]).mean(1)
    with np.errstate(divide='ignore', invalid='ignore'):  # flat price -> md = 0 -> inf, cleaned later
        out[n - 1:] = (tp[n - 1:] - m) / (0.015 * md)
    return out


def features(d):
    """Compute every indicator column for a batch of companies.

    Input `d`: rows for a set of companies, sorted by company_id then datetime, with the market columns
    (nifty_close, nifty_returns, global_returns, ...) already joined on the date.
    Output: the columns of the `features` dataset in contract\\schema.yaml, in that order. Infinite / NaN results (e.g. division by zero
    on a day with no volume) are stored as null."""
    g = lambda e: e.over('company_id')                  # "per company" wrapper - see golden rule above
    c, o, h, l = [pl.col(x) for x in ['close', 'open', 'high', 'low']]
    v = pl.col('volume').cast(F)
    r = c.pct_change()                                  # daily return: close / previous close - 1
    ema = lambda e, s: e.ewm_mean(span=s, adjust=False)  # EMA, alpha = 2/(span+1), recursive form
    # True range: the day's full movement including any gap from yesterday's close
    tr = pl.max_horizontal(h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs())
    dc = c.diff()                                       # change in close vs previous day

    d = d.with_columns(
        # --- trend / moving averages -------------------------------------------------------------
        # vwap: typical price (H+L+C)/3. Kept under this name for compatibility with the original file;
        # a true VWAP would need intraday traded value.
        vwap=(h + l + c) / 3,
        # MACD = EMA12 - EMA26: positive when short-term momentum is above long-term momentum.
        macd=g(ema(c, 12) - ema(c, 26)),
        # 20-day simple moving average (equal weights) and exponential moving average (recent days weigh more).
        sma_20=g(c.rolling_mean(20)), ema_20=g(ema(c, 20)),
        # 20-day weighted moving average: weights 1..20 (newest = 20), divided by their sum 210.
        wma_20=g(c.rolling_mean(20, weights=[i / 210 for i in range(1, 21)])),
        # Bollinger middle band = SMA20; _std20 (temporary) = 20-day standard deviation for the bands.
        bb_middle=g(c.rolling_mean(20)), _std20=g(c.rolling_std(20)),
        # --- volatility / oscillators ------------------------------------------------------------
        # ATR14 (Wilder): smoothed average of true range, alpha = 1/14. Typical daily movement in rupees.
        atr_14=g(tr.ewm_mean(alpha=1 / 14, adjust=False)),
        # RSI14 = 100 - 100/(1 + avg gain / avg loss) over 14 days. Simple-average (Cutler) variant,
        # as in the original code. >70 is commonly read as overbought, <30 as oversold.
        rsi_14=g(100 - 100 / (1 + dc.clip(lower_bound=0).rolling_mean(14)
                              / (-dc.clip(upper_bound=0)).rolling_mean(14))),
        # temporary: 14-day lowest low / highest high for the stochastic oscillator
        _ll14=g(l.rolling_min(14)), _hh14=g(h.rolling_max(14)),
        # OBV (on-balance volume): running total that adds the day's volume on up days and subtracts it on
        # down days. Rising OBV with flat price suggests accumulation.
        obv=g((v * dc.sign().fill_null(0)).cum_sum()),
        # Rate of change in %: (close / close n days ago - 1) x 100.  roc and roc_10 are both 10-day.
        roc=g((c / c.shift(10) - 1) * 100), roc_5=g((c / c.shift(5) - 1) * 100), roc_10=g((c / c.shift(10) - 1) * 100),
        # --- simple daily price / volume features -----------------------------------------------
        price_change=c - o,                             # intraday move: close - open (rupees)
        price_change_pct=g(r * 100),                    # day-on-day % change of the close
        volume_change=g(v.diff()), volume_change_pct=g(v.pct_change() * 100),
        hl_spread=h - l,                                # day's range in rupees
        co_ratio=c / o,                                 # >1: closed above the open (green candle)
        hl_ratio=h / l,                                 # day's range as a ratio
        price_range=(c - l) / (h - l),                  # where the close sits in the day's range: 0 = low, 1 = high
        price_vol_product=c * v,                        # approximate traded value
        # --- lags: yesterday's / last week's / two weeks ago values -----------------------------
        lag_close_1=g(c.shift(1)), lag_vol_1=g(v.shift(1)), lag_close_5=g(c.shift(5)), lag_vol_5=g(v.shift(5)),
        lag_close_10=g(c.shift(10)), lag_vol_10=g(v.shift(10)),
        # --- calendar (seasonality) -------------------------------------------------------------
        day_of_week=pl.col('datetime').dt.weekday().cast(I),       # Monday = 1 ... Sunday = 7
        # the same day as text ("Monday" ... "Sunday"); %A is the full weekday name, always in English here
        day_name=pl.col('datetime').dt.strftime('%A'),
        month=pl.col('datetime').dt.month().cast(I),
        quarter=pl.col('datetime').dt.quarter().cast(I),
        week_of_year=pl.col('datetime').dt.week().cast(pl.UInt32),  # ISO week number
        # --- 20-day rolling statistics ----------------------------------------------------------
        rolling_std_vol=g(v.rolling_std(20)), rolling_std_close=g(c.rolling_std(20)),
        rolling_mean_close=g(c.rolling_mean(20)),
        rolling_max_close=g(c.rolling_max(20)), rolling_min_close=g(c.rolling_min(20)),
        momentum_raw=g(c - c.shift(5)),                 # 5-day price change in rupees
        volume_sma=g(v.rolling_mean(20)),               # normal daily volume over the last 20 days
        rolling_return=g(r.rolling_mean(20)),           # average daily return over 20 days
        _rstd20=g(r.rolling_std(20)),                   # temporary: 20-day std of daily returns
        # Shape of the last 20 closes: kurtosis (fat tails / spikiness) and skewness (lopsidedness).
        # bias=False = sample-corrected versions (same as pandas' defaults used originally).
        kurtosis=g(c.rolling_kurtosis(20, bias=False)), skewness=g(c.rolling_skew(20, bias=False)),
        # Fibonacci retracement levels inside the day's range, measured down from the high.
        fib_236=h - (h - l) * 0.236, fib_382=h - (h - l) * 0.382, fib_618=h - (h - l) * 0.618,
        # Classic floor-trader pivot (H+L+C)/3 - traders use today's pivot as tomorrow's reference level.
        pivot_point=(h + l + c) / 3,
        returns=g(r),                                   # daily return as a fraction (0.01 = +1%)
        sma_5=g(c.rolling_mean(5)), sma_50=g(c.rolling_mean(50)),
        # volatility = standard deviation of daily returns (not annualised; x sqrt(252) to annualise)
        volatility_5=g(r.rolling_std(5)), volatility_20=g(r.rolling_std(20)),
        hl_range=h - l,                                 # same as hl_spread, kept for compatibility
        hl_pct=(h - l) / c * 100,                       # day's range as % of the close
    ).with_columns(
        # These need columns created in the step above.
        macd_signal=g(ema(pl.col('macd'), 9)),          # 9-day EMA of MACD; MACD crossing it = classic signal
        # Bollinger bands: middle +- 2 standard deviations. ~95% of closes fall inside if returns were normal.
        bb_upper=pl.col('bb_middle') + 2 * pl.col('_std20'), bb_lower=pl.col('bb_middle') - 2 * pl.col('_std20'),
        # Stochastic %K: where the close sits in the 14-day high-low range, 0-100. >80 near the top of the range.
        stoch_k=(c - pl.col('_ll14')) / (pl.col('_hh14') - pl.col('_ll14')) * 100,
        rolling_price_range=pl.col('rolling_max_close') - pl.col('rolling_min_close'),
        # z-score: how many standard deviations the close is above (+) / below (-) its 20-day mean.
        zscore_close=(c - pl.col('rolling_mean_close')) / pl.col('rolling_std_close'),
        price_accel=g(pl.col('price_change').diff()),  # change in the intraday move vs yesterday
        obv_change=g(pl.col('obv').diff()),
        # Relative volume: today's volume vs its 20-day average. 2.0 = twice the usual activity.
        vol_ratio=v / pl.col('volume_sma'), volume_ratio=v / pl.col('volume_sma'),
        # Sharpe-style ratio: mean / std of daily returns over 20 days (no risk-free rate, not annualised).
        # The original file divided returns by the std of PRICE, which is not meaningful - corrected.
        sharpe_ratio=pl.col('rolling_return') / pl.col('_rstd20'),
        # Beta (60 days): cov(stock returns, Nifty returns) / var(Nifty returns).
        # 1.5 = the stock tends to move 1.5% when Nifty moves 1%.
        beta_60=g(pl.rolling_cov(pl.col('returns'), pl.col('nifty_returns'), window_size=60)
                  / pl.col('nifty_returns').rolling_var(60)),
        # 60-day correlation of the stock's returns with Nifty / with the S&P 500 (-1 .. +1).
        corr_market=g(pl.rolling_corr(pl.col('returns'), pl.col('nifty_returns'), window_size=60)),
        corr_global=g(pl.rolling_corr(pl.col('returns'), pl.col('global_returns'), window_size=60)),
    ).with_columns(
        macd_hist=pl.col('macd') - pl.col('macd_signal'),   # MACD histogram: momentum of the momentum
        stoch_d=g(pl.col('stoch_k').rolling_mean(3)),       # %D = 3-day average of %K (smoother signal line)
    )

    # Parabolic SAR and CCI need numpy (loop / sliding windows). Rows are sorted by company, so each
    # company is a contiguous block; `b` holds the block boundaries.
    sar = np.empty(d.height)
    cc = np.empty(d.height)
    H, L = d['high'].to_numpy(), d['low'].to_numpy()
    TP = ((d['high'] + d['low'] + d['close']) / 3).to_numpy()
    ids = d['company_id'].to_numpy()
    b = np.flatnonzero(np.r_[True, ids[1:] != ids[:-1], True])
    for s, e in zip(b[:-1], b[1:]):
        sar[s:e] = _psar(H[s:e], L[s:e])
        cc[s:e] = _cci(TP[s:e])
    d = d.with_columns(sar=pl.Series(sar), cci_20=pl.Series(cc))

    # inf / NaN (division by zero etc.) -> null, so downstream code sees "missing" rather than a fake number
    fl = [k for k, t in d.schema.items() if t == F]
    d = d.with_columns([pl.when(pl.col(k).is_finite()).then(pl.col(k)).otherwise(None).alias(k) for k in fl])
    # only the contract's feature columns are written (intermediate / retired columns are dropped)
    return C.conform(d, 'features', keep_unknown=False)


def write_features(raw, mk, out_path, log, batch_size=150):
    """Compute features for every company and stream them to one parquet file.

    Why batches: computing ~100 columns for 7+ million rows at once needs far more memory than most PCs
    have. Instead, 150 companies are processed at a time and each batch is appended to the output file
    with a pyarrow ParquetWriter, so memory stays roughly constant whatever the dataset size. Within a
    batch polars still uses every CPU core. The file is written to .tmp and renamed at the end (see
    atomic_write) so an interrupted run never leaves a half-written feature file behind."""
    import pyarrow.parquet as pq
    t0 = time.time()
    # Market returns are computed on the market table itself (one row per day), not per company.
    mk = (mk.sort('datetime')
            .with_columns(nifty_returns=pl.col('nifty_close').pct_change(),
                          global_returns=pl.col('sp500').pct_change()))
    ids = raw['company_id'].unique().sort().to_list()
    tmp = out_path.with_name(out_path.name + ".tmp")
    writer = None
    try:
        for i in range(0, len(ids), batch_size):
            d = (raw.filter(pl.col('company_id').is_in(ids[i:i + batch_size])).sort('company_id', 'datetime')
                    .join(mk, on='datetime', how='left'))
            t = features(d).to_arrow()
            if writer is None:
                writer = pq.ParquetWriter(tmp, t.schema, compression='zstd')
            writer.write_table(t, row_group_size=250_000)   # row groups of 250k rows: fast filtered reads
    finally:
        if writer is not None:
            writer.close()
    os.replace(tmp, out_path)
    log(f"features: {raw.height:,} rows, {len(ids)} companies, "
        f"{raw['datetime'].min():%Y-%m-%d} .. {raw['datetime'].max():%Y-%m-%d} -> {out_path} "
        f"({time.time() - t0:.0f}s)")
