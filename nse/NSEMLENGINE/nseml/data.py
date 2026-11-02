"""Inputs from NSEDATAENGINE, read by name through its contract (columns added there later do no harm)."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import datetime as dt

import polars as pl

from .config import cfg, contract

RAW_COLS = ["company_id", "datetime", "open", "high", "low", "close", "volume"]


def load_raw(start: str | None = None, companies: list[str] | None = None, end=None) -> pl.DataFrame:
    C, c = contract(), cfg()
    lf = C.read("raw_ohlcv", RAW_COLS, variant=c["data"]["variant"], lazy=True)
    lf = lf.filter(pl.col("datetime") >= dt.datetime.fromisoformat(start or c["data"]["warmup_start"]))
    if end is not None:
        lf = lf.filter(pl.col("datetime") <= end)
    if companies is not None:
        lf = lf.filter(pl.col("company_id").is_in(companies))
    return lf.sort("company_id", "datetime").collect()


def load_market(end=None, extra: dict | None = None) -> pl.DataFrame:
    """Market table with every column lagged so that row t only holds values known at prediction time
    (after the 19:30 IST update on day t). Returns datetime + <col> levels (lagged).
    extra = {"datetime": today, "nifty_close": .., "vix": .., "nikkei225": ..}: a row for a day NSEDATAENGINE does not
    have yet (the 15:37 at-close forecast). Its lag-0 columns are used as given (a missing one repeats the previous
    value); lagged columns are never read from it."""
    C, c = contract(), cfg()
    lags = c["market_lag"]
    mk = C.read("market", ["datetime"] + list(lags), variant=c["data"]["variant"]).sort("datetime")
    if end is not None:
        mk = mk.filter(pl.col("datetime") <= end)
    if extra is not None:
        day = extra["datetime"]
        mk = mk.filter(pl.col("datetime") < day)
        row = {"datetime": day, **{k: extra.get(k) for k in lags}}
        mk = pl.concat([mk, pl.DataFrame([row], schema=mk.schema)], how="vertical_relaxed")
        zero = [k for k, n in lags.items() if not int(n)]
        mk = mk.with_columns([pl.col(k).fill_null(strategy="forward") for k in zero])
    return mk.with_columns([pl.col(k).shift(int(n)).alias(k) for k, n in lags.items() if int(n)])


def load_fno() -> pl.DataFrame:
    """(company_id, datetime, is_fno) for every day a stock had stock futures."""
    C = contract()
    f = C.read("fno_universe", ["date", "company_id"], variant="ref")
    return (f.select(pl.col("date").cast(pl.Datetime("ns")).alias("datetime"), "company_id")
             .unique().with_columns(is_fno=pl.lit(1, dtype=pl.Int8)))


def first_fno_date() -> dt.datetime:
    return contract().read("fno_universe", ["date"], variant="ref")["date"].min()


RANGES: dict = {}       # a_* column -> (first, last) day its source covers: a missing stock there means 0, not unknown


def load_alt(log=print) -> pl.DataFrame | None:
    """company_id, datetime + a_* columns for the alt model, each on the first day it was public:
    delivery and short selling on their own day (published that evening), MTF on its `disclosed` date (next session).
    Short selling: a stock with no row on a day the file covers sold 0 short (not unknown)."""
    C = contract()
    parts = []
    try:
        dv = (C.read("delivery", ["company_id", "date", "traded_qty", "deliv_qty"], variant="ref")
              .group_by("company_id", "date").agg(pl.col("traded_qty").sum().alias("a_traded_qty"),
                                                  pl.col("deliv_qty").sum().alias("a_deliv_qty")))
        parts.append(dv)
    except Exception as ex:
        log(f"  alt: delivery not available ({ex})")
    short_days = mtf_days = None
    try:
        ss = C.read("short_selling", ["company_id", "date", "qty"], variant="ref")
        short_days = ss["date"].unique()
        parts.append(ss.group_by("company_id", "date").agg(pl.col("qty").sum().alias("a_short_qty")))
    except Exception as ex:
        log(f"  alt: short_selling not available ({ex})")
    try:
        mt = (C.read("mtf", ["company_id", "disclosed", "qty_financed", "amount_financed_lakh"], variant="ref")
              .group_by("company_id", "disclosed").agg(pl.col("qty_financed").sum().alias("a_mtf_qty"),
                                                       pl.col("amount_financed_lakh").sum().alias("a_mtf_amt"))
              .rename({"disclosed": "date"}))
        mtf_days = mt["date"].unique()
        parts.append(mt)
    except Exception as ex:
        log(f"  alt: mtf not available ({ex})")
    if not parts:
        return None
    out = parts[0]
    for p in parts[1:]:
        out = out.join(p, on=["company_id", "date"], how="full", coalesce=True)
    for col in ("a_traded_qty", "a_deliv_qty", "a_short_qty", "a_mtf_qty", "a_mtf_amt"):
        if col not in out.columns:
            out = out.with_columns(pl.lit(None, dtype=pl.Float64).alias(col))
    out = out.with_columns(pl.col("date").cast(pl.Datetime("ns")).alias("datetime")).drop("date")
    RANGES.clear()
    if short_days is not None and short_days.len():
        RANGES["a_short_qty"] = (short_days.min(), short_days.max())
    if mtf_days is not None and mtf_days.len():
        RANGES["a_mtf_qty"] = RANGES["a_mtf_amt"] = (mtf_days.min(), mtf_days.max())
    return out.select("company_id", "datetime", "a_traded_qty", "a_deliv_qty", "a_short_qty", "a_mtf_qty", "a_mtf_amt")


# Intraday inputs of the parallel ALT-INTRADAY model (config: intraday_features: true; nseml/features.py).
# NSEDATAENGINE's contract dataset intraday_daily (nse_intraday.py, from NSEALGOENGINE's 1-minute archive): the columns
# the contract marks for ML. Left out: id_vol_first_bar (holds the opening auction in 2023-25: "not for models as is"),
# n_bars / id_vol_ratio_nse / px_factor (data checks) and the first_bar_auction flag (the volume shares here already
# leave the 09:15 bar out).
INTRADAY_COLS = [
    "id_ret_first30", "id_ret_mid", "id_ret_last30", "id_close_mark", "id_vwap_dev",
    "id_rv5", "id_bv5", "id_jump_share", "id_down_share", "id_rskew5", "id_rv_first30_share", "id_rv_last60_share",
    "id_hi_time", "id_lo_time",
    "id_vol_first30", "id_vol_last30", "id_vol_max_bar", "id_vol_close_bar", "id_no_trade_share",
    "id_signed_vol", "id_signed_vol_last30",
]


def load_intraday(end=None, log=print) -> pl.DataFrame:
    """company_id, datetime + INTRADAY_COLS for every usable stock-day (ok = true) of intraday_daily.
    Row t describes the whole session of day t. At 15:37 today's row is ok on the session shape alone (NSE's
    official close not published yet; id_close_mark / id_vwap_dev null) and is re-checked after the 19:30 update -
    so the row serves BOTH the 15:37 at-close forecast and the nightly forecast of t+1."""
    C = contract()
    p = C.file_path("intraday_daily", "ref")
    if not p.exists():
        raise SystemExit(f"ALT-INTRADAY needs {p} - in NSEDATAENGINE run:  python nse_intraday.py build")
    d = (C.read("intraday_daily", ["company_id", "date", "ok"] + INTRADAY_COLS, variant="ref", lazy=True)
          .filter(pl.col("ok"))
          .select(pl.col("company_id"), pl.col("date").cast(pl.Datetime("ns")).alias("datetime"), *INTRADAY_COLS))
    if end is not None:
        d = d.filter(pl.col("datetime") <= end)
    d = d.collect()
    dup = d.height - d.unique(["company_id", "datetime"]).height
    if dup:
        log(f"  intraday: {dup} duplicate (stock, day) rows in {p.name} - the last of each kept")
        d = d.unique(["company_id", "datetime"], keep="last", maintain_order=True)
    log(f"  intraday: {d.height:,} usable stock-days, {d['company_id'].n_unique()} stocks, "
        f"{d['datetime'].min():%Y-%m-%d} .. {d['datetime'].max():%Y-%m-%d}")
    return d
