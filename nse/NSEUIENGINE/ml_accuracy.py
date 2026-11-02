"""
NSEUIENGINE - ml_accuracy.py: how close was NSEMLENGINE's OHLCV forecast to what really happened?

    python ml_accuracy.py            evaluate every forecast whose session now has real prices (new ones only)
    python ml_accuracy.py --all      re-evaluate everything
    python ml_accuracy.py --report   rebuild the report only

Runs every weekday at 22:00 in the NSEUIENGINE-SignalLog task (after the signal ledger), with the Python that runs
NSEDATAENGINE (pandas + polars). Reads only: NSEMLENGINE predictions_<date>.csv and NSEDATAENGINE prices (through
its contract). Writes, in NSEUIENGINE's reports folder:
  ml_accuracy\\forecast_vs_actual_<session>.csv   per stock: predicted P50 vs actual O/H/L/C/V, error %, in-band
  ml_accuracy_history.csv                        one row of scores per session
  ml_accuracy_report.txt                         latest session + rolling 20-session averages

Errors are in % of the forecast's reference close (the close the forecast was made from). "Naive" = no change
(open = high = low = close = that reference close), the benchmark the model has to beat. Band = P10..P90 (ideal 80%
inside). Rank IC = Spearman correlation of the predicted return with the actual return, across stocks.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import math
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fyers_token import _read as env_value, data_dir  # noqa: E402  (stdlib only)

_ROOT = env_value("NSE_DATA_ROOT") or str((Path(env_value("NSE_ROOT")) if env_value("NSE_ROOT") else HERE.parents[1]) / "data")
REPORTS = (data_dir() if (env_value("NSEUIENGINE_DATA_DIR") or _ROOT)
           else Path(os.environ.get("NSEUIENGINE_HOME") or HERE)) / "reports"
OUT_DIR = REPORTS / "ml_accuracy"
HISTORY = REPORTS / "ml_accuracy_history.csv"
REPORT = REPORTS / "ml_accuracy_report.txt"
TARGETS = ("open", "high", "low", "close")


def first_dir(*cands):
    for c in cands:
        if c and Path(c).is_dir():
            return Path(c)
    return None


DATA_HOME = first_dir(env_value("NSEDATAENGINE_HOME"), os.path.expandvars(os.path.expandvars(__import__("os").environ.get("NSE_ROOT", r"D:\CBIL") + r"/code/NSEDATAENGINE")), HERE.parent / "NSEDATAENGINE")
ML_HOME = first_dir(env_value("NSEMLENGINE_HOME"), os.path.expandvars(os.path.expandvars(__import__("os").environ.get("NSE_ROOT", r"D:\CBIL") + r"/code/NSEMLENGINE")), HERE.parent / "NSEMLENGINE")
PRED_DIR = first_dir(env_value("NSEMLENGINE_WORK_DIR") and Path(env_value("NSEMLENGINE_WORK_DIR")) / "predictions",
                     _ROOT and Path(_ROOT) / "NSEMLENGINE" / "work" / "predictions",
                     ML_HOME / "work" / "predictions" if ML_HOME else None)


def load_prices(start) -> pd.DataFrame:
    import polars as pl
    sys.path.insert(0, str(DATA_HOME))
    import nse_contract as C
    p = C.file_path("raw_ohlcv", "master")
    df = (pl.scan_parquet(p).filter(pl.col("datetime") >= start)
          .select("company_id", "datetime", "open", "high", "low", "close", "volume").collect().to_pandas())
    df["datetime"] = pd.to_datetime(df["datetime"]).dt.normalize()
    return df


def fill_prob(b, p10, p50, p90):
    return np.where(b <= p10, 0.1, np.where(b <= p50, .1 + .4 * (b - p10) / (p50 - p10).replace(0, np.nan),
                    np.where(b <= p90, .5 + .4 * (b - p50) / (p90 - p50).replace(0, np.nan), .95)))


def spearman(a, b) -> float:
    m = a.notna() & b.notna()
    return float(a[m].rank().corr(b[m].rank())) if m.sum() >= 10 else float("nan")


def evaluate(pred_file: Path, prices: pd.DataFrame):
    """-> (session, per-stock frame, summary dict) or None if the session has no prices yet."""
    ml = pd.read_csv(pred_file).drop_duplicates("company_id").set_index("company_id")
    pdate = pd.Timestamp(datetime.strptime(pred_file.stem.split("_")[-1], "%Y%m%d"))
    later = sorted(d for d in prices["datetime"].unique() if d > pdate)
    if not later:
        return None
    session = pd.Timestamp(later[0])
    act = prices[prices["datetime"] == session].set_index("company_id")[["open", "high", "low", "close", "volume"]]
    d = ml.join(act, how="inner")
    d = d[d["close_today"] > 0]
    if len(d) < 20:
        return None
    pc = d["close_today"]
    out = pd.DataFrame({"ref_close": pc, "is_fno": d.get("is_fno")})
    s = {"session": f"{session:%Y-%m-%d}", "forecast_from": f"{pdate:%Y-%m-%d}", "stocks": len(d),
         "model": str(d["models"].iloc[0]) if "models" in d else "",
         "market_median_ret_pct": float(((d["close"] / pc - 1) * 100).median()),
         "share_up_pct": float((d["close"] > pc).mean() * 100)}
    for k in TARGETS:
        a, p50 = d[k], d[f"{k}_p50"]
        err, naive = (p50 - a).abs() / pc * 100, (pc - a).abs() / pc * 100
        out[f"{k}_pred_p50"], out[f"{k}_actual"] = p50.round(2), a.round(2)
        out[f"{k}_err_pct"] = ((p50 - a) / pc * 100).round(2)
        out[f"{k}_in_band"] = (a >= d[f"{k}_p10"]) & (a <= d[f"{k}_p90"])
        s.update({f"{k}_medae_pct": float(err.median()), f"{k}_naive_medae_pct": float(naive.median()),
                  f"{k}_ml_closer_pct": float((err < naive).mean() * 100),
                  f"{k}_in_band_pct": float(out[f"{k}_in_band"].mean() * 100),
                  f"{k}_below_p10_pct": float((a < d[f"{k}_p10"]).mean() * 100),
                  f"{k}_above_p90_pct": float((a > d[f"{k}_p90"]).mean() * 100)})
    v, vp = d["volume"].clip(lower=1), d["volume_p50"].clip(lower=1)
    out["volume_pred_p50"], out["volume_actual"] = vp.round(0), d["volume"]
    out["volume_err_pct"] = ((vp / v - 1) * 100).round(1)
    s.update({"volume_med_abs_err_pct": float((np.exp((np.log(vp) - np.log(v)).abs()) - 1).median() * 100),
              "volume_log_corr": float(np.corrcoef(np.log(vp), np.log(v))[0, 1]),
              "volume_in_band_pct": float(((d["volume"] >= d["volume_p10"]) & (d["volume"] <= d["volume_p90"])).mean() * 100)})
    ret = (d["close"] / pc - 1) * 100
    oc = (d["close"] / d["open"] - 1) * 100
    gap = (d["open"] / pc - 1) * 100
    out["ml_exp_return_pct"], out["actual_return_pct"], out["ml_rank"] = d["exp_return_pct"], ret.round(2), d["rank_return"]
    q = pd.qcut(d["rank_return"].rank(method="first"), 5, labels=False)
    s.update({"rank_ic_close": spearman(d["exp_return_pct"], ret),
              "rank_ic_open_close": spearman(d["close_p50"] / d["open_p50"] - 1, oc),
              "rank_ic_gap": spearman(d["exp_gap_pct"], gap),
              "top_minus_bottom_quintile_pct": float(ret[q == 4].mean() - ret[q == 0].mean()),
              "top_quintile_ret_pct": float(ret[q == 4].mean())})
    fno = d.get("is_fno", pd.Series(0, index=d.index)) == 1
    s["rank_ic_close_fno"] = spearman(d["exp_return_pct"][fno], ret[fno])
    s["rank_ic_close_nonfno"] = spearman(d["exp_return_pct"][~fno], ret[~fno])
    rng, prng = (d["high"] - d["low"]) / pc * 100, (d["high_p50"] - d["low_p50"]) / pc * 100
    s.update({"range_pred_median_pct": float(prng.median()), "range_actual_median_pct": float(rng.median()),
              "range_rank_corr": spearman(prng, rng)})
    pf = fill_prob(pc, d["low_p10"], d["low_p50"], d["low_p90"])
    s.update({"fill_at_ref_close_pred_pct": float(np.nanmean(pf) * 100),
              "fill_at_ref_close_actual_pct": float((d["low"] <= pc).mean() * 100)})
    return f"{session:%Y-%m-%d}", out.sort_values("ml_rank", ascending=False), s


def report(h: pd.DataFrame) -> str:
    L = [f"NSEMLENGINE forecast accuracy - {datetime.now():%Y-%m-%d %H:%M}", "=" * 100]
    if h.empty:
        return "\n".join(L + ["no evaluated sessions yet"])
    h = h.sort_values("session")
    last = h.iloc[-1]
    w = L.append

    def block(r, title):
        w(f"\n{title}")
        w(f"   {'':6s} {'model err':>10s} {'naive err':>10s} {'model closer':>13s} {'in P10-P90':>11s} {'<P10':>6s} {'>P90':>6s}")
        for k in TARGETS:
            w(f"   {k:6s} {r[f'{k}_medae_pct']:>9.2f}% {r[f'{k}_naive_medae_pct']:>9.2f}% {r[f'{k}_ml_closer_pct']:>12.0f}% "
              f"{r[f'{k}_in_band_pct']:>10.0f}% {r[f'{k}_below_p10_pct']:>5.0f}% {r[f'{k}_above_p90_pct']:>5.0f}%")
        w(f"   volume: median miss {r['volume_med_abs_err_pct']:.0f}%, log corr {r['volume_log_corr']:.2f}, "
          f"in band {r['volume_in_band_pct']:.0f}%")
        w(f"   rank IC close {r['rank_ic_close']:+.3f} (F&O {r['rank_ic_close_fno']:+.3f}, others "
          f"{r['rank_ic_close_nonfno']:+.3f}); open->close {r['rank_ic_open_close']:+.3f}; gap {r['rank_ic_gap']:+.3f}")
        w(f"   top-minus-bottom quintile {r['top_minus_bottom_quintile_pct']:+.2f}% (top quintile "
          f"{r['top_quintile_ret_pct']:+.2f}%)")
        w(f"   range predicted {r['range_pred_median_pct']:.2f}% vs actual {r['range_actual_median_pct']:.2f}% "
          f"(rank corr {r['range_rank_corr']:.2f}); fills at ref close predicted {r['fill_at_ref_close_pred_pct']:.0f}% "
          f"vs actual {r['fill_at_ref_close_actual_pct']:.0f}%")

    block(last, f"LATEST SESSION {last['session']} (forecast from {last['forecast_from']} close, {int(last['stocks'])} "
                f"stocks; market median {last['market_median_ret_pct']:+.2f}%, {last['share_up_pct']:.0f}% up)")
    r20 = h.tail(20)
    num = r20.select_dtypes("number").mean()
    block(num, f"LAST {len(r20)} SESSIONS (averages)")
    ics = r20["rank_ic_close"].dropna()
    if len(ics) >= 3 and ics.std() > 0:
        t = ics.mean() / (ics.std(ddof=1) / math.sqrt(len(ics)))
        w(f"\n   rank IC close: mean {ics.mean():+.3f}, positive on {(ics > 0).mean() * 100:.0f}% of sessions, t = {t:+.2f}"
          f"  (|t| > 2 = unlikely to be luck; NSEMLENGINE's own backtest: about +0.07)")
    w(f"\nHistory: {HISTORY}\nPer stock: {OUT_DIR}\\forecast_vs_actual_<session>.csv")
    w("One session is noise. Judge the averages after ~20 sessions.")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="re-evaluate every forecast")
    ap.add_argument("--report", action="store_true", help="rebuild the report only")
    a = ap.parse_args(argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    def log(m):
        print(f"{datetime.now():%H:%M:%S}  ml_accuracy  {m}", flush=True)

    hist = pd.read_csv(HISTORY) if HISTORY.exists() else pd.DataFrame()
    if not a.report:
        if not (PRED_DIR and DATA_HOME):
            log(f"NSEMLENGINE predictions ({PRED_DIR}) or NSEDATAENGINE ({DATA_HOME}) not found")
            return 1
        done = set() if a.all or hist.empty else set(hist["forecast_from"].astype(str))
        files = [f for f in sorted(PRED_DIR.glob("predictions_*.csv"))
                 if a.all or f"{datetime.strptime(f.stem.split('_')[-1], '%Y%m%d'):%Y-%m-%d}" not in done]
        if files:
            start = datetime.strptime(files[0].stem.split("_")[-1], "%Y%m%d")
            prices = load_prices(start)
            rows = []
            for f in files:
                r = evaluate(f, prices)
                if r is None:
                    log(f"{f.name}: its session has no prices yet")
                    continue
                session, per_stock, summary = r
                per_stock.to_csv(OUT_DIR / f"forecast_vs_actual_{session}.csv")
                rows.append(summary)
                log(f"{f.name} -> session {session}: close err {summary['close_medae_pct']:.2f}% (naive "
                    f"{summary['close_naive_medae_pct']:.2f}%), rank IC {summary['rank_ic_close']:+.3f}")
            if rows:
                new = pd.DataFrame(rows)
                if not hist.empty:
                    hist = hist[~hist["session"].isin(new["session"])]
                hist = pd.concat([hist, new], ignore_index=True) if not hist.empty else new
                hist.sort_values("session").to_csv(HISTORY, index=False)
        else:
            log("no new forecasts to evaluate")
    REPORT.write_text(report(hist), encoding="utf-8")
    log(f"report {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
