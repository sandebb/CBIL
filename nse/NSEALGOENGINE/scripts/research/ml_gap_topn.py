"""
Top-N on NSEMLENGINE's predicted gap: can the model's overnight-gap forecast pick the day's 15 stocks?

The forecast is made the evening before (after the close, ~20:00), so - unlike the real opening gap - it is known
long before the auction and the orders can go into the 09:00-09:08 pre-open with no timing problem.
Data: NSEMLENGINE's walk-forward test predictions (work\\results\\<run>\\pred_<model>_f*.parquet) - every prediction
was made by a model trained only on earlier data (5-year window, retrained each quarter), Nov 2023 -> Oct 2026.

Predicted gap   = y_g_n (P50, in units of the stock's daily volatility) x sigma  = log(open next day / close today)
Universe (known the evening before): 60-day median turnover >= Rs 25 cr, close >= Rs 50, no high = low day in 20.

Trades for the top N by predicted gap UP (most positive first)
  OVERNIGHT_LONG   buy at today's close, sell at the next open (delivery: Shoonya ~23 bps + 4 bps slippage)
  FADE_SHORT       short at the next open, cover at its close (intraday, 9.2 bps)
  FOLLOW_LONG      buy at the next open, sell at its close (intraday)
... and by predicted gap DOWN (most negative first): FADE_LONG, FOLLOW_SHORT (no overnight short in cash).
Also: SURPRISE = actual gap minus predicted gap (needs the real open: rank at the auction like the plain gap test),
ML_OC = predicted open->close move (y_r - y_g), and the plain actual-gap rankers over the same days for comparison.

    .venv\\Scripts\\python scripts\\research\\ml_gap_topn.py [--run phase1] [--model blend]
    -> runs\\ml_gap_topn.txt, runs\\ml_gap_topn_curve.csv
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
INTRA = 4.7 + 2.0 + 2.5          # Shoonya intraday + auction entry slippage + exit slippage
DELIV = 23.0 + 4.0               # Shoonya delivery round trip (STT 0.1% x2, stamp, DP ...) + close/open auction slippage
SHOW = (1, 2, 3, 5, 10, 15, 20, 30, 50, 100)
NMAX = 100


def load(run: str, model: str) -> pd.DataFrame:
    fs = sorted(glob.glob(str(ML / WORK / "results" / run / f"pred_{model}_f*.parquet")))
    if not fs:
        raise SystemExit(f"no predictions: {ML / 'work' / 'results' / run}/pred_{model}_f*.parquet")
    p = pd.concat([pd.read_parquet(f, columns=["company_id", "datetime", "y_g_n_q50", "y_r_n_q50"]) for f in fs])
    p = p.drop_duplicates(["company_id", "datetime"], keep="last")
    ds = pd.read_parquet(ML / "work" / "dataset_v2.parquet",
                         columns=["company_id", "datetime", "high", "low", "close", "volume", "sigma",
                                  "next_datetime", "o_next", "c_next"],
                         filters=[("datetime", ">=", p["datetime"].min() - pd.Timedelta(days=150))])
    ds = ds.sort_values(["company_id", "datetime"])
    g = ds.groupby("company_id")
    ds["turn60"] = (ds["close"] * ds["volume"]).groupby(ds["company_id"]).transform(
        lambda s: s.rolling(60, min_periods=40).median())                      # up to and including today (t)
    ds["lock20"] = (ds["high"] == ds["low"]).astype(float).groupby(ds["company_id"]).transform(
        lambda s: s.rolling(20, min_periods=1).sum())
    del g
    x = p.merge(ds, on=["company_id", "datetime"], how="inner")
    x = x[(x["turn60"] >= 25e7) & (x["close"] >= 50) & (x["lock20"] == 0) & x["o_next"].notna() & x["c_next"].notna()
          & (x["sigma"] > 0)].copy()
    x["pred_gap"] = x["y_g_n_q50"] * x["sigma"] * 1e4                          # bps (log)
    x["pred_oc"] = (x["y_r_n_q50"] - x["y_g_n_q50"]) * x["sigma"] * 1e4
    x["gap"] = np.log(x["o_next"] / x["close"]) * 1e4
    x["on"] = (x["o_next"] / x["close"] - 1) * 1e4                             # overnight return (close -> open)
    x["oc"] = (x["c_next"] / x["o_next"] - 1) * 1e4                            # next day open -> close
    x = x[(x["on"].abs() < 2000) & (x["oc"].abs() < 2000)]
    x["day"] = pd.to_datetime(x["next_datetime"]).dt.date                     # the trading day
    return x


def topn(x: pd.DataFrame, score: pd.Series, n_max=NMAX) -> pd.DataFrame:
    y = x.assign(score=score).dropna(subset=["score"])
    y = y.assign(rk=y.groupby("day")["score"].rank(method="first", ascending=False))
    return y[y["rk"] <= n_max]


def stats(t: pd.DataFrame, net: pd.Series, ndays: int):
    g = net.groupby(t["day"])
    a, s = g.mean().to_numpy(), g.sum()
    sd = a.std(ddof=1) if len(a) > 2 else 0
    return {"bps": net.mean(), "win": 100 * (net > 0).mean(), "t": a.mean() / (sd / np.sqrt(len(a))) if sd > 0 else np.nan,
            "rs_day": s.sum() * 10 / ndays, "worst": s.min() * 10, "per_day": len(t) / ndays}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="phase1")
    ap.add_argument("--model", default="blend")
    ap.add_argument("--work", default="work", help="work (original model) or work_alt (ALT model with delivery/short/MTF)")
    a = ap.parse_args()
    global WORK
    WORK = a.work
    x = load(a.run, a.model)
    ndays = x["day"].nunique()
    d0, d1 = min(x["day"]), max(x["day"])
    up = topn(x, x["pred_gap"])
    dn = topn(x, -x["pred_gap"])
    tests = [  # name, ranked frame, side, return column, cost
        ("ML gap UP    -> OVERNIGHT_LONG (close->open)", up, 1, "on", DELIV),
        ("ML gap UP    -> FADE_SHORT (open->close)", up, -1, "oc", INTRA),
        ("ML gap UP    -> FOLLOW_LONG (open->close)", up, 1, "oc", INTRA),
        ("ML gap DOWN  -> FADE_LONG (open->close)", dn, 1, "oc", INTRA),
        ("ML gap DOWN  -> FOLLOW_SHORT (open->close)", dn, -1, "oc", INTRA),
        ("ML open->close UP -> LONG", topn(x, x["pred_oc"]), 1, "oc", INTRA),
        ("ML open->close DOWN -> SHORT", topn(x, -x["pred_oc"]), -1, "oc", INTRA),
        ("actual gap UP -> SHORT at auction (comparison)", topn(x, x["gap"].where(x["gap"] > 0)), -1, "oc", INTRA),
        ("actual gap DOWN -> LONG at auction (comparison)", topn(x, (-x["gap"]).where(x["gap"] < 0)), 1, "oc", INTRA),
        ("SURPRISE gap UP (actual - predicted) -> SHORT at auction", topn(x, (x["gap"] - x["pred_gap"]).where(x["gap"] > 0)), -1, "oc", INTRA),
        ("SURPRISE gap DOWN (actual - predicted) -> LONG at auction", topn(x, (x["pred_gap"] - x["gap"]).where(x["gap"] < 0)), 1, "oc", INTRA),
    ]
    rows, out = [], [f"NSEMLENGINE predicted gap -> top N, model {a.model} (run {a.run}, walk-forward = out of sample), "
                     f"{ndays} trading days {d0} .. {d1}, Rs 1 lakh per stock",
                     f"costs: intraday {INTRA:.1f} bps, overnight delivery {DELIV:.0f} bps per trade. "
                     f"Universe: 60-day median turnover >= Rs 25 cr, close >= Rs 50, no price-limit lock in 20 days.", ""]
    # how good is the gap forecast itself?
    out.append("Does the predicted gap come true? (top N by prediction: average ACTUAL gap, % that opened in that direction)")
    out.append(f"   {'top N':>5s} {'pred UP: actual gap':>20s} {'opened up':>9s} {'pred DOWN: actual gap':>22s} {'opened down':>11s}")
    for n in SHOW:
        u, d = up[up["rk"] <= n], dn[dn["rk"] <= n]
        out.append(f"   {n:5d} {u['gap'].mean():+17.1f} bps {100 * (u['gap'] > 0).mean():8.0f}% "
                   f"{d['gap'].mean():+19.1f} bps {100 * (d['gap'] < 0).mean():10.0f}%")
    out.append(f"   all stocks: average gap {x['gap'].mean():+.1f} bps, opened up {100 * (x['gap'] > 0).mean():.0f}%, "
               f"rank correlation predicted vs actual gap (per day, average) "
               f"{x.groupby('day').apply(lambda g: g['pred_gap'].rank().corr(g['gap'].rank())).mean():.3f}")
    out.append("")
    for name, t, side, col, cost in tests:
        out.append(name)
        out.append(f"   {'top N':>5s} {'trades/d':>8s} {'bps/trade':>9s} {'win':>5s} {'t':>6s} {'Rs/day':>9s} {'worst day':>10s}")
        for n in range(1, NMAX + 1):
            tt = t[t["rk"] <= n]
            if tt.empty:
                continue
            net = side * tt[col] - cost
            s = stats(tt, net, ndays)
            rows.append({"test": name, "N": n, **s})
            if n in SHOW:
                out.append(f"   {n:5d} {s['per_day']:8.1f} {s['bps']:+9.1f} {s['win']:4.0f}% {s['t']:+6.1f} "
                           f"{s['rs_day']:+9,.0f} {s['worst']:+10,.0f}")
        tt = t[t["rk"] <= 15]
        net = side * tt[col] - cost
        yr = net.groupby(pd.to_datetime(tt["day"]).dt.year).mean()
        out.append("   top 15 by year: " + "  ".join(f"{y} {v:+.1f}" for y, v in yr.items()))
        out.append("")
    out += ["bps/trade = average net return per trade; t = average daily return / its standard error (|t| > 2 = "
            "unlikely to be luck); Rs/day with Rs 1 lakh in each stock; worst day in Rs.",
            "ML rankers are known the evening before -> orders can sit in the pre-open auction. 'actual gap' and "
            "SURPRISE rankers need the real opening price, i.e. the 09:06:50 indicative price (see nse_preopen.py)."]
    text = "\n".join(out)
    print(text)
    (ROOT / "runs").mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(ROOT / "runs" / "ml_gap_topn_curve.csv", index=False)
    (ROOT / "runs" / "ml_gap_topn.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
