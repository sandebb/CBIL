"""
nse_intraday.py - one row per stock per day of INTRADAY features, built from NSEALGOENGINE's 1-minute archive
(Fyers bars 2018 -> today), for the parallel NSEMLENGINE model "ALT-INTRADAY" (contract dataset intraday_daily,
data_ref\\intraday_daily.parquet).

Every column is "scale-free" (returns, shares of the day's volume, positions in the day), so splits / bonuses in the
minute prices do not matter. They describe HOW the day traded, which a daily bar cannot show:
  volatility   5-minute realized volatility, its jump part, downside part, skew, how much came in the first 30 min
               and the last hour (best documented use of minute data: forecasting the next day's range)
  timing       returns of the first 30 min / middle / last 30 min, where the official close sits vs the last trade
               and vs the day's VWAP, when the high and the low were made
  volume       share traded in the first / last 30 min, biggest single minute, minutes without trades,
               buying vs selling pressure (volume of up-minutes minus down-minutes), also for the last 30 min

POINT IN TIME: a row describes the whole session (09:15-15:30) of `date`, so it is known after the minute archive
task (NSEALGOENGINE-MinuteArchive, 19:05) - usable by the NIGHTLY forecast for the next session, NOT by the 15:37
at-close forecast (that one would need today's bars up to 15:30 fetched live).

Known quirks of the Fyers minute data (checked 23 Oct 2026 against NSE's daily file):
  * 08-Nov-2023 .. 03-Oct-2025: the 09:15 bar also holds the opening-auction volume (minute volume = 99.96 % of
    NSE's, else ~99.5 %). Volume shares here therefore leave the 09:15 bar out; its own share is kept as
    id_vol_first_bar together with the flag first_bar_auction - don't feed id_vol_first_bar to a model as is.
  * Since 2026 the 15:29 bar carries the official close (15:00-15:30 VWAP), not a trade. Prices here use bars up to
    15:28 as "trades" and take the official close from raw_ohlcv.
  * Special sessions (Muhurat trading, half days) and days with missing bars get ok = false.
  * The newest day, before NSE has published the official close (the 15:37 at-close build), is ok on the
    session-shape checks alone; the nightly re-run re-checks it with the published close.

Usage
  python nse_intraday.py build                 all stocks, all days (first run; ~10-30 min, uses all cores)
  python nse_intraday.py daily                 only new days (run_update.bat runs it nightly after the updater)
  python nse_intraday.py coverage              years of minute data per stock + data checks -> data_ref\\logs\\
  python nse_intraday.py show                  rows / stocks / last day
  options: --only SBIN,TCS (rebuild some stocks, keep the rest)   --workers 8   --archive <folder>
           --if-missing (build only when the file is not there yet - used by migrate\\setup_new_pc.ps1)
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import datetime as dt
import math
import os
import sys
import time
from pathlib import Path

import nse_common as NC  # noqa: F401  (installs missing packages on import)
import nse_contract as C
from nse_common import atomic_write, pl

DATASET = "intraday_daily"
AUCTION_IN_FIRST_BAR = (dt.date(2023, 11, 8), dt.date(2025, 10, 3))   # see module docstring
OPEN_MIN = 9 * 60 + 15            # 09:15 = minute 0
LAST_TRADE_M = 373                # 15:28 bar = last bar used as a trade
CLOSE_M = 374                     # 15:29 bar (official close mark since 2026)
REDO_DAYS = 7                     # daily: recompute this many calendar days back per stock (late / partial bars)
WARMUP_DAYS = 21                  # daily: read this much more history, so the 10-session medians match a full build
LOG = C.location("ref") / "logs" / "intraday_log.txt"


def log(msg: str) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def archive_dir(arg: str | None = None) -> Path:
    return Path(arg) if arg else C.location("minute_archive")


# ---------------------------------------------------------------------------------------------------------------
# reading one stock's minute bars
# ---------------------------------------------------------------------------------------------------------------
def _span_end(f: Path) -> dt.date | None:
    try:
        return dt.date.fromisoformat(f.stem.split("_")[1])
    except (IndexError, ValueError):
        return None


def read_bars(folder: Path, since: dt.date | None = None) -> pl.DataFrame:
    """All bars of one stock (span files from the archive), optionally only spans ending on/after `since`."""
    import pyarrow.parquet as pq
    parts = []
    for f in sorted(folder.glob("*_*.parquet")):
        end = _span_end(f)
        if end is None or (since and end < since):
            continue
        try:
            if pq.read_metadata(f).num_rows == 0:          # "Fyers has no bars for this span"
                continue
            p = pl.read_parquet(f, columns=["ts", "open", "high", "low", "close", "volume"])
        except Exception as ex:                             # half-written / damaged file: skip, report
            print(f"  skip {f}: {ex}", file=sys.stderr)
            continue
        parts.append(p.with_columns(pl.col("ts").cast(pl.Datetime("ms")),
                                    pl.col("open", "high", "low", "close").cast(pl.Float64),
                                    pl.col("volume").cast(pl.Int64)))
    if not parts:
        return pl.DataFrame(schema={"ts": pl.Datetime("ms"), "open": pl.Float64, "high": pl.Float64,
                                    "low": pl.Float64, "close": pl.Float64, "volume": pl.Int64})
    df = pl.concat(parts).unique("ts", keep="last").sort("ts")
    if since:
        df = df.filter(pl.col("ts").dt.date() >= since)
    return df


# ---------------------------------------------------------------------------------------------------------------
# the features (pure function: bars of one stock + its daily rows -> one row per day)
# ---------------------------------------------------------------------------------------------------------------
def day_features(bars: pl.DataFrame, daily: pl.DataFrame, company_id: str) -> pl.DataFrame:
    """bars: ts, open, high, low, close, volume (1-minute, IST). daily: date, open, close, volume (raw_ohlcv)."""
    if bars.height == 0:
        return pl.DataFrame()
    m = pl.col("m")
    b = (bars.with_columns(pl.col("ts").dt.date().alias("date"),
                           (pl.col("ts").dt.hour().cast(pl.Int32) * 60 + pl.col("ts").dt.minute().cast(pl.Int32)
                            - OPEN_MIN).alias("m"))
         .filter((m >= 0) & (m <= CLOSE_M))
         .sort("ts"))
    v, c, o = pl.col("volume"), pl.col("close"), pl.col("open")
    trade = m <= LAST_TRADE_M
    core = (m >= 1) & trade                      # leaves out the 09:15 bar (auction volume in 2023-25)
    last30 = (m >= 345) & trade                  # 15:00 - 15:28
    sgn = (c - o).sign()

    def price_at(mm):                            # last trade close at or before minute mm
        return c.filter(m <= mm).last()

    d = b.group_by("date").agg(
        pl.len().alias("n_bars"),
        m.min().alias("_m_first"),
        m.filter(trade).max().alias("_m_last"),
        o.filter(m == 0).first().alias("_open0"),
        o.first().alias("_open_first"),
        price_at(29).alias("_p29"),
        price_at(344).alias("_p344"),
        c.filter(trade).last().alias("_last"),
        ((pl.col("high") + pl.col("low") + c) / 3 * v).filter(trade).sum().alias("_pv"),
        v.filter(trade).sum().alias("_v_trade"),
        v.sum().alias("_v_all"),
        v.filter(m == 0).sum().alias("_v0"),
        v.filter(m == CLOSE_M).sum().alias("_v_close_bar"),
        v.filter(core).sum().alias("_v_core"),
        v.filter(core & (m <= 29)).sum().alias("_v_first30"),
        v.filter(last30).sum().alias("_v_last30"),
        v.filter(core).max().alias("_v_max"),
        (v.filter(trade) > 0).sum().alias("_n_traded"),
        (v * sgn).filter(core).sum().alias("_sv"),
        (v * sgn).filter(last30).sum().alias("_sv_last30"),
        # first minute at the day's high / low (a filter, not gather(arg_max): that crashes polars 1.21 at times)
        m.filter(trade & (pl.col("high") == pl.col("high").filter(trade).max())).first().alias("_m_hi"),
        m.filter(trade & (pl.col("low") == pl.col("low").filter(trade).min())).first().alias("_m_lo"),
    )

    # 5-minute realized measures from the last trade price of each 5-minute block (09:15-15:28)
    k = (b.filter(trade).with_columns((m // 5).alias("k"))
         .group_by("date", "k").agg(c.last().alias("ck"))
         .sort("date", "k")
         .join(d.select("date", pl.coalesce("_open0", "_open_first").alias("_o")), on="date", how="left")
         .with_columns((pl.col("ck").log() - pl.col("ck").shift(1).over("date").log()).alias("r")))
    k = k.with_columns(pl.when(pl.col("r").is_null()).then((pl.col("ck") / pl.col("_o")).log())
                       .otherwise(pl.col("r")).alias("r"))
    k = k.with_columns(pl.col("r").abs().shift(1).over("date").alias("_r_prev"))
    r = pl.col("r")
    rk = k.group_by("date").agg(
        pl.len().alias("_n5"),
        (r ** 2).sum().alias("_rv2"),
        (r.abs() * pl.col("_r_prev")).sum().alias("_bp"),
        (r ** 2).filter(r < 0).sum().alias("_rv2_down"),
        (r ** 3).sum().alias("_r3"),
        (r ** 2).filter(pl.col("k") <= 5).sum().alias("_rv2_first30"),
        (r ** 2).filter(pl.col("k") >= 63).sum().alias("_rv2_last60"),
    )

    day = (daily.select("date", pl.col("open").alias("_nse_open"), pl.col("close").alias("_nse_close"),
                        pl.col("volume").cast(pl.Float64).alias("_nse_volume")))
    x = d.join(rk, on="date", how="left").join(day, on="date", how="left").sort("date")
    opn = pl.coalesce("_open0", "_open_first")
    # Price scale of the minute bars vs raw_ohlcv. Usually 1.0, but the two sources do not always adjust the same
    # corporate actions (e.g. RELIANCE before the 2023 Jio Financial demerger: minute prices 8.3 % higher, volume
    # 8.3 % lower). px_factor = minute open / NSE open, smoothed with the median of the last 10 sessions (past
    # only), converts NSE's official close and volume to the minute scale. A day whose own factor is off that
    # median (bad open, adjustment step) gets ok = false.
    f = (x.select("date", (opn / pl.col("_nse_open")).alias("_f_day"))
         .filter(pl.col("_f_day").is_finite() & (pl.col("_f_day") > 0))
         .with_columns(pl.col("_f_day").log().rolling_median(window_size=10, min_samples=1).exp().alias("px_factor")))
    x = x.join(f, on="date", how="left").with_columns(
        (pl.col("_nse_close") * pl.col("px_factor")).alias("_nse_close"))
    # Volume scale: compared with the stock's own recent ratio, not with 1.0 - raw_ohlcv's corporate-action code
    # also divides VOLUME by the price factor on demergers (VEDL, SIEMENS, TRIVENI ...: NSE volume a fixed 2-5x the
    # minute volume before the event), while Fyers adjusts the price only.
    vr = (x.select("date", (pl.col("_v_all") / pl.col("_nse_volume")).alias("_vr_day"))
          .filter(pl.col("_vr_day").is_finite() & (pl.col("_vr_day") > 0))
          .with_columns(pl.col("_vr_day").log().rolling_median(window_size=10, min_samples=1).alias("_vr_med_log")))
    x = x.join(vr, on="date", how="left")
    rv2 = pl.col("_rv2")
    safe = lambda e: pl.when(e.is_finite()).then(e).otherwise(None)        # noqa: E731
    lo, hi = AUCTION_IN_FIRST_BAR
    x = x.with_columns(
        pl.lit(company_id).alias("company_id"),
        safe((pl.col("_p29") / opn).log()).alias("id_ret_first30"),
        safe((pl.col("_p344") / pl.col("_p29")).log()).alias("id_ret_mid"),
        safe((pl.col("_last") / pl.col("_p344")).log()).alias("id_ret_last30"),
        safe((pl.col("_nse_close") / pl.col("_last")).log()).alias("id_close_mark"),
        safe((pl.col("_nse_close") / (pl.col("_pv") / pl.col("_v_trade"))).log()).alias("id_vwap_dev"),
        rv2.sqrt().alias("id_rv5"),
        (math.pi / 2 * pl.col("_bp")).sqrt().alias("id_bv5"),
        safe(pl.max_horizontal(pl.lit(0.0), 1 - (math.pi / 2 * pl.col("_bp")) / rv2)).alias("id_jump_share"),
        safe(pl.col("_rv2_down") / rv2).alias("id_down_share"),
        safe(pl.col("_n5").cast(pl.Float64).sqrt() * pl.col("_r3") / rv2.pow(1.5)).alias("id_rskew5"),
        safe(pl.col("_rv2_first30") / rv2).alias("id_rv_first30_share"),
        safe(pl.col("_rv2_last60") / rv2).alias("id_rv_last60_share"),
        (pl.col("_m_hi") / LAST_TRADE_M).alias("id_hi_time"),
        (pl.col("_m_lo") / LAST_TRADE_M).alias("id_lo_time"),
        safe(pl.col("_v_first30") / pl.col("_v_core")).alias("id_vol_first30"),
        safe(pl.col("_v_last30") / pl.col("_v_core")).alias("id_vol_last30"),
        safe(pl.col("_v_max") / pl.col("_v_core")).alias("id_vol_max_bar"),
        safe(pl.col("_v0") / pl.col("_v_all")).alias("id_vol_first_bar"),
        safe(pl.col("_v_close_bar") / pl.col("_v_all")).alias("id_vol_close_bar"),
        (1 - pl.col("_n_traded") / (LAST_TRADE_M + 1)).clip(0, 1).alias("id_no_trade_share"),
        safe(pl.col("_sv") / pl.col("_v_core")).alias("id_signed_vol"),
        safe(pl.col("_sv_last30") / pl.col("_v_last30")).alias("id_signed_vol_last30"),
        safe(pl.col("_v_all") / pl.col("_nse_volume")).alias("id_vol_ratio_nse"),
        pl.col("date").is_between(lo, hi).alias("first_bar_auction"),
    )
    # Usable day: a normal session (first bar by 09:45, trading past 15:15 - quiet minutes have no bar, so the bar
    # COUNT is not a test; special 1-hour sessions fail here), the open on the usual minute/NSE price scale and the
    # day's volume within 10 % of the stock's usual minute/NSE ratio (both need the NSE official close).
    core = ((pl.col("_m_first") <= 30) & (pl.col("_m_last") >= 360) & (pl.col("n_bars") >= 30)
            & (pl.col("_v_core") > 0) & pl.col("id_rv5").is_not_null())
    nse = (pl.col("_nse_close").is_not_null()
           & ((pl.col("_f_day") / pl.col("px_factor")).log().abs() < 0.005)
           & ((pl.col("_vr_day").log() - pl.col("_vr_med_log")).abs() < math.log(1.1)))
    # A day whose NSE official close is not published yet (the 15:37 at-close build - raw_ohlcv gets the day at the
    # 19:30 update) is ok on the session shape alone: id_close_mark / id_vwap_dev stay null until the close is known,
    # and the nightly re-run (run_update.bat) recomputes the day with the full check. Only days at/after the newest
    # published raw day qualify, so a historical data gap keeps ok = false, as before.
    raw_last = daily["date"].max() if daily.height else None
    prelim = (pl.col("_nse_close").is_null() & (pl.col("date") >= raw_last)) if raw_last is not None else pl.lit(False)
    ok = core & (nse | prelim)
    x = x.with_columns(ok.fill_null(False).alias("ok"), pl.col("n_bars").cast(pl.Int32))
    keep = [c for c in C.columns(DATASET)]
    return x.select([c for c in keep if c in x.columns]).sort("date")


# ---------------------------------------------------------------------------------------------------------------
# worker (one stock) - top-level so Windows multiprocessing (spawn) can pickle it
# ---------------------------------------------------------------------------------------------------------------
def _work(args):
    folder, company_id, daily, since = args
    t = time.time()
    try:
        # read 3 weeks more than is rewritten, so px_factor (median of the last 10 sessions) is the same as in a
        # full build
        bars = read_bars(Path(folder), since - dt.timedelta(days=WARMUP_DAYS) if since else None)
        out = day_features(bars, daily, company_id)
        if since and out.height:
            out = out.filter(pl.col("date") >= since)
        return company_id, out, None, time.time() - t
    except Exception as ex:                                  # one bad stock must not stop the run
        return company_id, None, f"{type(ex).__name__}: {ex}", time.time() - t


def _daily_rows(start: dt.date) -> dict:
    raw = C.read("raw_ohlcv", columns=["company_id", "datetime", "open", "close", "volume"], lazy=True)
    raw = (raw.filter(pl.col("datetime") >= dt.datetime.combine(start, dt.time()))
           .with_columns(pl.col("datetime").dt.date().alias("date")).drop("datetime").collect())
    return {k[0] if isinstance(k, tuple) else k: g for k, g in raw.partition_by("company_id", as_dict=True).items()}


def run(mode: str, only: list[str], workers: int, archive: Path) -> None:
    out_path = C.file_path(DATASET, "ref")
    stocks = sorted(p for p in archive.iterdir() if p.is_dir()) if archive.exists() else []
    if only:
        want = {s.upper() for s in only}
        stocks = [p for p in stocks if p.name.upper() in want]
    if not stocks:
        raise SystemExit(f"no stock folders in {archive} - is the minute archive there? (nsealgo fetch-minute-archive)")
    # daily: keep everything, replace the recomputed days; build --only: keep the other stocks; build: start over
    old = pl.read_parquet(out_path) if ((mode == "daily" or only) and out_path.exists()) else None
    if mode == "build" and old is not None:
        old = old.filter(~pl.col("company_id").is_in([p.name for p in stocks]))
    last = {} if old is None else dict(old.group_by("company_id").agg(pl.col("date").max()).iter_rows())
    if mode == "daily" and old is None:
        log("no intraday_daily file yet - doing a full build")
        mode = "build"
    start = dt.date(2017, 12, 1)
    if mode == "daily" and last:
        start = min(last.values()) - dt.timedelta(days=REDO_DAYS + WARMUP_DAYS)
    daily = _daily_rows(start)
    empty = pl.DataFrame(schema={"date": pl.Date, "open": pl.Float64, "close": pl.Float64, "volume": pl.Int64})
    jobs = []
    for p in stocks:
        since = None
        if mode == "daily":
            since = (last[p.name] - dt.timedelta(days=REDO_DAYS)) if p.name in last else None
        jobs.append((str(p), p.name, daily.get(p.name, empty).drop("company_id", strict=False), since))
    no_daily = [p.name for p in stocks if p.name not in daily]
    log(f"{mode}: {len(jobs)} stocks from {archive}, {workers} worker(s)"
        + (f"; {len(no_daily)} not in raw_ohlcv (ok = false): {', '.join(no_daily[:15])}" if no_daily else ""))

    t0, parts, errors = time.time(), [], []
    if workers <= 1:
        results = map(_work, jobs)
    else:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor
        os.environ.setdefault("POLARS_MAX_THREADS", "2")     # children: many processes x few threads each
        # "spawn" (fresh processes, the Windows default) also on Linux: forking a process that already runs
        # polars threads can deadlock
        ex = ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn"))
        results = ex.map(_work, jobs, chunksize=4)
    for i, (sym, df, err, secs) in enumerate(results, 1):
        if err:
            errors.append((sym, err))
        elif df is not None and df.height:
            parts.append(df)
        if i % 100 == 0 or i == len(jobs):
            log(f"  {i}/{len(jobs)} stocks, {sum(p.height for p in parts):,} days, {time.time() - t0:.0f} s")
    if workers > 1:
        ex.shutdown()
    for sym, err in errors:
        log(f"  FAILED {sym}: {err}")
    if not parts and old is None:
        raise SystemExit("nothing built")
    new = pl.concat(parts, how="diagonal_relaxed") if parts else None
    if old is not None and new is not None:                   # replace the recomputed (company, date) rows
        old = old.join(new.select("company_id", "date"), on=["company_id", "date"], how="anti")
        new = pl.concat([old, new], how="diagonal_relaxed")
    elif new is None:
        new = old
    new = C.conform(new, DATASET, log=log).sort("company_id", "date")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(new, out_path)
    log(f"wrote {out_path}: {new.height:,} rows, {new['company_id'].n_unique()} stocks, "
        f"{new['date'].min()} .. {new['date'].max()}, ok {new['ok'].mean():.1%}  ({time.time() - t0:.0f} s)")
    if errors:
        sys.exit(1)


# ---------------------------------------------------------------------------------------------------------------
# coverage report
# ---------------------------------------------------------------------------------------------------------------
def coverage() -> None:
    p = C.file_path(DATASET, "ref")
    if not p.exists():
        raise SystemExit(f"{p} not found - run: python nse_intraday.py build")
    x = pl.read_parquet(p, columns=["company_id", "date", "ok", "n_bars", "id_vol_ratio_nse", "first_bar_auction"])
    first = x["date"].min()
    raw = (C.read("raw_ohlcv", columns=["company_id", "datetime"], lazy=True)
           .with_columns(pl.col("datetime").dt.date().alias("date"))
           .filter(pl.col("date") >= first).select("company_id", "date").collect())
    span = x.group_by("company_id").agg(pl.col("date").min().alias("first_day"), pl.col("date").max().alias("last_day"))
    # NSE trading days of each stock inside its minute-data window = what the archive should have
    exp = (raw.join(span, on="company_id")
           .filter(pl.col("date").is_between(pl.col("first_day"), pl.col("last_day")))
           .group_by("company_id").agg(pl.len().alias("nse_days")))
    rep = (x.group_by("company_id").agg(
                pl.col("date").min().alias("first_day"), pl.col("date").max().alias("last_day"),
                pl.len().alias("minute_days"), pl.col("ok").sum().alias("ok_days"),
                pl.col("id_vol_ratio_nse").median().round(4).alias("median_volume_vs_nse"))
           .join(exp, on="company_id", how="left")
           .with_columns((pl.col("nse_days").cast(pl.Int64) - pl.col("minute_days").cast(pl.Int64)).clip(0).alias("days_missing"),
                         (pl.col("ok_days") / 248).round(1).alias("years_usable"),
                         (pl.col("ok_days") / pl.col("nse_days") * 100).clip(0, 100).round(1).alias("pct_usable"))
           .sort("years_usable"))
    out = C.location("ref") / "logs" / "intraday_coverage.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    rep.write_csv(out)
    yrs = rep["years_usable"]
    print(f"\nMinute data per stock ({rep.height} stocks, {first} .. {x['date'].max()}):")
    for lo_, hi_, label in ((7.5, 99, "7.5+ years"), (5, 7.5, "5 - 7.5 years"), (3, 5, "3 - 5 years"),
                            (1, 3, "1 - 3 years"), (0, 1, "under 1 year")):
        n = int(((yrs >= lo_) & (yrs < hi_)).sum())
        print(f"  {label:<15} {n:>4} stocks")
    print(f"  usable days overall: {rep['ok_days'].sum() / max(rep['nse_days'].sum(), 1):.1%} of NSE trading days")
    by_year = (x.with_columns(pl.col("date").dt.year().alias("year")).group_by("year")
               .agg(pl.col("company_id").n_unique().alias("stocks"), pl.col("ok").mean().round(3).alias("ok_share"),
                    pl.col("id_vol_ratio_nse").median().round(4).alias("volume_vs_nse"))
               .sort("year"))
    print("\nBy year (stocks with minute data, share of usable days, minute volume / NSE volume):")
    for y, s, okr, vr in by_year.iter_rows():
        print(f"  {y}  {s:>4} stocks   usable {okr:.1%}   volume vs NSE {vr}")
    worst = rep.filter(pl.col("pct_usable") < 90).sort("pct_usable").head(15)
    if worst.height:
        print("\nLeast complete (usable days < 90 % of NSE days in their window):")
        for r in worst.iter_rows(named=True):
            print(f"  {r['company_id']:<12} {r['first_day']} .. {r['last_day']}  usable {r['pct_usable']}%  "
                  f"missing days {r['days_missing']}")
    print(f"\nFull table: {out}")


def show() -> None:
    p = C.file_path(DATASET, "ref")
    if not p.exists():
        print(f"{p}: not built yet (python nse_intraday.py build)")
        return
    x = pl.read_parquet(p, columns=["company_id", "date", "ok"])
    print(f"{p}\n  {x.height:,} rows, {x['company_id'].n_unique()} stocks, {x['date'].min()} .. {x['date'].max()}, "
          f"usable {x['ok'].mean():.1%}")
    lastday = x["date"].max()
    print(f"  last day {lastday}: {x.filter(pl.col('date') == lastday).height} stocks")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["build", "daily", "coverage", "show"])
    ap.add_argument("--only", default="", help="comma-separated symbols")
    ap.add_argument("--workers", type=int, default=max(1, min(12, (os.cpu_count() or 2) - 2)))
    ap.add_argument("--archive", default=None, help="minute archive folder (default: contract location minute_archive)")
    ap.add_argument("--if-missing", action="store_true",
                    help="build: only if the file does not exist yet and the archive is there (new-PC setup)")
    a = ap.parse_args()
    if a.if_missing:
        arch = archive_dir(a.archive)
        if C.file_path(DATASET, "ref").exists():
            print(f"{C.file_path(DATASET, 'ref')} exists - nothing to do")
            return
        if not (arch.exists() and any(p.is_dir() for p in arch.iterdir())):
            print(f"no minute archive at {arch} yet - skipped (later: python nse_intraday.py build)")
            return
    if a.cmd in ("build", "daily"):
        run(a.cmd, [s for s in a.only.split(",") if s], a.workers, archive_dir(a.archive))
    elif a.cmd == "coverage":
        coverage()
    else:
        show()


if __name__ == "__main__":
    main()
