"""Nightly forecasts: after the NSEDATAENGINE 19:30 IST update on day t, the next session's
P10 / P50 / P90 open, high, low, close and volume for every stock in the universe.

    python -m nseml predict              (what the scheduled task runs; see run_predict.bat)
    python -m nseml predict --retrain    force new models now
    python -m nseml predict --force      redo today's file even if it exists

Steps
  1. wait (up to live.wait_minutes) until NSEDATAENGINE has day t - normally today - then rebuild the
     dataset (features_v2 + targets, ~30 s)
  2. models: re-use the newest trained set unless it is older than live.retrain_every_days trading days
     (or --retrain); otherwise train LightGBM (15 models) + deeplob on the last 5 years, early-stopped on the
     most recent 63 days, exactly as one walk-forward fold, and keep them under work\\live\\models\\<date>\\
  3. predict the rows of day t, average lgbm + deeplob (live.model: blend) and convert to rupees
  4. write work\\predictions\\predictions_<t>.parquet + .csv and latest.parquet / latest.csv

Rupee levels (sigma = the stock's EWMA daily volatility at t, C = today's close):
  open_q  = C * exp(g_q * sigma)                       close_q = C * exp(r_q * sigma)
  high_q  = max(open_q, close_q) * exp(u_q * sigma)    volume_q = 20-day mean volume * exp(v_q)
  low_p10 = min(open_p10, close_p10) * exp(-d_p90 * sigma)   (deep end of the low)
  low_p50 = min(open_p50, close_p50) * exp(-d_p50 * sigma)
  low_p90 = min(open_p90, close_p90) * exp(-d_p10 * sigma)   (shallow end)
The P50 bar is always a valid bar. The rupee bands are built from the component quantiles (whose calibration
is measured in the walk-forward), so treat them as approximate ranges, not exact probabilities.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import datetime as dt
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl

from . import baselines as BL
from .config import cfg, contract, dataset_path, work_dir
from .features import FEATURES, TARGETS
from .metrics import MODEL_TARGETS, qcol
from .splits import TRADING_DAYS_PER_YEAR, Fold

KEYS = ["company_id", "datetime"]


def _dirs():
    w = work_dir()
    d = {"models": w / "live" / "models", "pred": w / "predictions", "logs": w / "logs"}
    for p in d.values():
        p.mkdir(parents=True, exist_ok=True)
    return d


def _log(msg):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    with open(_dirs()["logs"] / "predict_log.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---- 1. data -------------------------------------------------------------------------------------------
def latest_raw_day() -> dt.datetime:
    C = contract()
    return C.read("raw_ohlcv", ["datetime"], variant=cfg()["data"]["variant"], lazy=True) \
        .select(pl.col("datetime").max()).collect().item()


def wait_for_data(minutes: int) -> dt.datetime:
    """Wait until NSEDATAENGINE holds today's session (weekdays only), or give up after `minutes`
    and go on with the latest day it has (a holiday, or the update failed - logged)."""
    today = dt.date.today()
    t0 = time.time()
    while True:
        last = latest_raw_day()
        if last.date() >= today or today.weekday() >= 5:
            return last
        if time.time() - t0 > minutes * 60:
            _log(f"NSEDATAENGINE has data up to {last:%Y-%m-%d}, not {today} after {minutes} min "
                 f"(holiday, or the 19:30 update did not run) - forecasting from {last:%Y-%m-%d}")
            return last
        time.sleep(120)


# ---- 2. models -----------------------------------------------------------------------------------------
def live_fold(days_with_target: list) -> Fold:
    """Train / val windows that end at the newest day with a known next-day outcome."""
    wf = cfg()["walk_forward"]
    days = sorted(days_with_target)
    gap, nv = wf["gap_days"], wf["val_days"]
    ntr = int(wf["train_years"] * TRADING_DAYS_PER_YEAR)
    va = (len(days) - nv, len(days) - 1)
    tr_end = va[0] - gap - 1
    tr_start = 0 if wf["mode"] == "expanding" else max(0, tr_end - ntr + 1)
    return Fold(-1, (days[tr_start], days[tr_end]), (days[va[0]], days[va[1]]), (days[-1], days[-1]))


class SavedLGBM:
    """LightGBM quantile boosters saved as text files (one per target x quantile)."""

    def __init__(self, folder: Path, quantiles):
        import lightgbm as lgb
        self.q = quantiles
        self.b = {(t, q): lgb.Booster(model_file=str(folder / f"lgbm_{t}_q{int(q * 100):02d}.txt"))
                  for t in MODEL_TARGETS for q in quantiles}

    def predict(self, test: pl.DataFrame) -> pl.DataFrame:
        X = test.select(FEATURES).to_numpy().astype(np.float32)
        cols = {qcol(t, q): b.predict(X) for (t, q), b in self.b.items()}
        return BL._sorted(test.select(KEYS).with_columns([pl.Series(k, v) for k, v in cols.items()]), self.q)


def newest_models():
    root = _dirs()["models"]
    sets = sorted([p for p in root.iterdir() if (p / "manifest.json").exists()]) if root.exists() else []
    return (sets[-1], json.loads((sets[-1] / "manifest.json").read_text())) if sets else (None, None)


def train_models(ds: pl.DataFrame, panel, fold: Fold, kind: str) -> Path:
    c = cfg()
    q = c["quantiles"]
    folder = _dirs()["models"] / f"{fold.test[1]:%Y%m%d}"
    folder.mkdir(parents=True, exist_ok=True)
    _log(f"training {kind} models: train {fold.train[0]:%Y-%m-%d}..{fold.train[1]:%Y-%m-%d}, "
         f"val {fold.val[0]:%Y-%m-%d}..{fold.val[1]:%Y-%m-%d} -> {folder}")
    between = lambda r: pl.col("datetime").is_between(r[0], r[1])
    if kind in ("lgbm", "blend"):
        tr = ds.filter(between(fold.train), pl.col("y_r").is_not_null())
        va = ds.filter(between(fold.val), pl.col("y_r").is_not_null())
        m = BL.LGBM(c["lgbm"]).fit(tr, va, q, log=_log)
        for (t, qq), mdl in m.models.items():
            mdl.booster_.save_model(str(folder / f"lgbm_{t}_q{int(qq * 100):02d}.txt"), num_iteration=mdl.best_iteration_)
        del tr, va
    if kind in ("deeplob", "blend"):
        from . import deep
        for s in range(int(c["deeplob"].get("ensemble", 1))):
            deep.DeepLOB(c["deeplob"], panel, q, _log).fit(fold, seed=s).save(folder / f"deeplob_s{s}.pt")
    (folder / "manifest.json").write_text(json.dumps({
        "trained_through": f"{fold.test[1]:%Y-%m-%d}", "train": [str(x.date()) for x in fold.train],
        "val": [str(x.date()) for x in fold.val], "kind": kind, "features": FEATURES,
        "created": dt.datetime.now().isoformat(timespec="seconds")}, indent=1))
    # keep the newest few sets only
    sets = sorted(p for p in folder.parent.iterdir() if (p / "manifest.json").exists())
    for old in sets[:-int(c.get("live", {}).get("keep_models", 3))]:
        shutil.rmtree(old, ignore_errors=True)
    return folder


def load_deeplob(folder: Path, panel):
    import torch
    from . import deep
    c = cfg()
    members = []
    for f in sorted(folder.glob("deeplob_s*.pt")):
        ck = torch.load(f, map_location="cpu", weights_only=False)
        m = deep.DeepLOB(ck["params"], panel, ck["quantiles"], _log)
        m.model = deep.build_model(len(ck["features"]), ck["params"]).to(m.device)
        m.model.load_state_dict(ck["state_dict"])
        members.append(m)
    if not members:
        raise SystemExit(f"no deeplob models in {folder}")
    return members


# ---- 3. rupees -----------------------------------------------------------------------------------------
def to_rupees(pred: pl.DataFrame, rows: pl.DataFrame) -> pl.DataFrame:
    d = rows.join(pred, on=KEYS, how="inner")
    s, C, V = pl.col("sigma"), pl.col("close"), pl.col("vol_sma20")
    q = lambda t, p: pl.col(qcol(t, p))
    out = d.with_columns(
        **{f"open_p{p}": C * (q("y_g_n", p / 100) * s).exp() for p in (10, 50, 90)},
        **{f"close_p{p}": C * (q("y_r_n", p / 100) * s).exp() for p in (10, 50, 90)},
        **{f"volume_p{p}": V * q("y_v", p / 100).exp() for p in (10, 50, 90)},
    ).with_columns(
        **{f"high_p{p}": pl.max_horizontal(f"open_p{p}", f"close_p{p}") * (q("y_u_n", p / 100) * s).exp()
           for p in (10, 50, 90)},
        low_p10=pl.min_horizontal("open_p10", "close_p10") * (-q("y_d_n", 0.9) * s).exp(),
        low_p50=pl.min_horizontal("open_p50", "close_p50") * (-q("y_d_n", 0.5) * s).exp(),
        low_p90=pl.min_horizontal("open_p90", "close_p90") * (-q("y_d_n", 0.1) * s).exp(),
        exp_return_pct=(q("y_r_n", 0.5) * s) * 100, exp_gap_pct=(q("y_g_n", 0.5) * s) * 100,
        exp_range_pct=((q("y_u_n", 0.5) + q("y_d_n", 0.5)) * s) * 100,
    ).with_columns(
        rank_return=(pl.col("exp_return_pct").rank("average") / pl.len()).over("datetime"),
    )
    price = [f"{k}_p{p}" for k in ("open", "high", "low", "close") for p in (10, 50, 90)]
    return out.select(
        pl.col("company_id"), pl.col("datetime").dt.date().alias("date"), pl.col("close").alias("close_today"),
        *[pl.col(c).round(2) for c in price], *[pl.col(f"volume_p{p}").round(0) for p in (10, 50, 90)],
        pl.col("exp_return_pct").round(3), pl.col("exp_gap_pct").round(3), pl.col("exp_range_pct").round(3),
        pl.col("rank_return").round(4), pl.col("is_fno"), (pl.col("sigma") * 100).round(3).alias("daily_vol_pct"),
        pl.col("log_med_tv60").exp().round(0).alias("median_turnover_60d"),
    ).sort("rank_return", descending=True)


# ---- the command ----------------------------------------------------------------------------------------
def run(retrain: bool = False, force: bool = False, wait_minutes: int | None = None, rebuild: bool = True):
    c = cfg()
    lc = c.get("live", {})
    kind = lc.get("model", "blend")
    t0 = time.time()
    last = wait_for_data(int(lc.get("wait_minutes", 90) if wait_minutes is None else wait_minutes))
    out = _dirs()["pred"] / f"predictions_{last:%Y%m%d}.parquet"
    if out.exists() and not force:
        _log(f"predictions for {last:%Y-%m-%d} already exist ({out.name}) - nothing to do (--force to redo)")
        return out
    if rebuild:
        from .config import wf_lock, wf_running
        if wf_running():
            _log(f"walk-forward running in this work folder ({wf_lock()}) - tonight's forecast is skipped so its "
                 "dataset is not rewritten underneath it (it runs again tomorrow; --no-build forecasts from the old dataset)")
            return None
        from .features import build
        build(log=_log)
    cols = list(dict.fromkeys(KEYS + ["sigma", "close", "vol_sma20", "is_fno", "log_med_tv60"] + FEATURES + TARGETS
                              + ["y_g_n", "y_r_n", "y_u_n", "y_d_n"]))
    ds = pl.read_parquet(dataset_path(), columns=cols)
    t = ds["datetime"].max()
    if t < last:
        _log(f"WARNING: dataset ends {t:%Y-%m-%d} but NSEDATAENGINE has {last:%Y-%m-%d} - run python -m nseml build")
    days_tgt = ds.filter(pl.col("y_r").is_not_null())["datetime"].unique().to_list()
    folder, man = newest_models()
    age = None
    if man:
        trained = dt.datetime.fromisoformat(man["trained_through"])
        age = ds.filter(pl.col("datetime") > trained)["datetime"].n_unique()
    due = (retrain or man is None or man.get("kind") != kind or man.get("features") != FEATURES
           or age >= int(lc.get("retrain_every_days", 21)))
    panel = None
    if kind in ("deeplob", "blend"):
        from . import deep
        panel = deep.Panel(ds.select(list(dict.fromkeys(KEYS + FEATURES + ["y_g_n", "y_r_n", "y_u_n", "y_d_n", "y_v"]))))
    if due:
        why = "forced" if retrain else ("none yet" if man is None else f"{age} trading days old")
        _log(f"models due ({why})")
        folder = train_models(ds, panel, live_fold(days_tgt), kind)
        man = json.loads((folder / "manifest.json").read_text())
    else:
        _log(f"using models {folder.name} (trained through {man['trained_through']}, {age} trading days ago)")
    today = ds.filter(pl.col("datetime") == t)
    q = c["quantiles"]
    preds = []
    if kind in ("lgbm", "blend"):
        preds.append(SavedLGBM(folder, q).predict(today))
    if kind in ("deeplob", "blend"):
        members = load_deeplob(folder, panel)
        preds.append(members[0].predict(Fold(-1, (t, t), (t, t), (t, t)), members))
    pred = preds[0]
    if len(preds) == 2:
        qc = [x for x in preds[0].columns if "_q" in x]
        pred = (preds[0].join(preds[1], on=KEYS, suffix="_b")
                  .select(KEYS + [((pl.col(x) + pl.col(x + "_b")) / 2).alias(x) for x in qc]))
    res = to_rupees(pred, today).with_columns(model=pl.lit(kind), models=pl.lit(folder.name))
    res.write_parquet(out)
    res.write_csv(out.with_suffix(".csv"))
    shutil.copyfile(out, out.parent / "latest.parquet")
    shutil.copyfile(out.with_suffix(".csv"), out.parent / "latest.csv")
    _log(f"predictions for the session after {t:%Y-%m-%d}: {res.height:,} stocks, model {kind} ({folder.name}) "
         f"-> {out} ({time.time() - t0:.0f}s)")
    try:
        close_check()
    except Exception as ex:                      # the check must never break the nightly forecast
        _log(f"close-check failed: {ex!r}")
    return out


# =========================================================================================================
# AT-CLOSE FORECAST (~15:37): the same models, today's bar from NSEDATAENGINE's 15:30 snapshot (nse_close_snapshot.py)
# =========================================================================================================
def _at_close_dir() -> Path:
    C = contract()
    try:
        return C.location("at_close")
    except KeyError:
        return C.location("master") / "at_close"


def _predict_frame(folder: Path, kind: str, frame: pl.DataFrame, t) -> pl.DataFrame:
    """P10/P50/P90 for the rows of day t in `frame` (which also holds the history the deeplob window needs)."""
    q = cfg()["quantiles"]
    today = frame.filter(pl.col("datetime") == t)
    preds = []
    if kind in ("lgbm", "blend"):
        preds.append(SavedLGBM(folder, q).predict(today))
    if kind in ("deeplob", "blend"):
        from . import deep
        panel = deep.Panel(frame.select(list(dict.fromkeys(KEYS + FEATURES + ["y_g_n", "y_r_n", "y_u_n", "y_d_n", "y_v"]))))
        members = load_deeplob(folder, panel)
        preds.append(members[0].predict(Fold(-1, (t, t), (t, t), (t, t)), members))
    pred = preds[0]
    if len(preds) == 2:
        qc = [x for x in preds[0].columns if "_q" in x]
        pred = (preds[0].join(preds[1], on=KEYS, suffix="_b")
                  .select(KEYS + [((pl.col(x) + pl.col(x + "_b")) / 2).alias(x) for x in qc]))
    return pred


def run_at_close(force: bool = False, wait_minutes: int = 10, day: dt.date | None = None, kind: str | None = None):
    """Forecast the next session from today's 15:30 snapshot. Writes predictions_close_<t>.parquet/.csv and
    latest_close.csv; never trains (uses the newest nightly models)."""
    from .features import build
    t0 = time.time()
    day = day or dt.date.today()
    t = dt.datetime.combine(day, dt.time())
    out = _dirs()["pred"] / f"predictions_close_{day:%Y%m%d}.parquet"
    if out.exists() and not force:
        _log(f"at-close: {out.name} already exists (--force to redo)")
        return out
    snap = _at_close_dir() / f"{day:%Y-%m-%d}.parquet"
    end_wait = time.time() + wait_minutes * 60
    while not snap.exists() and time.time() < end_wait:
        time.sleep(15)
    if not snap.exists():
        _log(f"at-close: no snapshot {snap} after {wait_minutes} min (holiday, PC asleep at 15:00, or Fyers failed) - "
             f"no at-close forecast today")
        return None
    if latest_raw_day() >= t:
        _log("at-close: NSEDATAENGINE already has today's session - use the normal forecast")
    s = pl.read_parquet(snap)
    st = s.filter(pl.col("kind") == "stock", pl.col("close") > 0, pl.col("open") > 0, pl.col("volume") > 0)
    # corporate action today (ex-date): NSE's previous close differs from our history -> leave the stock out
    hist = contract().read("raw_ohlcv", ["company_id", "datetime", "close"], variant=cfg()["data"]["variant"], lazy=True) \
        .filter(pl.col("datetime") >= t - dt.timedelta(days=15), pl.col("datetime") < t).collect() \
        .sort("datetime").group_by("company_id").agg(pl.col("close").last().alias("hist_close"))
    st = st.join(hist, on="company_id", how="inner")
    bad = st.filter(((pl.col("prev_close") / pl.col("hist_close") - 1).abs() > 0.03))
    if bad.height:
        _log(f"at-close: {bad.height} stock(s) left out - previous close differs from history by > 3% "
             f"(split / bonus today?): {', '.join(bad['company_id'].head(10).to_list())}")
    st = st.join(bad.select("company_id"), on="company_id", how="anti")
    bars = st.select("company_id", pl.lit(t).cast(pl.Datetime("ns")).alias("datetime"), "open", "high", "low", "close",
                     "volume")
    idx = {r["company_id"]: r["close"] for r in s.filter(pl.col("kind") == "index").iter_rows(named=True)}
    mrow = {"datetime": t, "nifty_close": idx.get("NIFTY 50"), "vix": idx.get("INDIA VIX"),
            "nikkei225": idx.get("NIKKEI 225")}
    missing = [k for k, v in mrow.items() if v is None]
    if missing:
        _log(f"at-close: {missing} missing in the snapshot - the previous close is repeated for them")
    _log(f"at-close: {bars.height} stocks from {snap.name} "
         f"({st.filter(pl.col('close_src') == 'vwap30').height} closes = 15:00-15:30 VWAP)")
    frame = build(companies=bars["company_id"].to_list(), start=(day - dt.timedelta(days=200)).isoformat(),
                  out=False, log=lambda m: _log("at-close:" + m) if "nowcast" in m or "alt:" in m else None,
                  extra_raw=bars, extra_market=mrow, live_close=True)
    if frame.filter(pl.col("datetime") == t).is_empty():
        _log("at-close: no rows for today after the feature build - nothing written")
        return None
    folder, man = newest_models()
    if folder is None:
        _log("at-close: no trained models yet - run the nightly forecast first")
        return None
    want = cfg().get("live", {}).get("model", "blend")
    kind = kind or want
    if man.get("features") != FEATURES or man.get("kind") != want:
        _log("at-close: newest models do not match the current features / model kind - waiting for the nightly retrain")
        return None
    pred = _predict_frame(folder, kind, frame, t)
    res = to_rupees(pred, frame.filter(pl.col("datetime") == t)).with_columns(
        model=pl.lit(kind), models=pl.lit(folder.name), made_at=pl.lit(dt.datetime.now().strftime("%H:%M:%S")))
    res.write_parquet(out)
    res.write_csv(out.with_suffix(".csv"))
    shutil.copyfile(out.with_suffix(".csv"), out.parent / "latest_close.csv")
    _log(f"at-close forecast for the session after {day}: {res.height:,} stocks, models {folder.name} -> {out} "
         f"({time.time() - t0:.0f}s)")
    return out


# ---- nightly check: at-close forecast vs the 20:00 forecast, snapshot close vs NSE's close, paper overnight trade --
DELIVERY_BPS = 27.0      # Shoonya delivery round trip ~23 bps (STT 0.1% x 2, stamp, DP) + ~4 bps slippage


def close_check(log=None) -> Path | None:
    log = log or _log
    pdir = _dirs()["pred"]
    rows_f = pdir / "close_check.csv"
    paper_f = pdir / "close_paper.csv"
    done = set(pl.read_csv(rows_f)["day"].cast(pl.String).to_list()) if rows_f.exists() else set()
    done_p = set(pl.read_csv(paper_f)["day"].cast(pl.String).to_list()) if paper_f.exists() else set()
    C, var = contract(), cfg()["data"]["variant"]
    new, newp = [], []
    for f in sorted(pdir.glob("predictions_close_????????.parquet")):
        ds = f.stem[-8:]
        day = dt.datetime.strptime(ds, "%Y%m%d")
        a = pl.read_parquet(f)
        night = pdir / f"predictions_{ds}.parquet"
        if ds not in done and night.exists():
            b = pl.read_parquet(night)
            j = a.join(b, on="company_id", suffix="_n")
            oc = lambda df, s="": df.with_columns((pl.col("exp_return_pct" + s) - pl.col("exp_gap_pct" + s)).alias("oc" + s))
            j = oc(oc(j), "_n")
            top = lambda col, desc: set(j.sort(col, descending=desc).head(15)["company_id"])
            snap = _at_close_dir() / f"{day:%Y-%m-%d}.parquet"
            err_v = err_l = None
            if snap.exists():
                sp = pl.read_parquet(snap).filter(pl.col("kind") == "stock").join(
                    b.select("company_id", pl.col("close_today").alias("nse_close")), on="company_id")
                e = lambda c: sp.filter(pl.col(c).is_not_null()).select(
                    ((pl.col(c) / pl.col("nse_close") - 1).abs() * 1e4).median()).item()
                err_v, err_l = e("vwap30"), e("ltp")
            new.append({"day": ds, "stocks": j.height,
                        "corr_gap": j.select(pl.corr("exp_gap_pct", "exp_gap_pct_n")).item(),
                        "corr_oc": j.select(pl.corr("oc", "oc_n")).item(),
                        "top15_gap_up_same": len(top("exp_gap_pct", True) & top("exp_gap_pct_n", True)),
                        "top15_oc_short_same": len(top("oc", False) & top("oc_n", False)),
                        "close_err_bps_median": j.select(((pl.col("close_today") / pl.col("close_today_n") - 1).abs()
                                                          * 1e4).median()).item(),
                        "vwap30_err_bps_median": err_v, "ltp_err_bps_median": err_l})
        # paper overnight trade: buy the top N predicted gap-ups at NSE's close, sell at the next open
        if ds in done_p:
            continue
        nxt = C.read("raw_ohlcv", ["company_id", "datetime", "open", "close"], variant=var, lazy=True) \
            .filter(pl.col("datetime") >= day, pl.col("datetime") <= day + dt.timedelta(days=7)).collect()
        days = sorted(nxt["datetime"].unique().to_list())
        if len(days) < 2 or days[0] != day:
            continue
        px = nxt.filter(pl.col("datetime") == day).select("company_id", pl.col("close").alias("c0")).join(
            nxt.filter(pl.col("datetime") == days[1]).select("company_id", pl.col("open").alias("o1")), on="company_id")
        liq = a.filter((pl.col("median_turnover_60d") >= 25e7) & (pl.col("close_today") >= 50))
        r = liq.join(px, on="company_id").sort("exp_gap_pct", descending=True)
        for n in (1, 5, 15):
            x = r.head(n).with_columns(((pl.col("o1") / pl.col("c0") - 1) * 1e4 - DELIVERY_BPS).alias("net"))
            newp.append({"day": ds, "next_day": f"{days[1]:%Y%m%d}", "top_n": n, "stocks": " ".join(x["company_id"]),
                         "pred_gap_bps": round(x["exp_gap_pct"].mean() * 100, 1),
                         "actual_gap_bps": round(((x["o1"] / x["c0"] - 1) * 1e4).mean(), 1),
                         "net_bps": round(x["net"].mean(), 1), "net_rs_per_lakh_each": round(x["net"].sum() * 10, 0)})
    for rows, fp in ((new, rows_f), (newp, paper_f)):
        if rows:
            df = pl.DataFrame(rows)
            if fp.exists():
                df = pl.concat([pl.read_csv(fp, schema_overrides={"day": pl.String, "next_day": pl.String}
                                            if fp == paper_f else {"day": pl.String}), df], how="diagonal_relaxed")
            df.write_csv(fp)
    if not rows_f.exists() and not paper_f.exists():
        return None
    lines = ["At-close (15:37) forecast vs the nightly (20:00) forecast, and the paper overnight trade", ""]
    if rows_f.exists():
        c = pl.read_csv(rows_f)
        lines.append(f"{c.height} day(s). Averages: correlation of the predicted gap {c['corr_gap'].mean():.3f}, of the "
                     f"predicted open->close {c['corr_oc'].mean():.3f}; same stocks in the top-15 gap-up list "
                     f"{c['top15_gap_up_same'].mean():.1f}/15, in the top-15 short list {c['top15_oc_short_same'].mean():.1f}/15;")
        lines.append(f"  snapshot close vs NSE's close (median |error| bps): used {c['close_err_bps_median'].mean():.1f}, "
                     f"15:00-15:30 VWAP {c['vwap30_err_bps_median'].mean() if c['vwap30_err_bps_median'].null_count() < c.height else float('nan'):.1f}, "
                     f"last price {c['ltp_err_bps_median'].mean() if c['ltp_err_bps_median'].null_count() < c.height else float('nan'):.1f}")
        lines.append("")
    if paper_f.exists():
        p = pl.read_csv(paper_f)
        lines.append(f"Paper overnight trade (buy at NSE's close, sell at the next open, net of {DELIVERY_BPS:.0f} bps):")
        for n, g in p.group_by("top_n", maintain_order=True):
            lines.append(f"  top {n[0]:2d}: {g.height} days, average {g['net_bps'].mean():+.1f} bps, "
                         f"winning days {100 * (g['net_bps'] > 0).mean():.0f}%, total Rs {g['net_rs_per_lakh_each'].sum():+,.0f} "
                         f"(Rs 1 lakh per stock)")
    rep = pdir / "close_check.txt"
    rep.write_text("\n".join(lines), encoding="utf-8")
    log(f"close-check -> {rep}")
    return rep
