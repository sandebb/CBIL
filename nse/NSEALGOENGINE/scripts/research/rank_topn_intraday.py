"""
Top-N ranking test with entries we can actually get ("option 2").

The opening price is fixed in the 09:00-09:08 pre-open auction, so the gap is known around 09:08 - but by then the
auction is closed and nobody can trade at that price any more. This test ranks on the real opening gap and then enters
at prices that are still available after 09:08, using 1-minute bars (data/history.db, from `nsealgo fetch-history`).

Entries (all exit at the 14:55 bar's close)
  AUCTION   the opening price itself (09:15 bar open) - NOT tradeable after 09:08, kept as the upper bound
  09:15     the 09:15 bar's close  = order sent 09:08-09:15, filled in the first minute of normal trading
  09:16 / 09:20 / 09:30 / 09:45   that bar's open
  DAILY     daily-bar open -> close, for comparison with rank_topn_test.py over the same days
Rankers (score; highest = rank 1)
  GAPUP_SHORT      gap > 0, score gap                     short
  GAPDN_LONG       gap < 0, score -gap                    long
  GAPUP_ATR_SHORT  gap > 0, score gap / ATR%  (gap measured in "normal days of movement" for that stock)
  GAPDN_ATR_LONG   gap < 0, score -gap / ATR%
  RSI14_SHORT      yesterday's RSI-14 > 60, score RSI     short
  RSI2_SHORT       yesterday's 2-day RSI > 75, score RSI  short
  RANDOM           random score and side (baseline)
Universe each day ('clean', point in time - see opening_window_test.signals): 60-day median turnover >= Rs 25 cr,
price >= Rs 50, no price-limit lock in 20 sessions, promoter pledge <= 50%, not on ASM/GSM that day.
Costs per trade: Shoonya 4.7 bps + entry slippage (2 bps auction, 5 bps otherwise) + 2.5 bps exit. Rs 1 lakh per stock.
Also prints the daily-bar GAPUP / GAPDN top-15 result by year since 2019, to show whether the minute-data months are
a normal or an unusual period.

    .venv\\Scripts\\python scripts\\research\\rank_topn_intraday.py
    -> runs\\rank_topn_intraday.txt, runs\\rank_topn_intraday_curve.csv
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))
from opening_window_test import DATA, signals  # noqa: E402
from nsealgo.config import load_config, resolve_path  # noqa: E402

CHARGES, EXIT_SLIP = 4.7, 2.5
MINUTES = ("09:15", "09:16", "09:20", "09:30", "09:45", "14:55")
ENTRIES = ("AUCTION", "09:15", "09:16", "09:20", "09:30", "09:45", "DAILY")
SHOW = (1, 2, 3, 5, 8, 10, 15, 20, 30, 50, 100, 300)
NMAX = 300


def minute_snapshot(db: Path) -> pd.DataFrame:
    """Only the bars we need (a few per stock-day), straight from SQLite: one row per stock and day."""
    c = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    q = ("SELECT key, substr(ts,1,10) AS d, substr(ts,12,5) AS hm, open, close FROM minute "
         f"WHERE substr(ts,12,5) IN ({','.join('?' * len(MINUTES))})")
    m = pd.read_sql_query(q, c, params=MINUTES)
    c.close()
    w = m.pivot_table(index=["key", "d"], columns="hm", values=["open", "close"], aggfunc="first")
    w.columns = [f"{a}_{b}" for a, b in w.columns]
    w = w.reset_index()
    w["company_id"] = w["key"].str.split(":").str[1]
    w["date"] = pd.to_datetime(w["d"]).dt.date
    return w


def rsi2(px: pd.DataFrame) -> pd.Series:
    chg = px.groupby("company_id")["close"].diff()
    up = chg.clip(lower=0).groupby(px["company_id"]).transform(lambda x: x.ewm(alpha=0.5, adjust=False).mean())
    dn = (-chg).clip(lower=0).groupby(px["company_id"]).transform(lambda x: x.ewm(alpha=0.5, adjust=False).mean())
    return 100 - 100 / (1 + up / dn)


def build(w: pd.DataFrame, start, end) -> pd.DataFrame:
    sig = signals(["NSE:" + s for s in w["company_id"].unique()], start, end).reset_index()
    since = pd.Timestamp(start) - pd.Timedelta(days=60)
    px = pd.read_parquet(DATA / "raw_ohlcv.parquet", columns=["company_id", "datetime", "open", "close"],
                         filters=[("datetime", ">=", since), ("company_id", "in", list(w["company_id"].unique()))])
    px["company_id"] = px["company_id"].astype(str)
    px = px.sort_values(["company_id", "datetime"])
    px["rsi2"] = rsi2(px)
    px["rsi2_y"] = px.groupby("company_id")["rsi2"].shift(1)
    px["date"] = pd.to_datetime(px["datetime"]).dt.date
    px = px.rename(columns={"open": "d_open", "close": "d_close"})[["company_id", "date", "d_open", "d_close", "rsi2_y"]]
    sig["company_id"] = sig["company_id"].astype(str)
    x = w.merge(sig, on=["company_id", "date"], how="inner").merge(px, on=["company_id", "date"], how="left")
    x = x[x["clean"] & x["open_09:15"].notna() & x["close_14:55"].notna() & (x["prev_close"] > 0)].copy()
    x["gap"] = x["open_09:15"] / x["prev_close"] - 1
    x["atr_pct"] = x["atr_y"] / x["prev_close"]
    x["gap_atr"] = x["gap"] / x["atr_pct"].where(x["atr_pct"] > 0)
    # raw (before cost) entry -> exit returns for a long, in bps
    ex = x["close_14:55"]
    ent = {"AUCTION": x["open_09:15"], "09:15": x["close_09:15"], "09:16": x["open_09:16"], "09:20": x["open_09:20"],
           "09:30": x["open_09:30"], "09:45": x["open_09:45"]}
    for k, e in ent.items():
        x[f"r_{k}"] = (ex / e - 1) * 1e4
    x["r_DAILY"] = (x["d_close"] / x["d_open"] - 1) * 1e4
    for k in ENTRIES:
        x.loc[x[f"r_{k}"].abs() > 2000, f"r_{k}"] = np.nan          # bad prints
    return x


def rankers(x: pd.DataFrame):
    rng = np.random.default_rng(7)
    x = x.assign(rnd=rng.random(len(x)), rside=rng.choice([-1, 1], len(x)))
    return x, [("GAPUP_SHORT", x["gap"].where(x["gap"] > 0), -1),
               ("GAPDN_LONG", (-x["gap"]).where(x["gap"] < 0), 1),
               ("GAPUP_ATR_SHORT", x["gap_atr"].where(x["gap"] > 0), -1),
               ("GAPDN_ATR_LONG", (-x["gap_atr"]).where(x["gap"] < 0), 1),
               ("RSI14_SHORT", x["rsi_y"].where(x["rsi_y"] > 60), -1),
               ("RSI2_SHORT", x["rsi2_y"].where(x["rsi2_y"] > 75), -1),
               ("RANDOM", x["rnd"], 0)]


def curves(x: pd.DataFrame) -> pd.DataFrame:
    """All N at once: per ranker and entry, a days x rank-position matrix of net returns, then running sums."""
    x, rk = rankers(x)
    days = np.sort(x["date"].unique())
    ndays = len(days)
    di = pd.Series(np.arange(ndays), index=days)
    rows = []
    for name, score, side in rk:
        y = x.assign(score=score).dropna(subset=["score"])
        y = y.assign(rk=y.groupby("date")["score"].rank(method="first", ascending=False).astype(int))
        y = y[y["rk"] <= NMAX]
        sd = (y["rside"] if side == 0 else side)
        width = int(y["rk"].max())
        for e in ENTRIES:
            slip = 2.0 if e in ("AUCTION", "DAILY") else 5.0
            net = (sd * y[f"r_{e}"] - CHARGES - slip - EXIT_SLIP).to_numpy()
            M = np.full((ndays, width), np.nan)
            M[di[y["date"]].to_numpy(), y["rk"].to_numpy() - 1] = net
            ok = ~np.isnan(M)
            S = np.cumsum(np.where(ok, M, 0.0), axis=1)            # Rs-per-lakh-ish running sums per day
            C = np.cumsum(ok, axis=1)
            W = np.cumsum(ok & (M > 0), axis=1)
            for n in range(1, width + 1):
                c, s_ = C[:, n - 1], S[:, n - 1]
                live = c > 0
                if not live.any():
                    continue
                a = s_[live] / c[live]
                tot = c.sum()
                sdv = a.std(ddof=1) if len(a) > 2 else 0
                rows.append((name, e, n, tot / ndays, s_.sum() / tot, 100 * W[:, n - 1].sum() / tot,
                             100 * (a > 0).mean(), s_.sum() * 10 / ndays, s_[live].min() * 10,
                             a.mean() / (sdv / np.sqrt(len(a))) if sdv > 0 else np.nan))
    return pd.DataFrame(rows, columns=["ranker", "entry", "N", "trades_per_day", "bps_per_trade", "win_trade_pct",
                                       "win_day_pct", "rs_per_day", "worst_day_rs", "t"])


def by_year(top=15) -> pd.DataFrame:
    """Daily bars since 2019: GAPUP_SHORT / GAPDN_LONG top-15 open->close per year (same universe as rank_topn_test)."""
    d = pd.read_parquet(DATA / "raw_ohlcv.parquet", columns=["company_id", "datetime", "open", "high", "low", "close", "volume"],
                        filters=[("datetime", ">=", pd.Timestamp("2018-06-01"))])
    d = d.sort_values(["company_id", "datetime"])
    g = d.groupby("company_id")
    d["turn_y"] = d["close"] * d["volume"]
    d["turn_y"] = d.groupby("company_id")["turn_y"].transform(lambda s: s.rolling(60, min_periods=40).median().shift(1))
    d["lock_y"] = (d["high"] == d["low"]).astype(float).groupby(d["company_id"]).transform(
        lambda s: s.rolling(20, min_periods=1).sum().shift(1))
    d["pc"] = g["close"].shift(1)
    d = d[(d["datetime"] >= "2019-01-01") & (d["turn_y"] >= 25e7) & (d["pc"] >= 50) & (d["lock_y"].fillna(0) == 0)]
    d["gap"] = d["open"] / d["pc"] - 1
    d["oc"] = (d["close"] / d["open"] - 1) * 1e4
    d = d[d["oc"].abs() < 2000]
    out = []
    for name, sel, score, side in (("GAPUP_SHORT", d["gap"] > 0, d["gap"], -1), ("GAPDN_LONG", d["gap"] < 0, -d["gap"], 1)):
        y = d[sel].assign(score=score[sel])
        y = y[y.groupby("datetime")["score"].rank(method="first", ascending=False) <= top]
        y = y.assign(net=side * y["oc"] - CHARGES - 2.0 - EXIT_SLIP, yr=y["datetime"].dt.year)
        out.append(y.groupby("yr")["net"].mean().rename(name))
    return pd.concat(out, axis=1)


def main():
    cfg = load_config(ROOT / "config" / "system.yaml")
    db = resolve_path(cfg, "data_dir") / "history.db"
    cache = ROOT / "runs" / "rank_topn_intraday_bars.parquet"
    if cache.exists() and cache.stat().st_mtime > db.stat().st_mtime:
        w = pd.read_parquet(cache)
    else:
        print("reading minute bars (one pass over history.db, cached afterwards) ...", flush=True)
        w = minute_snapshot(db)
        (ROOT / "runs").mkdir(exist_ok=True)
        w.to_parquet(cache, index=False)
    start, end = w["date"].min(), w["date"].max()
    print(f"  {len(w):,} stock-days, {w['company_id'].nunique()} stocks, {start} .. {end}", flush=True)
    x = build(w, start, end)
    print(f"  {len(x):,} stock-days in the clean universe; ranking ...", flush=True)
    R = curves(x)
    ndays = x["date"].nunique()
    out = [f"Top-N with tradeable entries: {ndays} sessions ({start} .. {end}), clean point-in-time universe, "
           f"exit 14:55, Rs 1 lakh per stock, net of charges + slippage", ""]
    for name, g in R.groupby("ranker", sort=False):
        out.append(f"{name}   (bps per trade, t in brackets; AUCTION is not tradeable once the gap is known)")
        out.append(f"   {'top N':>5s} {'trades/d':>8s} " + " ".join(f"{e:>14s}" for e in ENTRIES))
        for n in SHOW:
            r = g[g["N"] == n]
            if r.empty:
                continue
            cells = []
            for e in ENTRIES:
                c = r[r["entry"] == e]
                cells.append(f"{c['bps_per_trade'].iloc[0]:+6.1f} ({c['t'].iloc[0]:+4.1f})" if len(c) else "-")
            tpd = r["trades_per_day"].iloc[0]
            out.append(f"   {n:5d} {tpd:8.1f} " + " ".join(f"{c:>14s}" for c in cells))
        r15 = g[(g["N"] == 15) & (g["entry"] == "09:16")]
        if len(r15):
            r15 = r15.iloc[0]
            out.append(f"   top 15 entered 09:16: Rs {r15.rs_per_day:+,.0f}/day, winning days {r15.win_day_pct:.0f}%, "
                       f"worst day Rs {r15.worst_day_rs:+,.0f}")
        out.append("")
    try:
        Y = by_year()
        out.append("Daily bars, top 15 open->close by year (net bps per trade) - is the minute-data period typical?")
        out.append("   year " + " ".join(f"{c:>12s}" for c in Y.columns))
        for yr, r in Y.iterrows():
            out.append(f"   {yr} " + " ".join(f"{v:+12.1f}" for v in r))
        out.append("")
    except Exception as exc:  # noqa: BLE001
        out.append(f"(by-year table skipped: {exc})")
    out.append("t = average daily return / its standard error (|t| > 2 = unlikely to be luck). "
               "Full curves N = 1..300 per entry: runs\\rank_topn_intraday_curve.csv")
    text = "\n".join(out)
    print(text)
    (ROOT / "runs").mkdir(exist_ok=True)
    R.to_csv(ROOT / "runs" / "rank_topn_intraday_curve.csv", index=False)
    (ROOT / "runs" / "rank_topn_intraday.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
