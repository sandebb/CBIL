"""
Overnight trade on NSEMLENGINE's predicted gap: buy the top N stocks at today's official close (NSE post-close session,
15:40-16:00, market order = fills at the closing price), sell them at the next day's official open (market order in
the 09:00-09:07 pre-open = fills at the opening auction price). Both prices are exact, so no slippage is charged.

Predictions: the walk-forward test predictions (work\\results\\<run>\\pred_<model>_f*.parquet - each made by a model
trained only on earlier data), plus the nightly live forecasts (work\\predictions\\predictions_YYYYMMDD.parquet) for
days after the last walk-forward fold. Rank = predicted gap in rupee terms (y_g_n P50 x sigma = exp_gap_pct).
Universe (known the evening before): 60-day median turnover >= Rs 25 cr, close >= Rs 50, no high = low day in 20.
Costs (Shoonya delivery, Rs 1 lakh per stock): STT 0.1% buy + 0.1% sell, stamp 0.015% buy, NSE + SEBI fees + GST,
DP Rs 10.62 per stock sold = 23.3 bps per round trip.

Upper-band closes: a stock that closes locked at its upper price band has (almost) no sellers, so a post-close buy at
the closing price would most likely NOT fill. From the 1-minute bars (data\\history.db) a buy day is flagged LOCKED when
the stock closed at its day high and at least LOCK_MIN of the last 30 one-minute bars (15:00-15:29) traded only at that
high (high = low = day high) - the price could not go higher and nothing traded below. The report shows the results
with every trade, and without the locked ones (the trades you could really have bought).

    python scripts\\research\\ml_overnight_backtest.py [--months 3] [--tops 1,2,3,4,5,9,18] [--run phase1] [--model blend]
    -> runs\\ml_overnight_backtest.txt, runs\\ml_overnight_trades.csv (every trade)
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import glob
import os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
ML = Path(os.environ.get("NSEMLENGINE_HOME", __import__("os").environ.get("NSE_ROOT", r"D:\CBIL") + r"/code\NSEMLENGINE"))
WORK = "work"           # --work work_alt: the ALT model's predictions
LAKH = 100_000
LOCK_MIN = 10            # of the 30 bars 15:00-15:29 flat at the day high -> locked at the upper band


def lock_flags(start) -> pd.DataFrame:
    """One row per (company_id, buy_day) with the 15:00-15:29 one-minute bars: n30 bars, their max high, and how many
    bars traded at a single price (high = low) together with that price, for the lock test. One pass over history.db,
    cached in runs\\close_lock_bars.parquet."""
    import sqlite3
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from nsealgo.config import load_config, resolve_path
    db = resolve_path(load_config(ROOT / "config" / "system.yaml"), "data_dir") / "history.db"
    cache = ROOT / "runs" / "close_lock_bars.parquet"
    if cache.exists() and cache.stat().st_mtime > db.stat().st_mtime:
        bars = pd.read_parquet(cache)
        if bars["buy_day"].min() <= start + pd.Timedelta(days=4).to_pytimedelta():
            return bars
    c = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    q = ("SELECT key, substr(ts,1,10) AS d, high, low FROM minute "
         "WHERE ts >= ? AND substr(ts,12,5) BETWEEN '15:00' AND '15:29'")
    bars = pd.read_sql_query(q, c, params=(f"{start:%Y-%m-%d}",))
    c.close()
    bars["company_id"] = bars["key"].str.split(":").str[1]
    bars["buy_day"] = pd.to_datetime(bars["d"]).dt.date
    bars = bars[["company_id", "buy_day", "high", "low"]]
    cache.parent.mkdir(exist_ok=True)
    bars.to_parquet(cache, index=False)
    return bars


def lock_table(bars: pd.DataFrame, x: pd.DataFrame) -> pd.DataFrame:
    """flat_bars = 15:00-15:29 bars that traded only at the stock's official day high (high = low = day high)."""
    b = bars.merge(x[["company_id", "buy_day", "high"]].rename(columns={"high": "day_high"}), on=["company_id", "buy_day"])
    b["flat"] = (b["high"] == b["low"]) & (b["high"] >= b["day_high"] * 0.9999)
    return b.groupby(["company_id", "buy_day"]).agg(flat_bars=("flat", "sum"), n30=("flat", "size")).reset_index()


def cost_bps(value=LAKH) -> float:
    stt = 0.001 * value * 2
    stamp = 0.00015 * value
    exch = 0.0000297 * value * 2
    sebi = 10 / 1e7 * value * 2
    gst = 0.18 * (exch + sebi)
    dp = 10.62
    return (stt + stamp + exch + sebi + gst + dp) / value * 1e4


def load(run: str, model: str) -> pd.DataFrame:
    fs = sorted(glob.glob(str(ML / WORK / "results" / run / f"pred_{model}_f*.parquet")))
    p = pd.concat([pd.read_parquet(f, columns=["company_id", "datetime", "y_g_n_q50"]) for f in fs])
    p = p.drop_duplicates(["company_id", "datetime"], keep="last")
    ds = pd.read_parquet(ML / "work" / "dataset_v2.parquet",
                         columns=["company_id", "datetime", "high", "low", "close", "volume", "sigma",
                                  "next_datetime", "o_next"],
                         filters=[("datetime", ">=", p["datetime"].min() - pd.Timedelta(days=150))])
    ds = ds.sort_values(["company_id", "datetime"])
    ds["turn60"] = (ds["close"] * ds["volume"]).groupby(ds["company_id"]).transform(
        lambda s: s.rolling(60, min_periods=40).median())
    ds["lock20"] = (ds["high"] == ds["low"]).astype(float).groupby(ds["company_id"]).transform(
        lambda s: s.rolling(20, min_periods=1).sum())
    wf = p.merge(ds, on=["company_id", "datetime"], how="inner")
    wf["pred_gap"] = wf["y_g_n_q50"] * wf["sigma"]
    wf["src"] = "walk-forward"
    # live nightly forecasts after the last walk-forward day
    last = wf["datetime"].max()
    live = []
    for f in sorted(glob.glob(str(ML / WORK / "predictions" / "predictions_????????.parquet"))):
        d = pd.Timestamp(Path(f).stem[-8:])
        if d <= last:
            continue
        x = pd.read_parquet(f, columns=["company_id", "exp_gap_pct"])
        x["datetime"] = d
        live.append(x)
    if live:
        lv = pd.concat(live).merge(ds, on=["company_id", "datetime"], how="inner")
        lv["pred_gap"] = np.log1p(lv["exp_gap_pct"] / 100)
        lv["src"] = "live"
        wf = pd.concat([wf, lv], ignore_index=True)
    x = wf[(wf["turn60"] >= 25e7) & (wf["close"] >= 50) & (wf["lock20"] == 0) & wf["o_next"].notna()].copy()
    x["gross_bps"] = (x["o_next"] / x["close"] - 1) * 1e4
    x = x[x["gross_bps"].abs() < 2000]
    x["buy_day"] = pd.to_datetime(x["datetime"]).dt.date
    x["sell_day"] = pd.to_datetime(x["next_datetime"]).dt.date
    return x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=float, default=3)
    ap.add_argument("--tops", default="1,2,3,4,5,9,18")
    ap.add_argument("--run", default="phase1")
    ap.add_argument("--model", default="blend")
    ap.add_argument("--work", default="work", help="work (original model) or work_alt (ALT model with delivery/short/MTF)")
    a = ap.parse_args()
    global WORK
    WORK = a.work
    tops = [int(t) for t in a.tops.split(",")]
    cost = cost_bps()
    x = load(a.run, a.model)
    end = max(x["buy_day"])
    start = (pd.Timestamp(end) - pd.DateOffset(months=a.months)).date()
    x = x[x["buy_day"] > start].copy()
    x["net_bps"] = x["gross_bps"] - cost
    lf = lock_table(lock_flags(start), x)
    x = x.merge(lf, on=["company_id", "buy_day"], how="left")
    x["locked"] = (x["flat_bars"].fillna(0) >= LOCK_MIN) & (x["close"] >= x["high"] * 0.9995)
    x["no_minute_data"] = x["flat_bars"].isna()
    days = sorted(x["buy_day"].unique())
    nd = len(days)
    src = x.groupby("src")["buy_day"].nunique().to_dict()
    out = [f"Overnight ML trade: buy the top N predicted gap-ups at the official close (post-close session), sell at "
           f"the next official open (pre-open auction)",
           f"{nd} trading nights, bought {days[0]} .. {days[-1]} ({', '.join(f'{k}: {v} days' for k, v in src.items())}); "
           f"model {a.model}; Rs 1 lakh per stock; costs {cost:.1f} bps per round trip (Shoonya delivery), no slippage",
           f"locked at the upper band at the close (>= {LOCK_MIN} of the last 30 one-minute bars flat at the day high): "
           f"{x['locked'].sum()} of {len(x)} stock-nights; no minute data for {x['no_minute_data'].sum()} (kept)", ""]
    allm = x.groupby("buy_day")["gross_bps"].mean()
    out.append(f"Baseline - every stock in the universe (~{x.groupby('buy_day').size().mean():.0f} a night): average "
               f"overnight move {allm.mean():+.1f} bps before costs, {allm.mean() - cost:+.1f} after")
    out.append("")
    x["rk_all"] = x.groupby("buy_day")["pred_gap"].rank(method="first", ascending=False)
    for n in (1, 5, 18):
        t = x[x["rk_all"] <= n]
        out.append(f"top {n:2d} as ranked: {100 * t['locked'].mean():.0f}% of the picks were locked at the upper band "
                   f"(their average {t[t['locked']]['gross_bps'].mean():+.0f} bps gross, the others "
                   f"{t[~t['locked']]['gross_bps'].mean():+.0f})")
    out.append("")
    variants = [("A. ALL picks (assumes even locked stocks could be bought - NOT realistic)", x),
                ("B. BUYABLE only: locked stocks removed before ranking (the next-best stock takes the slot)", x[~x["locked"]].copy())]
    for title, y in variants:
        y = y.copy()
        y["rk"] = y.groupby("buy_day")["pred_gap"].rank(method="first", ascending=False)
        out.append(title)
        out.append(f"{'top N':>5s} {'trades':>6s} {'gross bps':>9s} {'net bps':>8s} {'win trades':>10s} {'win nights':>10s} "
                   f"{'net Rs total':>13s} {'Rs/night':>9s} {'best night':>11s} {'worst night':>12s} {'t':>5s}  capital")
        for n in tops:
            t = y[y["rk"] <= n]
            nightly = t.groupby("buy_day")["net_bps"].agg(["mean", "sum"])
            rs = nightly["sum"] * LAKH / 1e4
            a_ = nightly["mean"].to_numpy()
            tt = a_.mean() / (a_.std(ddof=1) / np.sqrt(len(a_))) if len(a_) > 2 and a_.std(ddof=1) > 0 else np.nan
            out.append(f"{n:5d} {len(t):6d} {t['gross_bps'].mean():+9.1f} {t['net_bps'].mean():+8.1f} "
                       f"{100 * (t['net_bps'] > 0).mean():9.0f}% {100 * (rs > 0).mean():9.0f}% {rs.sum():+13,.0f} "
                       f"{rs.mean():+9,.0f} {rs.max():+11,.0f} {rs.min():+12,.0f} {tt:+5.1f}  Rs {n} lakh")
        y["month"] = pd.to_datetime(y["buy_day"]).dt.strftime("%Y-%m")
        out.append("   by month (net bps per trade):  " + "   ".join(f"top {n}" for n in tops))
        for m, g in y.groupby("month"):
            out.append(f"   {m}  " + " ".join(f"{g[g['rk'] <= n]['net_bps'].mean():+8.1f}" for n in tops))
        out.append("")
    y = x[~x["locked"]].copy()
    y["rk"] = y.groupby("buy_day")["pred_gap"].rank(method="first", ascending=False)
    t5 = y[y["rk"] <= min(5, max(tops))].sort_values(["buy_day", "rk"])
    out.append("Night by night, BUYABLE top 5 (net Rs with Rs 1 lakh each; stock: overnight %):")
    for d, g in t5.groupby("buy_day"):
        rs = g["net_bps"].sum() * LAKH / 1e4
        out.append(f"   {d} -> {g['sell_day'].iloc[0]}  {rs:+8,.0f}  " +
                   ", ".join(f"{r.company_id} {r.gross_bps / 100:+.2f}%" for r in g.itertuples()))
    x["rk"] = x["rk_all"]
    out += ["", "t = average nightly return / its standard error (|t| > 2 = unlikely to be luck). Three months is a "
            "short sample: one or two big nights can decide the total.",
            "Not included: overnight news on a stock you hold (it is in the numbers - that IS the risk), BTST short-"
            "delivery risk, and days the at-close forecast would differ from the nightly one (checked in close_check.txt)."]
    text = "\n".join(out)
    print(text)
    (ROOT / "runs").mkdir(exist_ok=True)
    (ROOT / "runs" / "ml_overnight_backtest.txt").write_text(text, encoding="utf-8")
    cols = ["buy_day", "sell_day", "rk", "company_id", "src", "pred_gap", "close", "o_next", "gross_bps", "net_bps",
            "locked", "flat_bars"]
    x[x["rk"] <= max(tops)].sort_values(["buy_day", "rk"])[cols].to_csv(ROOT / "runs" / "ml_overnight_trades.csv", index=False)


if __name__ == "__main__":
    main()
