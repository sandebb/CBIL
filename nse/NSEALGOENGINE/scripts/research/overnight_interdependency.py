"""
Overnight trade - who else is in the trade with us? Market, sector and cross-asset dependence of the picks.

    python scripts\\research\\overnight_interdependency.py  -> runs\\research_interdep\\report.txt (+ csv)

Tests (top-N = the trade's candidates ranked by predicted gap; 658 nights after the 60-night start):
  T1 what the profit is made of: each pick's gap = market gap + its sector's extra + the stock's own part
  T2 sector clustering: how often the top 5 share a sector, and what that does to the spread of nightly results
  T3 sector / market lead-lag: does today's move of the stock's sector (without the stock) predict its gap tomorrow,
     beyond the ML forecast?
  T4 crowding / obviousness: are the 'obvious' gap-ups (big move today, closed at the high, many names from the same
     sector in the list) worth less than the forecast says?
  T5 overnight cross-asset exposure: how much of a night's result is the US market / gold moving after we buy
Everything is measured with information in the order it arrives; T5 is exposure (risk), not a signal.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import overnight_sizing_backtest as B  # noqa: E402

OUT = B.RUNS / "research_interdep"
OUT.mkdir(parents=True, exist_ok=True)
L_: list[str] = []


def say(s=""):
    print(s, flush=True)
    L_.append(s)


def ols(y, X, names):
    """OLS with t-stats clustered by night is overkill here; plain OLS on night-demeaned data (market removed)."""
    X = np.column_stack([np.ones(len(y))] + list(X))
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ b
    s2 = e @ e / (len(y) - X.shape[1])
    se = np.sqrt(np.diag(s2 * np.linalg.inv(X.T @ X)))
    return {n: (b[i + 1], b[i + 1] / se[i + 1]) for i, n in enumerate(names)}


def main():
    home = B.L.data_engine_home(B.ROOT)
    c = B.candidates("phase1", "blend", 40)
    nights = np.sort(c["buy_day"].unique())
    c = c[c["buy_day"] >= nights[B.BURN]].copy()
    raw = pd.read_parquet(home / "data" / "raw_ohlcv.parquet", columns=["company_id", "datetime", "open", "high", "low",
                                                                          "close", "volume"],
                          filters=[("datetime", ">=", c["buy_day"].min() - pd.Timedelta(days=150))])
    raw["datetime"] = pd.to_datetime(raw["datetime"]).astype("datetime64[ns]")
    raw = raw.sort_values(["company_id", "datetime"])
    g = raw.groupby("company_id", sort=False)
    raw["pc"] = g["close"].shift(1)
    raw["gap"] = np.log(raw["open"] / raw["pc"]) * 1e4
    raw["ret"] = np.log(raw["close"] / raw["pc"]) * 1e4
    raw["tv60"] = (raw["close"] * raw["volume"]).groupby(raw["company_id"]).transform(
        lambda s: s.shift(1).rolling(60, min_periods=40).median())
    raw = raw[(raw["gap"].abs() < 2000) & (raw["tv60"] >= 25e7)]
    sec = pd.read_parquet(home / "data_ref" / "sectors.parquet", columns=["company_id", "sector"])
    raw = raw.merge(sec, on="company_id", how="left")
    # market and leave-one-out sector averages per day
    day = raw.groupby("datetime")
    raw["mkt_gap"], raw["mkt_ret"] = day["gap"].transform("mean"), day["ret"].transform("mean")
    sd = raw.dropna(subset=["sector"]).groupby(["datetime", "sector"])
    for v in ("gap", "ret"):
        s_, n_ = sd[v].transform("sum"), sd[v].transform("count")
        raw.loc[s_.index, f"sec_{v}"] = (s_ - raw.loc[s_.index, v]) / (n_ - 1).where(n_ > 1)
    raw.loc[sd["gap"].transform("count").index, "sec_n"] = sd["gap"].transform("count")
    keep = ["company_id", "datetime", "gap", "ret", "mkt_gap", "mkt_ret", "sec_gap", "sec_ret", "sector", "sec_n"]
    r = raw[keep]
    c = c.merge(r.rename(columns={"datetime": "sell_day", "gap": "gap_s", "mkt_gap": "mkt_gap_s", "sec_gap": "sec_gap_s"})
                [["company_id", "sell_day", "gap_s", "mkt_gap_s", "sec_gap_s"]], on=["company_id", "sell_day"], how="left")
    c = c.merge(r.rename(columns={"datetime": "buy_day"})[["company_id", "buy_day", "ret", "mkt_ret", "sec_ret", "sector"]],
                on=["company_id", "buy_day"], how="left")
    c["clv"] = ((c["close"] - c["low"]) / (c["high"] - c["low"])).where(c["high"] > c["low"])
    c["gross"] = c["gross_bps"]
    top5 = c[c["rank"] <= 5]
    say(f"OVERNIGHT TRADE - INTERDEPENDENCE WITH THE MARKET, SECTORS AND OTHER ASSETS   ({c['buy_day'].nunique()} nights "
        f"{c['buy_day'].min():%Y-%m-%d} .. {c['buy_day'].max():%Y-%m-%d}; gaps in bps = 1/100 of 1%)")
    say(f"sector known for {100 * top5['sector'].notna().mean():.0f}% of top-5 picks (NSE total-market index list, 22 sectors)")

    # ---- T1 decomposition
    t = top5.dropna(subset=["mkt_gap_s"]).copy()
    t["sec_x"] = (t["sec_gap_s"] - t["mkt_gap_s"]).fillna(0)
    t["own"] = t["gross"] - t["mkt_gap_s"] - t["sec_x"]
    say("\nT1  WHAT THE TOP-5 GAP IS MADE OF  (average per pick, bps)")
    say(f"   total gap {t['gross'].mean():+.1f} = market {t['mkt_gap_s'].mean():+.1f} + sector extra {t['sec_x'].mean():+.1f} "
        f"+ the stock's own {t['own'].mean():+.1f}")
    nv = t.groupby("buy_day")[["gross", "mkt_gap_s", "sec_x", "own"]].mean()
    v = nv["gross"].var()
    say(f"   night-to-night spread of the top-5 average: market explains {100 * nv['mkt_gap_s'].cov(nv['gross']) / v:.0f}%, "
        f"sector extra {100 * nv['sec_x'].cov(nv['gross']) / v:.0f}%, own part {100 * nv['own'].cov(nv['gross']) / v:.0f}%")
    pos = (nv["mkt_gap_s"] > 0).mean()
    say(f"   the market gapped up on {100 * pos:.0f}% of these nights; top-5 own part positive on "
        f"{100 * (nv['own'] > 0).mean():.0f}% of nights")

    # ---- T2 sector clustering
    say("\nT2  SECTOR CLUSTERING IN THE TOP 5")
    k = top5.dropna(subset=["sector"]).groupby(["buy_day", "sector"]).size().groupby("buy_day").max()
    nights_all = top5.groupby("buy_day")["gross"].mean()
    k = k.reindex(nights_all.index).fillna(1)
    say(f"   nights where 3+ of the top 5 share a sector: {100 * (k >= 3).mean():.0f}%; 2 share: {100 * (k == 2).mean():.0f}%")
    for lab, m in (("3+ same sector", k >= 3), ("2 same sector", k == 2), ("all different", k <= 1)):
        x = nights_all[m]
        say(f"   {lab:15s} {m.sum():4d} nights: average gap {x.mean():+6.1f} bps, night-to-night std {x.std():6.1f}, "
            f"worst night {x.min():+7.1f}")
    top_s = top5.dropna(subset=["sector"])["sector"].value_counts(normalize=True).head(6)
    say("   sectors most often picked: " + ", ".join(f"{s} {100 * p:.0f}%" for s, p in top_s.items()))
    # pairwise co-movement of picks on the same night
    pr = []
    t10 = c[c["rank"] <= 10].dropna(subset=["sector", "gap_s"])
    for d, gg in t10.groupby("buy_day"):
        x = gg[["sector", "gap_s"]].to_numpy()
        m = gg["gap_s"].mean()
        for i in range(len(x)):
            for j in range(i + 1, len(x)):
                pr.append((x[i][0] == x[j][0], x[i][1] - m, x[j][1] - m))
    pr = pd.DataFrame(pr, columns=["same", "a", "b"])
    for lab, m in (("same sector", pr["same"]), ("different sectors", ~pr["same"])):
        q = pr[m]
        say(f"   pairs of top-10 picks, {lab:17s}: {len(q):6,d} pairs, correlation of their gaps (night average removed) "
            f"{np.corrcoef(q['a'].astype(float), q['b'].astype(float))[0, 1]:+.2f}")

    # ---- T3 lead-lag (top 40, night-demeaned -> the market is removed)
    say("\nT3  DOES TODAY'S SECTOR MOVE PREDICT TOMORROW'S GAP, BEYOND THE ML?  (top-40 candidates, night average removed)")
    z = c.dropna(subset=["sec_ret", "ret", "clv", "gap_s"]).copy()
    for col in ("gross", "pred_bps", "sec_ret", "ret", "clv"):
        z[col + "_d"] = z[col] - z.groupby("buy_day")[col].transform("mean")
    res = ols(z["gross_d"].to_numpy(), [z["pred_bps_d"], z["sec_ret_d"], z["ret_d"], z["clv_d"]],
              ["ML predicted gap", "sector move today (without the stock)", "stock's own move today",
               "closed near the day high (0-1)"])
    for n_, (b_, t_) in res.items():
        say(f"   {n_:40s} coefficient {b_:+8.3f}  t {t_:+6.1f}")
    say(f"   ({len(z):,} stock-nights; coefficients per bps of input, except close-location: bps per full 0->1 range)")
    # same on the whole liquid market (not only candidates): sector spillover in general
    rr = raw.dropna(subset=["sec_ret"]).copy()
    rr["next_gap"] = rr.groupby("company_id")["gap"].shift(-1)
    rr = rr.dropna(subset=["next_gap"])
    rr = rr[rr["datetime"] >= c["buy_day"].min()]
    for col in ("next_gap", "sec_ret", "ret"):
        rr[col + "_d"] = rr[col] - rr.groupby("datetime")[col].transform("mean")
    res2 = ols(rr["next_gap_d"].to_numpy(), [rr["sec_ret_d"], rr["ret_d"]], ["sector move today", "own move today"])
    say("   whole liquid market (no ML): " + "; ".join(f"{n_} {b_:+.3f} (t {t_:+.1f})" for n_, (b_, t_) in res2.items()))

    # ---- T4 crowding / obviousness
    say("\nT4  ARE THE OBVIOUS GAP-UPS WORTH LESS?  realised gap per bps of predicted gap, top-40 candidates")
    z["fit"] = np.nan
    for lab, col in (("own move today", "ret"), ("close location (1 = closed at the high)", "clv")):
        q = pd.qcut(z[col], 3, labels=["low", "mid", "high"])
        parts = []
        for qq in ("low", "mid", "high"):
            w = z[q == qq]
            b_ = np.polyfit(w["pred_bps_d"], w["gross_d"], 1)[0]
            parts.append(f"{qq} {b_:.2f} (avg gap {w['gross'].mean():+.0f})")
        say(f"   by {lab:42s}: " + ", ".join(parts))
    cnt = c.dropna(subset=["sector"]).groupby(["buy_day", "sector"])["company_id"].transform("count")
    c.loc[cnt.index, "same_sector_in_list"] = cnt
    w = c.dropna(subset=["same_sector_in_list"])
    for lab, m in (("sector alone (1-2 in the top 40)", w["same_sector_in_list"] <= 2),
                   ("sector theme (5+ in the top 40)", w["same_sector_in_list"] >= 5)):
        q = w[m]
        say(f"   {lab:35s}: {len(q):6,d} stock-nights, predicted {q['pred_bps'].mean():+.0f}, realised {q['gross'].mean():+.0f}, "
            f"ratio {q['gross'].mean() / q['pred_bps'].mean():.2f}")

    # ---- T5 overnight cross-asset exposure
    say("\nT5  OVERNIGHT EXPOSURE AFTER WE BUY  (moves that happen between 15:40 and the next open)")
    mk = pd.read_parquet(home / "data" / "market.parquet")
    mk["datetime"] = pd.to_datetime(mk["datetime"]).astype("datetime64[ns]")
    mk = mk.sort_values("datetime").set_index("datetime")
    for a in ("sp500", "gold", "usd_inr", "crude_oil"):
        mk[a + "_r"] = np.log(mk[a] / mk[a].shift(1)) * 1e4
    t5 = top5.merge(mk[[a + "_r" for a in ("sp500", "gold", "usd_inr", "crude_oil")]], left_on="buy_day",
                    right_index=True, how="left")
    nn = t5.groupby("buy_day").agg(gross=("gross", "mean"), sp=("sp500_r", "first"), gold=("gold_r", "first"),
                                   fx=("usd_inr_r", "first"), oil=("crude_oil_r", "first")).dropna()
    say(f"   top-5 night average vs the same evening's move ({len(nn)} nights): correlation with US S&P 500 "
        f"{nn['gross'].corr(nn['sp']):+.2f}, gold {nn['gross'].corr(nn['gold']):+.2f}, USD/INR {nn['gross'].corr(nn['fx']):+.2f}, "
        f"crude {nn['gross'].corr(nn['oil']):+.2f}")
    bad = nn["sp"] < nn["sp"].quantile(0.1)
    say(f"   nights when the S&P fell most (worst 10%): top-5 average {nn.loc[bad, 'gross'].mean():+.0f} bps vs "
        f"{nn.loc[~bad, 'gross'].mean():+.0f} on the others")
    by = []
    for s, gg in t5.dropna(subset=["sector", "sp500_r", "gold_r"]).groupby("sector"):
        if len(gg) >= 60:
            by.append((s, len(gg), gg["gross"].mean(), gg["gross"].corr(gg["sp500_r"]), gg["gross"].corr(gg["gold_r"])))
    by = sorted(by, key=lambda x: -x[1])
    say(f"   {'sector':38s} {'picks':>5s} {'avg gap':>8s} {'corr S&P':>9s} {'corr gold':>9s}")
    for s, n_, m_, cs, cg in by[:12]:
        say(f"   {s[:38]:38s} {n_:5d} {m_:+8.0f} {cs:+9.2f} {cg:+9.2f}")
    (OUT / "report.txt").write_text("\n".join(L_), encoding="utf-8")
    c.to_parquet(OUT / "candidates_with_factors.parquet", index=False)
    say(f"\n-> {OUT / 'report.txt'}")


if __name__ == "__main__":
    main()
