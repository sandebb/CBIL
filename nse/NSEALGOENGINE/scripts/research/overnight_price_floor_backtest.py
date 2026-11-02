"""
Overnight trade - what if the Rs 50 price floor is lowered or dropped? Same trade, same sizing, same two sale rules
(sell at the open / the switched '75% to high, else at 12:00 [ML close > actual open]'), same charges and fills.

    python scripts\\research\\overnight_price_floor_backtest.py
    -> runs\\overnight_price_floor.txt, _summary.csv, _nightly.csv
       runs\\overnight_candidates_phase1_blend_p0.parquet   candidates with NO price floor (ranks 1..80, locked removed)

Floors tested: Rs 50 (today), Rs 20, none. Each floor's candidate list = the no-floor list with the cheaper stocks
removed and the ranks redone, so the three differ ONLY by the floor. The edge model is refitted on each list.
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
import overnight_sizing_backtest as B  # noqa: E402
import overnight_exit_backtest as X  # noqa: E402
import overnight_filter_backtest as F  # noqa: E402  (restricts X.RULES to open / 75%-12:00 / gated)

RUNS, log = B.RUNS, B.log
FLOORS = [50, 20, 0]
TOP, CAPITALS = [5, 9, 18], [1, 5, 20]
R_OPEN, R_GATED = X.BASE, F.R_GATED
X.RULES = {k: v for k, v in X.RULES.items() if k in (R_OPEN, R_GATED)}
F.CAPITALS = CAPITALS


def add_series(c: pd.DataFrame) -> pd.DataFrame:
    """series (EQ / BE / BZ) of each candidate on its buy day, from NSEDATAENGINE's fresh rebuild (raw_ohlcv in
    data_fresh has the series; the last known one is carried forward past its end). BE / BZ = trade-for-trade:
    every trade is settled by delivery, so shares bought tonight cannot be sold tomorrow morning (no BTST)."""
    home = B.L.data_engine_home(B.ROOT)
    f = home / "data_fresh" / "raw_ohlcv.parquet"
    try:
        sys.path.insert(0, str(home))
        import nse_contract as C
        f = C.file_path("raw_ohlcv", "fresh")
    except Exception:
        pass
    sr = pd.read_parquet(f, columns=["company_id", "datetime", "series"],
                         filters=[("datetime", ">=", c["buy_day"].min() - pd.Timedelta(days=30))])
    sr["datetime"] = pd.to_datetime(sr["datetime"]).astype("datetime64[ns]")
    sr = sr.sort_values("datetime")
    x = c.sort_values("buy_day")
    x = pd.merge_asof(x, sr.rename(columns={"datetime": "buy_day"}), on="buy_day", by="company_id", direction="backward")
    log(f"series on the buy day: {x['series'].value_counts(dropna=False).to_dict()} (fresh data through "
        f"{sr['datetime'].max():%Y-%m-%d})")
    return x


def main():
    t0 = time.time()
    t2t = "--exclude-t2t" in sys.argv
    sfx = "_t2t" if t2t else ""
    B.MIN_PRICE = 0.0
    c = B.candidates("phase1", "blend", 80)
    if B.TABLE.exists():
        B.L.build = lambda *x, **k: pd.read_parquet(B.TABLE)
    c, _ = B.liquidity(c)
    c = X.add_predictions(c, "phase1", "blend")
    nights = np.sort(c["buy_day"].unique())
    if t2t:
        c = add_series(c)
        n0 = len(c)
        c = c[c["series"].fillna("EQ") == "EQ"].copy()
        log(f"trade-for-trade (BE / BZ) removed: {n0 - len(c):,} of {n0:,} candidate stock-nights")
    lists = {}
    for fl in FLOORS:
        x = c[c["close"] >= fl].copy()
        x["rank"] = x.groupby("buy_day")["pred_bps"].rank(method="first", ascending=False).astype(int)
        lists[fl] = x[x["rank"] <= 40]
    post = lambda x: x[x["buy_day"] >= nights[B.BURN]]
    need = pd.concat([post(x[x["rank"] <= max(TOP)]) for x in lists.values()]).drop_duplicates(["company_id", "buy_day"])
    log(f"stock-nights to simulate: {len(need):,} ({100 * (need['close'] < 50).mean():.0f}% below Rs 50)")
    F.cache_bars(need[["company_id", "sell_day"]])
    sp = RUNS / "overnight_price_floor_exit_stats.parquet"            # cache: a run can be resumed after this step
    if sp.exists():
        st = pd.read_parquet(sp)
    else:
        st = X.exit_stats(need)
        st.to_parquet(sp, index=False)
    log(f"exit stats done ({time.time() - t0:.0f}s)")
    evs = []
    for fl, x in lists.items():
        hist = x[["buy_day", "pred_bps", "gross_bps"]]
        variants = {f"top {n}, floor {fl}": (x[x["rank"] <= n], n, False) for n in TOP}
        pos = F.positions(x, hist, variants, nights)
        evs.append(X.evaluate(pos, need, st))
        log(f"floor {fl} done ({time.time() - t0:.0f}s)")
    ev = pd.concat(evs, ignore_index=True)
    ev = ev.merge(need[["company_id", "buy_day", "close"]], on=["company_id", "buy_day"], how="left")
    s, nightly = X.summarize(ev.drop(columns=["close"]))
    S = s.set_index(["capital_lakh", "strategy", "rule"])
    f0 = lambda v: f"{v:+,.0f}"
    cheap = ev.assign(cb=np.where(ev["close"] < 50, ev["bv"], 0.0), cp=np.where(ev["close"] < 50, ev["pnl"], 0.0)) \
        .groupby(["capital_lakh", "strategy", "rule"])[["bv", "cb", "cp"]].sum()
    n_all = len(nights) - B.BURN
    base = nightly.set_index(["buy_day", "capital_lakh", "strategy", "rule"])["pnl"]
    L_ = [f"OVERNIGHT TRADE - LOWER OR DROP THE Rs 50 PRICE FLOOR   ({n_all} nights {nightly['buy_day'].min():%Y-%m-%d} .. "
          f"{nightly['buy_day'].max():%Y-%m-%d}; Rs per night after all charges)"
          + ("  - TRADE-FOR-TRADE (BE / BZ series) STOCKS EXCLUDED" if t2t else ""), ""]
    x0 = post(lists[0])
    for n in (5, 18):
        q = x0[x0["rank"] <= n]
        L_.append(f"With no floor, stocks below Rs 50 in the top {n}: {q.groupby('buy_day')['close'].apply(lambda v: (v < 50).sum()).mean():.1f} "
                  f"a night on average ({100 * (q['close'] < 50).mean():.0f}% of the top-{n} places)")
    pt = x0[x0["rank"] <= 5].assign(cheap=lambda d: d["close"] < 50)
    L_ += ["", "TOP 5 WITH NO FLOOR - what the stocks did (before costs and sizing):",
           f"  {'group':18s} {'stock-nights':>12s} {'predicted gap':>13s} {'actual gap':>10s} {'gap > 0':>8s} {'open->close':>11s}"]
    for lab, m in [("below Rs 50", pt["cheap"]), ("Rs 50 and above", ~pt["cheap"])]:
        q = pt[m]
        L_.append(f"  {lab:18s} {len(q):12,d} {q['pred_bps'].mean():+12.0f}b {q['gross_bps'].mean():+9.0f}b "
                  f"{100 * (q['gross_bps'] > 0).mean():7.0f}% {((q['c_next'] / q['o_next'] - 1) * 1e4).mean():+10.0f}b")
    L_.append("")
    for n in TOP:
        L_ += [f"TOP {n} at Rs 5 lakh",
               f"  {'floor':10s} {'sale':28s} | {'EQUAL':>7s} | {'OPTIMIZED':>9s} {'vs Rs 50':>8s} {'t':>5s} | "
               f"{'% money <Rs 50':>14s} {'their profit/night':>18s} {'worst night':>11s} {'nights up':>9s}"]
        for rule, rl in ((R_OPEN, "sell at open"), (R_GATED, "75%/12:00 + 09:07 check")):
            for fl in FLOORS:
                k_o, k_e = (5, f"OPTIMIZED | top {n}, floor {fl}", rule), (5, f"EQUAL | top {n}, floor {fl}", rule)
                o, e = S.loc[k_o], S.loc[k_e]
                ref = base.xs((5, f"OPTIMIZED | top {n}, floor 50", rule), level=[1, 2, 3])
                dd = base.xs(k_o[0:1] + k_o[1:], level=[1, 2, 3]) - ref
                t = dd.mean() / (dd.std(ddof=1) / np.sqrt(len(dd))) if fl != 50 and dd.std(ddof=1) > 0 else np.nan
                ch = cheap.loc[k_o]
                L_.append(f"  {'Rs ' + str(fl) if fl else 'none':10s} {rl:28s} | {f0(e['rs_per_night']):>7s} | "
                          f"{f0(o['rs_per_night']):>9s} {f0(dd.mean()):>8s} {t:5.1f} | {100 * ch['cb'] / ch['bv']:13.0f}% "
                          f"{f0(ch['cp'] / n_all):>18s} {f0(o['worst_night_rs']):>11s} {o['pct_nights_up']:8.0f}%")
        L_.append("")
    L_ += ["BY CAPITAL - OPTIMIZED top 5, Rs per night (sell at open / 75%-12:00 + 09:07 check):"]
    for fl in FLOORS:
        L_.append(f"  floor {'Rs ' + str(fl) if fl else 'none':7s} " + "   ".join(
            f"Rs {cl:2d} L: {f0(S.loc[(cl, f'OPTIMIZED | top 5, floor {fl}', R_OPEN), 'rs_per_night'])} / "
            f"{f0(S.loc[(cl, f'OPTIMIZED | top 5, floor {fl}', R_GATED), 'rs_per_night'])}" for cl in CAPITALS))
    L_ += ["", "BY YEAR - OPTIMIZED top 5, Rs 5 lakh, Rs per night (sell at open / 75%-12:00 + 09:07 check):"]
    y = nightly[nightly["capital_lakh"] == 5].assign(year=pd.to_datetime(nightly["buy_day"]).dt.year)
    for fl in FLOORS:
        parts = []
        for yr in sorted(y["year"].unique()):
            a = y[(y["year"] == yr) & (y["strategy"] == f"OPTIMIZED | top 5, floor {fl}")]
            parts.append(f"{yr}: {f0(a.loc[a['rule'] == R_OPEN, 'pnl'].mean())} / {f0(a.loc[a['rule'] == R_GATED, 'pnl'].mean())}")
        L_.append(f"  floor {'Rs ' + str(fl) if fl else 'none':7s} " + "   ".join(parts))
    L_ += ["", "vs Rs 50 = Rs per night more (+) or less (-) than the same sale with today's Rs 50 floor; t = that difference / "
           "its standard error (|t| > 2 = unlikely to be luck).",
           "% money <Rs 50 = share of the rupees bought that went into stocks below Rs 50; their profit/night = what those "
           "stocks earned after charges.",
           "Same fills, auction impact, limit rules and Shoonya charges as the other overnight backtests. The limit target "
           "must trade a full 0.05 above it - on a Rs 20 stock that is 0.25%, so cheap stocks' limit fills are conservative."]
    text = "\n".join(L_)
    print(text)
    (RUNS / f"overnight_price_floor{sfx}.txt").write_text(text, encoding="utf-8")
    s.to_csv(RUNS / f"overnight_price_floor{sfx}_summary.csv", index=False)
    nightly.to_csv(RUNS / f"overnight_price_floor{sfx}_nightly.csv", index=False)
    log(f"written runs\\overnight_price_floor.txt ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
