"""
Delivery-% NOWCAST - an estimate of TODAY's delivery % at 15:37, before NSE publishes it (~18:00).

Delivery % = shares delivered / shares traded. It only exists after NSE nets every client's trades, so a forecast made
at 15:37 (in time for the 15:40-16:00 post-close session) cannot see today's value. But most of it is predictable:
  * it is very persistent per stock (yesterday's value and the 20-day average explain most of it);
  * on days with unusual volume / range intraday traders crowd in and the % drops; "implied" % = the stock's usual
    delivered quantity / today's volume captures that;
  * trade-for-trade stocks (BE / BZ series) deliver ~100%.
This module learns today's delivery % from what is known at 15:37 (today's bar from the 15:30 snapshot + delivery
history up to yesterday) with LightGBM, and hands the ALT-CLOSE model `a_dp_now`.

No look-ahead / no over-trust: the history the main model trains on gets OUT-OF-SAMPLE nowcasts - each calendar year
predicted by a nowcast model fitted only on earlier years (walk-forward), so the main model learns how good (or bad)
the nowcast really is. New days are nowcast by the live model (refitted every RETRAIN_DAYS sessions, never at 15:37).

Files (in the model's work folder): delivery_nowcast.parquet (company_id, datetime, dp_now, src = oos / live),
live\\nowcast_model.txt + .json, delivery_nowcast_report.txt (accuracy vs the naive guesses, by year).
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import datetime as dt
import json

import numpy as np
import polars as pl

from .config import contract, work_dir

NOW_FEATURES = ["dp_l1", "dp_m5", "dp_m20", "dp_sd20", "dp_max20", "impl_dp", "vol_ratio", "vol_ratio5", "ret",
                "absret", "rng", "clv", "gap", "t2t_l1", "ats_l1", "dow"]
PARAMS = dict(objective="regression", n_estimators=400, learning_rate=0.05, num_leaves=63, min_child_samples=200,
              subsample=0.8, subsample_freq=1, colsample_bytree=0.9, verbose=-1)
MAX_TRAIN_ROWS = 1_500_000
RETRAIN_DAYS = 21
KEY = ["company_id", "datetime"]


def _paths():
    w = work_dir()
    (w / "live").mkdir(parents=True, exist_ok=True)
    return (w / "delivery_nowcast.parquet", w / "live" / "nowcast_model.txt", w / "live" / "nowcast_model.json",
            w / "delivery_nowcast_report.txt")


def load_delivery() -> pl.DataFrame:
    d = contract().read("delivery", ["company_id", "date", "series", "traded_qty", "deliv_qty", "trades"], variant="ref")
    return (d.group_by("company_id", "date")
             .agg(pl.col("traded_qty").sum().alias("n_traded"), pl.col("deliv_qty").sum().alias("n_deliv"),
                  pl.col("trades").sum().alias("n_trades"),
                  pl.col("series").is_in(["BE", "BZ"]).any().cast(pl.Float32).alias("n_t2t"))
             .with_columns(pl.col("date").cast(pl.Datetime("ns")).alias("datetime")).drop("date"))


def frame(raw: pl.DataFrame, deliv: pl.DataFrame) -> pl.DataFrame:
    """Nowcast inputs on row t: today's bar (known at 15:30) + delivery history up to t-1. dp_target = actual day-t %."""
    g = lambda e: e.over("company_id")
    o, h, l, c = (pl.col(x) for x in ("open", "high", "low", "close"))
    v = pl.col("volume").cast(pl.Float64)
    d = (raw.select("company_id", "datetime", "open", "high", "low", "close", "volume")
            .join(deliv, on=KEY, how="left").sort(KEY))
    d = d.with_columns(pl.when(pl.col("n_traded") > 0)
                       .then((pl.col("n_deliv") / pl.col("n_traded")).clip(0, 1)).alias("dp_target"))
    t1 = pl.col("dp_target").shift(1)
    d = d.with_columns(
        dp_l1=g(t1), dp_m5=g(t1.rolling_mean(5, min_samples=3)), dp_m20=g(t1.rolling_mean(20, min_samples=10)),
        dp_sd20=g(t1.rolling_std(20, min_samples=10)), dp_max20=g(t1.rolling_max(20, min_samples=10)),
        dq_m20=g(pl.col("n_deliv").shift(1).rolling_mean(20, min_samples=10)),
        v_m20=g(v.shift(1).rolling_mean(20, min_samples=10)), v_m5=g(v.shift(1).rolling_mean(5, min_samples=3)),
        c1=g(c.shift(1)), t2t_l1=g(pl.col("n_t2t").shift(1)),
        ats_l1=g(pl.when(pl.col("n_trades") > 0).then(pl.col("n_traded") / pl.col("n_trades")).shift(1)),
    )
    hl = h - l
    d = d.with_columns(
        impl_dp=pl.when(v > 0).then((pl.col("dq_m20") / v).clip(0, 2)),
        vol_ratio=pl.when((v > 0) & (pl.col("v_m20") > 0)).then((v / pl.col("v_m20")).log()),
        vol_ratio5=pl.when((v > 0) & (pl.col("v_m5") > 0)).then((v / pl.col("v_m5")).log()),
        ret=(c / pl.col("c1")).log(), absret=(c / pl.col("c1")).log().abs(), rng=(h / l).log(),
        clv=pl.when(hl > 0).then((c - l) / hl - 0.5).otherwise(0.0), gap=(o / pl.col("c1")).log(),
        ats_l1=pl.when(pl.col("ats_l1") > 0).then(pl.col("ats_l1").log()),
        dow=pl.col("datetime").dt.weekday().cast(pl.Float32),
    )
    d = d.with_columns([pl.when(pl.col(x).is_finite()).then(pl.col(x)).cast(pl.Float32).alias(x) for x in NOW_FEATURES])
    return d.select(KEY + ["dp_target"] + NOW_FEATURES)


def _x(f: pl.DataFrame) -> np.ndarray:
    return f.select(NOW_FEATURES).to_numpy().astype(np.float32)


def _fit(rows: pl.DataFrame):
    import lightgbm as lgb
    if rows.height > MAX_TRAIN_ROWS:
        rows = rows.sample(MAX_TRAIN_ROWS, seed=7)
    prm = {k: v for k, v in PARAMS.items() if k != "n_estimators"}
    prm.update(bagging_fraction=prm.pop("subsample"), bagging_freq=prm.pop("subsample_freq"),
               feature_fraction=prm.pop("colsample_bytree"), min_data_in_leaf=prm.pop("min_child_samples"))
    return lgb.train(prm, lgb.Dataset(_x(rows), label=rows["dp_target"].to_numpy()), num_boost_round=PARAMS["n_estimators"])


def _trainable(f: pl.DataFrame) -> pl.DataFrame:
    return f.filter(pl.col("dp_target").is_not_null() & pl.col("dp_m20").is_not_null())


def walk_forward(f: pl.DataFrame, log=print) -> pl.DataFrame:
    """Out-of-sample nowcasts: year Y predicted by a model fitted on everything before 1 Jan Y (first year: none)."""
    rows = _trainable(f)
    out = []
    for y in sorted(rows["datetime"].dt.year().unique().to_list()):
        a, b = dt.datetime(y, 1, 1), dt.datetime(y + 1, 1, 1)
        tr = rows.filter(pl.col("datetime") < a)
        if tr.height < 50_000:
            continue
        te = f.filter((pl.col("datetime") >= a) & (pl.col("datetime") < b) & pl.col("dp_m20").is_not_null())
        p = _fit(tr).predict(_x(te))
        out.append(te.select(KEY).with_columns(pl.Series("dp_now", np.clip(p, 0, 1)).cast(pl.Float32),
                                               pl.lit("oos").alias("src")))
        log(f"  nowcast: {y} predicted by a model fitted on {tr.height:,} earlier stock-days")
    return pl.concat(out) if out else pl.DataFrame(schema={"company_id": pl.String, "datetime": pl.Datetime("ns"),
                                                           "dp_now": pl.Float32, "src": pl.String})


def report(f: pl.DataFrame, cache: pl.DataFrame, path, log=print) -> None:
    r = (cache.filter(pl.col("src") == "oos").join(f.select(KEY + ["dp_target", "dp_l1", "dp_m20"]), on=KEY)
         .drop_nulls(["dp_target", "dp_l1", "dp_m20", "dp_now"]))
    if r.is_empty():
        return
    t = pl.col("dp_target")
    def stats(df):
        tm = df["dp_target"].mean()
        sst = ((df["dp_target"] - tm) ** 2).sum()
        o = {}
        for name, col in (("nowcast", "dp_now"), ("yesterday", "dp_l1"), ("20-day avg", "dp_m20")):
            e = df["dp_target"] - df[col]
            o[name] = (e.abs().mean() * 100, 1 - (e ** 2).sum() / sst)
        s = df.select(pl.corr(t - pl.col("dp_m20"), pl.col("dp_now") - pl.col("dp_m20")).alias("a"),
                      pl.corr(t - pl.col("dp_m20"), pl.col("dp_l1") - pl.col("dp_m20")).alias("b"))
        return o, s["a"][0], s["b"][0]
    lines = ["Delivery-% nowcast at 15:37 vs what NSE publishes at ~18:00 (out-of-sample: each year predicted by a model",
             "fitted on earlier years only). MAE in percentage points; R2 = share of the day-to-day variation explained.",
             "'surprise corr' = how well a guess catches today's deviation from the stock's 20-day average (the part",
             "that could carry news); the 20-day average itself has 0 by construction.", ""]
    o, sa, sb = stats(r)
    lines.append(f"all {r.height:,} stock-days:")
    for k, (mae, r2) in o.items():
        lines.append(f"  {k:11s} MAE {mae:5.2f} pp   R2 {r2:.3f}")
    lines.append(f"  surprise corr: nowcast {sa:.3f}, yesterday's value {sb:.3f}")
    lines += ["", "by year:   nowcast MAE / R2 | yesterday MAE / R2 | surprise corr nowcast / yesterday"]
    for y, g in r.group_by(pl.col("datetime").dt.year().alias("y"), maintain_order=True):
        o, sa, sb = stats(g)
        lines.append(f"  {y[0]}   {o['nowcast'][0]:5.2f} / {o['nowcast'][1]:.3f}  |  {o['yesterday'][0]:5.2f} / "
                     f"{o['yesterday'][1]:.3f}  |  {sa:.3f} / {sb:.3f}")
    text = "\n".join(lines)
    path.write_text(text, encoding="utf-8")
    for ln in lines[5:9]:
        log("  nowcast " + ln.strip())


def nowcast_for(raw: pl.DataFrame, log=print, allow_train: bool = True, last_official=None) -> pl.DataFrame | None:
    """(company_id, datetime, a_dp_now) for every row of `raw` that has 20 days of delivery history.
    allow_train=False (15:37): only the existing live model, nothing written."""
    cache_p, model_p, meta_p, rep_p = _paths()
    try:
        deliv = load_delivery()
    except Exception as ex:
        log(f"  nowcast: no delivery data ({ex})")
        return None
    f = frame(raw, deliv)
    cache = pl.read_parquet(cache_p) if cache_p.exists() else None
    if cache is None:
        if not allow_train:
            log("  nowcast: no history yet - run the nightly build first; today's nowcast left empty")
            cache = walk_forward(f.head(0), log)
        else:
            log("  nowcast: first run - out-of-sample history, one model per year (a few minutes per year)")
            cache = walk_forward(f, log)
            cache.write_parquet(cache_p)
            report(f, cache, rep_p, log)
    booster, meta = None, {}
    if model_p.exists() and meta_p.exists():
        import lightgbm as lgb
        meta = json.loads(meta_p.read_text())
        booster = lgb.Booster(model_file=str(model_p)) if meta.get("features") == NOW_FEATURES else None
    days = f.filter(pl.col("dp_target").is_not_null())["datetime"].unique().sort()
    if allow_train and days.len() > 1:
        # fitted up to the day BEFORE the newest published one, so even the newest day gets an out-of-sample nowcast
        train_to = days[-2]
        age = (days > dt.datetime.fromisoformat(meta["trained_through"])).sum() if meta.get("trained_through") else None
        if booster is None or age is None or age > RETRAIN_DAYS:
            log(f"  nowcast: fitting the live model on delivery history to {train_to:%Y-%m-%d}")
            booster = _fit(_trainable(f).filter(pl.col("datetime") <= train_to))
            booster.save_model(str(model_p))
            meta = {"trained_through": f"{train_to:%Y-%m-%d}", "features": NOW_FEATURES}
            meta_p.write_text(json.dumps(meta))
    # the live model only ever nowcasts days AFTER its training data (no in-sample values in the history)
    after = dt.datetime.fromisoformat(meta["trained_through"]) if meta.get("trained_through") else None
    todo = f.filter(pl.col("dp_m20").is_not_null() & (pl.col("datetime") > after if after else pl.lit(False)))
    todo = todo.join(cache.select(KEY), on=KEY, how="anti")
    if booster is not None and todo.height:
        add = todo.select(KEY).with_columns(pl.Series("dp_now", np.clip(booster.predict(_x(todo)), 0, 1)).cast(pl.Float32),
                                            pl.lit("live").alias("src"))
        if allow_train and last_official is not None:
            keep = add.filter(pl.col("datetime") <= last_official)
            if keep.height:
                pl.concat([cache, keep]).unique(subset=KEY, keep="first").sort(KEY).write_parquet(cache_p)
        cache = pl.concat([cache, add]).unique(subset=KEY, keep="first")
    elif booster is None:
        log("  nowcast: no live model yet - newest days left empty")
    return cache.select(KEY + [pl.col("dp_now").alias("a_dp_now")])
