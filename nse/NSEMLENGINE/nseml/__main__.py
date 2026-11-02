"""NSEMLENGINE command line.

  python -m nseml paths                          where every folder / input file resolves
  python -m nseml build                          features_v2 + targets -> work\\dataset_v2.parquet
  python -m nseml folds                          list the walk-forward folds
  python -m nseml baselines [--models naive,ewma,lgbm,deeplob] [--last 8] [--name NAME]
                                                 walk-forward run; resumable (finished folds are skipped,
                                                 --fresh re-runs them)
  python -m nseml report --name NAME             summary table of a run (also written to summary.md)
  python -m nseml rescore --name NAME            recompute metrics from saved predictions
  python -m nseml predict [--retrain] [--force]  nightly forecasts for the next session (see nseml/live.py)
  python -m nseml predict-close [--force]        ~15:37 forecast from today's 15:30 snapshot (same models)
  python -m nseml close-check                    at-close vs nightly forecast + paper overnight trade report
  python -m nseml link-map [--redo]              ALT-LINK model: show its "system map" (--redo: draw it again)
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import os as _os
import sys as _sys

# --config must take effect BEFORE the feature list is built (features.FEATURES depends on extra_features)
for _i, _a in enumerate(_sys.argv):
    if _a == "--config" and _i + 1 < len(_sys.argv):
        _os.environ["NSEMLENGINE_CONFIG"] = _os.path.abspath(_sys.argv[_i + 1])
    elif _a.startswith("--config="):
        _os.environ["NSEMLENGINE_CONFIG"] = _os.path.abspath(_a.split("=", 1)[1])

import argparse
import datetime as dt
import json
import sys
import time

import polars as pl

from . import baselines as BL
from .config import cfg, contract, data_engine_home, dataset_path, home, results_dir, work_dir
from .features import FEATURES, TARGETS
from .metrics import evaluate
from .splits import make_folds


def log(msg, path=None):
    line = f"{dt.datetime.now():%H:%M:%S}  {msg}"
    print(line, flush=True)
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def cmd_paths(a):
    print(f"NSEMLENGINE_HOME      {home()}")
    print(f"NSEMLENGINE_WORK_DIR  {work_dir()}")
    print(f"NSEDATAENGINE_HOME    {data_engine_home()}  (contract {contract().version()})")
    for ds, v in (("raw_ohlcv", cfg()["data"]["variant"]), ("market", cfg()["data"]["variant"]), ("fno_universe", "ref")):
        p = contract().file_path(ds, v)
        print(f"  {ds:14s} {p}  {'ok' if p.exists() else 'MISSING'}")
    from .features import EXTRA, INTRADAY
    missing = []
    for ds in (("delivery", "short_selling", "mtf") if EXTRA else ()):      # the ALT models' extra inputs
        p = contract().file_path(ds, "ref")
        print(f"  {ds:14s} {p}  {'ok' if p.exists() else 'MISSING'}")
        if not p.exists():
            missing.append(ds)
    if INTRADAY:                                                            # the ALT-INTRADAY model's input
        p = contract().file_path("intraday_daily", "ref")
        print(f"  {'intraday_daily':14s} {p}  {'ok' if p.exists() else 'MISSING'}")
        if not p.exists():
            sys.exit("missing input: intraday_daily - in NSEDATAENGINE run:  python nse_intraday.py build")
    print(f"  dataset        {dataset_path()}  {'ok' if dataset_path().exists() else 'not built'}")
    if missing:
        sys.exit(f"missing inputs: {', '.join(missing)} - in NSEDATAENGINE run:  python nse_alt_data.py backfill "
                 f"--start 2019-01-01 --only {','.join(missing)}")


def cmd_build(a):
    from .features import build
    build()


def _days():
    return pl.scan_parquet(dataset_path()).select(pl.col("datetime").unique()).collect()["datetime"].sort().to_list()


def cmd_folds(a):
    for f in make_folds(_days()):
        print(f"{f.label()}  train {f.train[0]:%Y-%m-%d}..{f.train[1]:%Y-%m-%d}  val {f.val[0]:%Y-%m-%d}..{f.val[1]:%Y-%m-%d}"
              f"  test {f.test[0]:%Y-%m-%d}..{f.test[1]:%Y-%m-%d}")


def cmd_baselines(a):
    """Walk-forward. While it runs, a lock file in the work folder stops the nightly `predict` of the same model from
    rebuilding dataset_v2.parquet underneath it (that broke a run on 21 Oct 2026: 'Invalid thrift: bad data')."""
    from .config import wf_lock
    lock = wf_lock()
    lock.write_text(f"walk-forward {a.name or ''} started {dt.datetime.now():%Y-%m-%d %H:%M:%S}\n", encoding="utf-8")
    try:
        return _baselines(a, lock)
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _baselines(a, lock):
    c = cfg()
    name = a.name or f"baselines_{dt.datetime.now():%Y%m%d}"
    out = results_dir(name)
    lp = out / "log.txt"
    models = [m.strip() for m in a.models.split(",")]
    folds = make_folds(_days())
    if a.last:
        folds = folds[-a.last:]
    (out / "config.json").write_text(json.dumps({"config": c, "models": models, "features": FEATURES,
                                                 "folds": [f.label() for f in folds]}, indent=2, default=str))
    log(f"run {name}: models {models}, {len(folds)} folds, {len(FEATURES)} features -> {out}", lp)
    cols = ["company_id", "datetime", "sigma", "close", "o_next", "h_next", "l_next", "c_next", "v_next"] + \
        FEATURES + TARGETS + [t for t in ("y_g_n", "y_r_n", "y_u_n", "y_d_n")]
    lf = pl.scan_parquet(dataset_path()).select(list(dict.fromkeys(cols)))
    panel = None                       # deeplob: the whole dataset as arrays, loaded once
    for f in folds:
        done = [] if a.fresh else [m for m in models if (out / f"metrics_{m}_{f.label()}.json").exists()]
        todo = [m for m in models if m not in done]
        if not todo:
            continue
        t0 = time.time()
        between = lambda r: pl.col("datetime").is_between(r[0], r[1])
        tabular = [m for m in todo if m not in ("deeplob", "blend")]
        if tabular:
            tr = lf.filter(between(f.train), pl.col("y_r").is_not_null()).collect()
            va = lf.filter(between(f.val), pl.col("y_r").is_not_null()).collect()
        te = lf.filter(between(f.test)).collect()
        log(f"{f.label()}: train {f.train[0]:%Y-%m-%d}..{f.train[1]:%Y-%m-%d}, test {te.height:,} rows", lp)
        for m in todo:
            t1 = time.time()
            if m == "blend":
                # equal-weight average of the deeplob and lgbm quantiles of this fold (both must exist)
                pa, pb = out / f"pred_deeplob_{f.label()}.parquet", out / f"pred_lgbm_{f.label()}.parquet"
                if not (pa.exists() and pb.exists()):
                    log(f"  blend: needs deeplob and lgbm predictions for {f.label()} - skipped", lp)
                    continue
                a_, b_ = pl.read_parquet(pa), pl.read_parquet(pb)
                qc = [x for x in a_.columns if "_q" in x]
                pred = (a_.join(b_, on=["company_id", "datetime"], suffix="_l")
                          .select(["company_id", "datetime"] + [((pl.col(x) + pl.col(x + "_l")) / 2).alias(x) for x in qc]))
                model = None
            elif m == "deeplob":
                from . import deep
                if panel is None:
                    log("  loading the panel for deeplob ...", lp)
                    panel = deep.Panel(pl.read_parquet(dataset_path(), columns=list(dict.fromkeys(
                        ["company_id", "datetime"] + FEATURES + ["y_g_n", "y_r_n", "y_u_n", "y_d_n", "y_v"]))))
                pred = deep.fit_predict(c["deeplob"], panel, f, c["quantiles"], out, log=lambda s: log(s, lp))
                model = None
            else:
                model = BL.make(m, c).fit(tr, va, c["quantiles"], log=lambda s: log(s, lp))
                pred = model.predict(te)
            pred.write_parquet(out / f"pred_{m}_{f.label()}.parquet")
            if m == "lgbm":
                model.importance().write_parquet(out / f"importance_{f.label()}.parquet")
            sc = {"model": m, "fold": f.label(), "test_start": str(f.test[0].date()), "test_end": str(f.test[1].date()),
                  **evaluate(pred, te, c["quantiles"])}
            (out / f"metrics_{m}_{f.label()}.json").write_text(json.dumps(sc, indent=1))
            log(f"  {m}: pinball r {sc['y_r_n.pinball']:.4f} g {sc['y_g_n.pinball']:.4f} u {sc['y_u_n.pinball']:.4f} "
                f"d {sc['y_d_n.pinball']:.4f} v {sc['y_v.pinball']:.4f} | IC {sc['y_r.rank_ic']:.4f} "
                f"| vol R2 {sc['y_v.r2']:.3f} ({time.time() - t1:.0f}s)", lp)
        tr = va = te = None
        log(f"{f.label()} done ({time.time() - t0:.0f}s)", lp)
        lock.touch()
    cmd_report(a, name)


def cmd_rescore(a):
    """Recompute every fold's metrics from the saved predictions (after a change to metrics.py)."""
    c, out = cfg(), results_dir(a.name)
    cols = ["company_id", "datetime", "sigma", "close", "o_next", "h_next", "l_next", "c_next", "v_next"] + \
        TARGETS + ["y_g_n", "y_r_n", "y_u_n", "y_d_n"]
    ds = pl.scan_parquet(dataset_path()).select(list(dict.fromkeys(cols)))
    for p in sorted(out.glob("pred_*.parquet")):
        m, fold = p.stem.split("_", 2)[1], p.stem.split("_", 2)[2]
        pred = pl.read_parquet(p)
        lo, hi = pred["datetime"].min(), pred["datetime"].max()
        te = ds.filter(pl.col("datetime").is_between(lo, hi)).collect()
        mp = out / f"metrics_{m}_{fold}.json"
        old = json.loads(mp.read_text()) if mp.exists() else {}
        sc = {k: old.get(k) for k in ("model", "fold", "test_start", "test_end")} | evaluate(pred, te, c["quantiles"])
        mp.write_text(json.dumps(sc, indent=1))
        log(f"rescored {p.name}")
    cmd_report(a, a.name)


def cmd_report(a, name=None):
    name = name or a.name
    out = results_dir(name)
    rows = [json.loads(p.read_text()) for p in sorted(out.glob("metrics_*.json"))]
    if not rows:
        sys.exit(f"no metrics in {out}")
    df = pl.DataFrame(rows).with_columns(pl.col(pl.Float64).fill_nan(None))   # NaN (e.g. constant P50) -> missing
    df.write_csv(out / "metrics_by_fold.csv")
    keys = ["y_g_n.pinball", "y_r_n.pinball", "y_u_n.pinball", "y_d_n.pinball", "y_v.pinball",
            "y_r_n.coverage", "y_u_n.coverage", "y_d_n.coverage", "y_v.coverage",
            "y_r.rank_ic", "oc.rank_ic", "y_g.rank_ic", "y_r.hit_rate", "y_v.r2",
            "ohlc.mape_open", "ohlc.mape_high", "ohlc.mape_low", "ohlc.mape_close",
            "fill.low_d10", "fill.low_d50", "fill.low_d90"]
    summ = df.group_by("model").agg([pl.len().alias("folds")] + [pl.col(k).mean() for k in keys]).sort("model")
    ic = df.filter(pl.col("model") == "lgbm")["y_r.rank_ic"].drop_nulls()
    lines = [f"# {name}", "", f"folds: {df['fold'].n_unique()} ({df['test_start'].min()} .. {df['test_end'].max()})", "",
             "| metric | " + " | ".join(summ["model"]) + " |", "|---|" + "---|" * summ.height]
    for k in keys:
        lines.append(f"| {k} | " + " | ".join("-" if v is None else f"{v:.4f}" for v in summ[k]) + " |")
    if len(ic) > 1:
        lines += ["", f"lgbm rank IC across folds: mean {ic.mean():.4f}, std {ic.std():.4f}, t {ic.mean() / ic.std() * len(ic) ** 0.5:.2f}"]
    # head-to-head, fold by fold: the deep model against LightGBM (the baseline it must beat)
    models_here = set(df["model"].to_list())
    for challenger in ("deeplob", "blend"):
        if not {challenger, "lgbm"} <= models_here:
            continue
        a = df.filter(pl.col("model") == challenger)
        b = df.filter(pl.col("model") == "lgbm")
        j = a.join(b, on="fold", suffix="_lgbm")
        lines += ["", f"## {challenger} vs lgbm, fold by fold ({j.height} folds)", "",
                  f"| target | {challenger} better in | mean change vs lgbm | worst fold |", "|---|---|---|---|"]
        for t in ("y_g_n", "y_r_n", "y_u_n", "y_d_n", "y_v"):
            k = f"{t}.pinball"
            ch = ((j[k] - j[k + "_lgbm"]) / j[k + "_lgbm"] * 100)
            lines.append(f"| {t} pinball | {int((ch < 0).sum())} of {j.height} | {ch.mean():+.2f}% | {ch.max():+.2f}% |")
        for k in ("y_r.rank_ic", "oc.rank_ic", "y_g.rank_ic", "y_v.r2"):
            d_ = (j[k] - j[k + "_lgbm"]).drop_nulls()
            if len(d_):
                lines.append(f"| {k} | higher in {int((d_ > 0).sum())} of {len(d_)} | {d_.mean():+.4f} | {d_.min():+.4f} |")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def cmd_predict(a):
    from . import live
    live.run(retrain=a.retrain, force=a.force, wait_minutes=a.wait, rebuild=not a.no_build)


def cmd_predict_close(a):
    from . import live
    live.run_at_close(force=a.force, wait_minutes=a.wait)


def cmd_close_check(a):
    from . import live
    rep = live.close_check(log=print)
    print(rep.read_text(encoding="utf-8") if rep else "nothing to check yet (no predictions_close_*.parquet)")


def cmd_link_map(a):
    from . import features as FT
    from . import links as LK
    if not FT.LINKS:
        sys.exit("link-map is for the ALT-LINK model (link_features: true):  nseml_model.cmd alt_link link-map")
    if a.redo:
        if not dataset_path().exists():
            sys.exit("no dataset yet - run build (the first build draws the map)")
        days = _days()
        lo, hi, _ = LK.window(days)
        cand = FT.link_candidates()
        have = pl.scan_parquet(dataset_path()).collect_schema().names()
        hand = [h for h in LK.LINK_HAND if h in have]          # for the univariate check in link_map.txt
        d = (pl.scan_parquet(dataset_path())
               .select(list(dict.fromkeys(["company_id", "datetime", "sigma", "y_r", "y_g", "y_r_n", "y_g_n"] + cand + hand)))
               .filter(pl.col("datetime").is_between(lo, hi)).collect())
        LK.mine(d, cand, log, days=days)
        print("\nNew map drawn. Run build next: the pair columns change, so this model's earlier walk-forward results are "
              "out of date and its live models retrain on the next predict.\n")
    print(LK.show())


def main():
    ap = argparse.ArgumentParser(prog="python -m nseml", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="settings file to use instead of config.yaml (same as %%NSEMLENGINE_CONFIG%%)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("paths"); sub.add_parser("build"); sub.add_parser("folds")
    p = sub.add_parser("baselines")
    p.add_argument("--models", default="naive,ewma,lgbm",
                   help="any of naive, ewma, lgbm, deeplob, blend (= average of deeplob and lgbm; run after both)")
    p.add_argument("--last", type=int, default=8, help="only the last N folds (0 = all)")
    p.add_argument("--name")
    p.add_argument("--fresh", action="store_true", help="re-run folds that already have results under this name")
    p = sub.add_parser("report")
    p.add_argument("--name", required=True)
    p = sub.add_parser("predict", help="nightly: forecasts for the next session (trains models when due)")
    p.add_argument("--retrain", action="store_true", help="train new models now")
    p.add_argument("--force", action="store_true", help="redo the forecast file even if it exists")
    p.add_argument("--wait", type=int, default=None, help="minutes to wait for today's data (default live.wait_minutes)")
    p.add_argument("--no-build", action="store_true", help="use the existing dataset (skip the rebuild)")
    p = sub.add_parser("predict-close", help="~15:37: forecast from today's 15:30 snapshot (no training)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--wait", type=int, default=10, help="minutes to wait for the snapshot file")
    sub.add_parser("close-check", help="at-close vs nightly forecast, snapshot close error, paper overnight trade")
    p = sub.add_parser("link-map", help="ALT-LINK model: show the system map of linked inputs")
    p.add_argument("--redo", action="store_true", help="draw the map again from the current dataset (then run build)")
    p = sub.add_parser("rescore", help="recompute metrics from saved predictions")
    p.add_argument("--name", required=True)
    a = ap.parse_args()
    if a.config:
        import os
        from pathlib import Path
        if not Path(a.config).is_file():
            sys.exit(f"--config: file not found: {a.config}")
        os.environ["NSEMLENGINE_CONFIG"] = str(Path(a.config).resolve())
    {"paths": cmd_paths, "build": cmd_build, "folds": cmd_folds, "baselines": cmd_baselines, "report": cmd_report,
     "rescore": cmd_rescore, "predict": cmd_predict, "predict-close": cmd_predict_close,
     "close-check": cmd_close_check, "link-map": cmd_link_map}[a.cmd](a)


if __name__ == "__main__":
    main()
