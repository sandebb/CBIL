"""
Overnight trade - BUY ONLY the stocks the ML expects to close above where they open, then sell with the
'75% to high, else at 12:00' limit. Compared with the current trade and the switched exit rule.

    python scripts\\research\\overnight_filter_backtest.py
    -> runs\\overnight_filter.txt, runs\\overnight_filter_summary.csv, runs\\overnight_filter_nightly.csv

The buy happens at 15:40, when tomorrow's open is not known yet. So the evening filter uses the ML's OWN predicted
open:  buy only if  P50 predicted close > P50 predicted open  (both from the forecast made at the close).
  no refill = of the top N by predicted gap, buy only those that pass (the rest of the money stays in cash)
  refill    = go down the ranked list (up to rank 40) and buy the first N that pass
The sale, for the stocks bought:
  sell at open           = the pre-open auction, as today
  75%/12:00 always       = limit at open + 75% x (predicted high - open) from 09:15, rest at market at 12:00
  75%/12:00 + 09:07 check = the same, but if the predicted close is NOT above the actual 09:07 open, sell in the auction
Equal split = the capital divided equally among the stocks bought that night. Optimized = the sizing model.
Everything else (fills, impact, charges, minute-bar rules) as overnight_exit_backtest.py.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import overnight_exit_backtest as X  # noqa: E402
import overnight_sizing_backtest as B  # noqa: E402
from nsealgo.sizing import overnight as O  # noqa: E402

RUNS, log, LAKH = B.RUNS, B.log, B.LAKH
TOP, CAPITALS = [5, 9, 18], [1, 5, 20]
R_OPEN = X.BASE
R_ALWAYS = "75% to high, else at 12:00"
R_GATED = "75% to high, else at 12:00 [ML close > actual open]"
X.RULES = {k: v for k, v in X.RULES.items() if k in (R_OPEN, R_ALWAYS, R_GATED)}


def cache_bars(need: pd.DataFrame) -> None:
    """Like X.cache_bars, but adds missing days to a stock's existing cache file (days with no bars are remembered
    with a mins = -1 row so they are not read again)."""
    t0, n = time.time(), 0
    for cid, g in need.groupby("company_id"):
        f = X.BARS / f"{str(cid).replace('/', '_')}.parquet"
        old = pd.read_parquet(f) if f.exists() else X._no_bars()
        old["day"] = pd.to_datetime(old["day"]).astype("datetime64[ns]")
        days = pd.DatetimeIndex(g["sell_day"].unique()).difference(pd.DatetimeIndex(old["day"].unique()))
        if f.exists() and len(days) == 0:
            continue
        d = X.ARCHIVE / str(cid).replace("/", "_")
        parts = []
        if d.exists() and len(days):
            for q in sorted(d.glob("*_*.parquet")):
                try:
                    a, b = (pd.Timestamp(v) for v in q.stem.split("_"))
                except ValueError:
                    continue
                if ((days >= a) & (days <= b + pd.Timedelta(days=1))).any():
                    parts.append(pd.read_parquet(q, columns=["ts", "open", "high", "volume"]))
        new = X._no_bars()
        if parts:
            m = pd.concat(parts).drop_duplicates("ts")
            m["ts"] = pd.to_datetime(m["ts"]).astype("datetime64[ns]")
            m["day"] = m["ts"].dt.normalize()
            m["mins"] = (m["ts"].dt.hour * 60 + m["ts"].dt.minute).astype("int16")
            m = m[m["day"].isin(days) & (m["mins"] >= X.START) & (m["mins"] < X.END)]
            new = m[["day", "mins", "open", "high", "volume"]].astype({"open": float, "high": float, "volume": float})
        miss = days.difference(pd.DatetimeIndex(new["day"].unique()))
        mark = pd.DataFrame({"day": miss, "mins": np.int16(-1), "open": np.nan, "high": np.nan, "volume": 0.0})
        out = pd.concat([x for x in (old, new, mark) if len(x)] or [X._no_bars()], ignore_index=True)
        out = out.astype({"mins": "int16"}).sort_values(["day", "mins"])
        out.to_parquet(f, index=False)
        n += 1
        if n % 50 == 0:
            log(f"  bars: {n} stocks updated ({time.time() - t0:.0f}s)")
    log(f"bars: {n} stock caches updated ({time.time() - t0:.0f}s)")


def positions(c: pd.DataFrame, hist: pd.DataFrame, variants: dict, nights_all, half_life=250.0) -> pd.DataFrame:
    """variants: name -> (rows of c to consider, N, equal_by_count). Rows are taken in rank order; the first N."""
    base = O.Params(max_stocks=40)
    rows, t0 = [], time.time()
    groups = {v: {d: g.sort_values("rank") for d, g in sub.groupby("buy_day")} for v, (sub, _, _) in variants.items()}
    for i, d in enumerate(nights_all):
        if i < B.BURN:
            continue
        em = O.fit_edge(hist[hist["buy_day"] < d], half_life=half_life)
        for v, (_, n, by_count) in variants.items():
            g = groups[v].get(d)
            if g is None or len(g) == 0:
                continue
            g = g.head(n)
            price, sig = g["close"].to_numpy(float), g["sigma_bps"].to_numpy(float)
            edge = em.edge_bps(g["pred_bps"].to_numpy(float))
            pc_rs, pre_rs, pc_real = (g["pc_rs"].to_numpy(float), g["pre_rs"].to_numpy(float),
                                      g["pc_rs_real"].to_numpy(float))
            for cap_l in CAPITALS:
                capital = cap_l * LAKH
                for split in ("EQUAL", "OPTIMIZED"):
                    if split == "EQUAL":
                        qty = np.floor((capital / (len(g) if by_count else n)) / price)
                    else:
                        from dataclasses import replace
                        qty = O.allocate_arrays(price, edge, sig, pc_rs, pre_rs, capital, replace(base, max_stocks=n))[0]
                    fq = np.minimum(qty, np.floor(B.FILL_SHARE * pc_real / price))
                    for j in np.nonzero(fq > 0)[0]:
                        rows.append((d, cap_l, f"{split} | {v}", g["company_id"].iloc[j], fq[j]))
        if (i + 1) % 100 == 0:
            log(f"  positions: {i + 1} of {len(nights_all)} nights ({time.time() - t0:.0f}s)")
    return pd.DataFrame(rows, columns=["buy_day", "capital_lakh", "strategy", "company_id", "qty"])


def main():
    t0 = time.time()
    c = B.candidates("phase1", "blend", 40)
    hist = c[["buy_day", "pred_bps", "gross_bps"]].copy()
    if B.TABLE.exists():
        B.L.build = lambda *x, **k: pd.read_parquet(B.TABLE)
    c, _ = B.liquidity(c)
    c = X.add_predictions(c, "phase1", "blend")
    nights = np.sort(c["buy_day"].unique())
    c = c[c["buy_day"] >= nights[B.BURN]].copy()
    c["passes"] = c["pred_close"] > c["pred_open"]
    log(f"evening filter (P50 close > P50 open): {100 * c['passes'].mean():.1f}% of the 40 candidates pass, "
        f"{100 * c.loc[c['rank'] <= 5, 'passes'].mean():.1f}% of the top 5")
    need = c[(c["rank"] <= 18) | c["passes"]]
    cache_bars(need[["company_id", "sell_day"]])
    st = X.exit_stats(need)
    log(f"exit stats: {len(st):,} rows ({time.time() - t0:.0f}s)")
    p = c[c["passes"]]
    variants = {}
    for n in TOP:
        variants[f"top {n}, no filter"] = (c[c["rank"] <= n], n, False)
        variants[f"top {n}, filter, no refill"] = (p[p["rank"] <= n], n, True)
        variants[f"top {n}, filter, refill"] = (p, n, False)
    pos = positions(c, hist, variants, nights)
    ev = X.evaluate(pos, c, st)
    s, nightly = X.summarize(ev)
    # how many stocks / nights in cash
    cnt = ev[ev["rule"] == R_OPEN].groupby(["capital_lakh", "strategy", "buy_day"])["company_id"].nunique()
    n_all = len(nights) - B.BURN
    held = cnt.groupby(["capital_lakh", "strategy"]).agg(["sum", "size"])
    s = s.merge(held.rename(columns={"sum": "stock_nights", "size": "nights_traded"}).reset_index(),
                on=["capital_lakh", "strategy"], how="left")
    s["avg_stocks"] = s["stock_nights"] / n_all
    s["pct_nights_in_cash"] = 100 * (1 - s["nights_traded"] / n_all)
    s["deployed_pct"] = np.nan
    dep = nightly.groupby(["capital_lakh", "strategy", "rule"])["bv"].mean().rename("avg_bought_rs").reset_index()
    s = s.merge(dep, on=["capital_lakh", "strategy", "rule"], how="left")
    S = s.set_index(["capital_lakh", "strategy", "rule"])
    f0 = lambda v: f"{v:+,.0f}"
    L_ = [f"OVERNIGHT TRADE - BUY ONLY WHAT THE ML EXPECTS TO CLOSE ABOVE ITS OPEN   ({n_all} nights "
          f"{nightly['buy_day'].min():%Y-%m-%d} .. {nightly['buy_day'].max():%Y-%m-%d}; Rs per night after all charges)", "",
          "At 15:40 tomorrow's open is unknown, so the buy filter uses the ML's own predicted open: buy only if the P50 "
          "predicted close > P50 predicted open.",
          f"  That passes {100 * c['passes'].mean():.1f}% of the 40 ranked candidates and {100 * c.loc[c['rank'] <= 5, 'passes'].mean():.1f}% "
          f"of the top 5 (the ML usually expects gap-up stocks to fall back after the open).", ""]
    pt = c.assign(oc=(c["c_next"] / c["o_next"] - 1) * 1e4, gap=c["gross_bps"])
    L_ += ["WHAT THE FILTER PICKS - all 40 candidates, what happened next day:",
           f"  {'group':34s} {'stock-nights':>12s} {'gap (close->open)':>17s} {'open->close':>11s} {'closed above open':>17s}"]
    for lab, m in [("pass (ML close > ML open)", pt["passes"]), ("fail", ~pt["passes"]),
                   ("top 5, pass", pt["passes"] & (pt["rank"] <= 5)), ("top 5, fail", ~pt["passes"] & (pt["rank"] <= 5))]:
        q = pt[m]
        L_.append(f"  {lab:34s} {len(q):12,d} {q['gap'].mean():+16.0f}b {q['oc'].mean():+10.0f}b {100 * (q['oc'] > 0).mean():16.0f}%")
    L_ += ["", "Stocks per night that pass, top N:  " + "  ".join(
        f"top {n}: avg {p[p['rank'] <= n].groupby('buy_day').size().reindex(np.unique(c['buy_day'])).fillna(0).mean():.1f}" for n in TOP)
        + f"   (all 40: avg {p.groupby('buy_day').size().reindex(np.unique(c['buy_day'])).fillna(0).mean():.1f})", ""]
    rows = [("top {n}, no filter", R_OPEN, "now: no filter, sell at open"),
            ("top {n}, no filter", R_GATED, "switched rule: no filter, 75%/12:00 + 09:07 check"),
            ("top {n}, filter, no refill", R_OPEN, "filter, no refill, sell at open"),
            ("top {n}, filter, no refill", R_ALWAYS, "filter, no refill, 75%/12:00 always"),
            ("top {n}, filter, no refill", R_GATED, "filter, no refill, 75%/12:00 + 09:07 check"),
            ("top {n}, filter, refill", R_OPEN, "filter, refill to N, sell at open"),
            ("top {n}, filter, refill", R_ALWAYS, "filter, refill to N, 75%/12:00 always"),
            ("top {n}, filter, refill", R_GATED, "filter, refill to N, 75%/12:00 + 09:07 check")]
    for n in TOP:
        now = S.loc[(5, f"OPTIMIZED | top {n}, no filter", R_OPEN), "rs_per_night"]
        L_ += [f"TOP {n} at Rs 5 lakh",
               f"  {'what':48s} | {'EQUAL':>7s} | {'OPTIMIZED':>9s} {'vs now':>7s} {'t':>5s} | {'stocks':>6s} {'invested':>9s} "
               f"{'cash nights':>11s} {'worst night':>11s} {'nights up':>9s}"]
        for v, r, lab in rows:
            v = v.format(n=n)
            e, o = S.loc[(5, f"EQUAL | {v}", r)], S.loc[(5, f"OPTIMIZED | {v}", r)]
            vs = o["rs_per_night"] - now
            dd = (nightly[(nightly["capital_lakh"] == 5) & (nightly["strategy"] == f"OPTIMIZED | {v}") & (nightly["rule"] == r)]
                  .set_index("buy_day")["pnl"] - nightly[(nightly["capital_lakh"] == 5) & (nightly["strategy"] ==
                  f"OPTIMIZED | top {n}, no filter") & (nightly["rule"] == R_OPEN)].set_index("buy_day")["pnl"])
            t = dd.mean() / (dd.std(ddof=1) / np.sqrt(len(dd))) if dd.std(ddof=1) > 0 else np.nan
            L_.append(f"  {lab:48s} | {f0(e['rs_per_night']):>7s} | {f0(o['rs_per_night']):>9s} {f0(vs):>7s} "
                      f"{t:5.1f} | {o['avg_stocks']:6.1f} {o['avg_bought_rs']:9,.0f} {o['pct_nights_in_cash']:10.0f}% "
                      f"{f0(o['worst_night_rs']):>11s} {o['pct_nights_up']:8.0f}%")
        L_.append("")
    L_ += ["BY CAPITAL - OPTIMIZED top 5, Rs per night:"]
    for v, r, lab in rows:
        v = v.format(n=5)
        L_.append(f"  {lab:48s} " + "  ".join(f"Rs {cl:2d} L {f0(S.loc[(cl, f'OPTIMIZED | {v}', r), 'rs_per_night']):>7s}"
                                             for cl in CAPITALS))
    L_ += ["", "BY YEAR - OPTIMIZED top 5, Rs 5 lakh, Rs per night:"]
    y = nightly[(nightly["capital_lakh"] == 5)].assign(year=pd.to_datetime(nightly["buy_day"]).dt.year)
    for v, r, lab in rows:
        v = v.format(n=5)
        q = y[(y["strategy"] == f"OPTIMIZED | {v}") & (y["rule"] == r)].groupby("year")["pnl"].mean()
        L_.append(f"  {lab:48s} " + "  ".join(f"{yr}: {f0(val):>7s}" for yr, val in q.items()))
    L_ += ["", "t = (Rs per night minus 'now') / its standard error; |t| > 2 = unlikely to be luck. invested = average rupees "
           "bought per night. cash nights = nights with nothing bought.",
           "Fills, slippage and charges as in overnight_exit_backtest.py (limit: trade-through by a tick, at most 10% of "
           "the volume at the target)."]
    text = "\n".join(L_)
    print(text)
    (RUNS / "overnight_filter.txt").write_text(text, encoding="utf-8")
    s.to_csv(RUNS / "overnight_filter_summary.csv", index=False)
    nightly.to_csv(RUNS / "overnight_filter_nightly.csv", index=False)
    log(f"written runs\\overnight_filter.txt / _summary.csv / _nightly.csv ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
