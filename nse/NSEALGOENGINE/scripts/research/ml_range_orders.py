"""
Test: one BUY limit and one SELL limit per stock per day, at NSEMLENGINE's predicted low and high (intraday, MIS).

Both orders rest from the open. If both fill, the trade is round-tripped whatever the order of events (bought low /
sold high, or sold high / bought back low): P&L = sell - buy. If only one fills, the position is closed at the
day's close (intraday square-off). Fills: at the open if the market opens through the limit, else only if the day's
low (high) trades THROUGH the limit by one tick - touching is not counted (queue risk). Rs 1 lakh per stock,
Shoonya intraday charges. Compared with a naive band of the same kind around the previous close (no ML), so
we see what NSEMLENGINE adds.

    python scripts\\research\\ml_range_orders.py
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import glob
import os
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

ML = Path(os.environ.get("NSEMLENGINE_HOME", __import__("os").environ.get("NSE_ROOT", r"D:\CBIL") + r"/code\NSEMLENGINE")) / "work" / "predictions"
RAW = Path(os.environ.get("NSEDATAENGINE_DATA_DIR", __import__("os").environ.get("NSE_ROOT", r"D:\CBIL") + r"/code\NSEDATAENGINE\data")) / "raw_ohlcv.parquet"
VALUE = 100_000


def charges(value=VALUE):
    brk = 2 * min(5.0, value * 0.0003)
    txn, sebi = 2 * value * 0.0000307, 2 * value * 1e-6
    return brk + value * 0.00025 + txn + sebi + value * 0.00003 + 0.18 * (brk + txn + sebi)


def tick(p):
    return 0.01 if p < 100 else 0.05


def simulate(d: pd.DataFrame, buy: np.ndarray, sell: np.ndarray) -> pd.DataFrame:
    o, h, l, c = (d[k].to_numpy() for k in ("open", "high", "low", "close"))
    tk = np.where(c < 100, 0.01, 0.05)
    ok = np.isfinite(buy) & np.isfinite(sell) & (sell > buy) & (buy > 0)
    bfill = np.where(o <= buy, o, np.where(l <= buy - tk, buy, np.nan))
    sfill = np.where(o >= sell, o, np.where(h >= sell + tk, sell, np.nan))
    bf, sf = np.isfinite(bfill) & ok, np.isfinite(sfill) & ok
    entry = np.where(bf, bfill, np.where(sf, sfill, np.nan))
    pnl_ps = np.where(bf & sf, sfill - bfill, np.where(bf, c - bfill, np.where(sf, sfill - c, 0.0)))
    traded = bf | sf
    qty = np.where(traded, np.floor(VALUE / np.where(traded, entry, 1)), 0)
    gross = pnl_ps * qty
    net = gross - np.where(traded, charges(), 0.0)
    return pd.DataFrame({"session": d["session"].to_numpy(), "company_id": d["company_id"].to_numpy(),
                         "both": bf & sf, "buy_only": bf & ~sf, "sell_only": sf & ~bf, "traded": traded,
                         "gross": gross, "net": net})


def summarise(name, r: pd.DataFrame) -> str:
    t = r[r.traded]
    daily = r.groupby("session")["net"].sum()
    per = t["net"] / VALUE * 1e4
    tstat = daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily))) if len(daily) > 2 and daily.std() > 0 else np.nan
    return (f"{name:34s} {len(t) / r.session.nunique():7.0f} {100 * t.both.mean():6.0f}% {100 * t.buy_only.mean():5.0f}% "
            f"{100 * t.sell_only.mean():5.0f}%  {per.mean():+7.1f} {100 * (t.net > 0).mean():5.0f}%  "
            f"{daily.mean():+10,.0f} {daily.min():+10,.0f} {daily.max():+10,.0f}  {tstat:+5.1f}  "
            + " ".join(f"{v:+.0f}" for v in daily.to_numpy()))


def main():
    preds = []
    for f in sorted(glob.glob(str(ML / "predictions_2*.csv"))):
        p = pd.read_csv(f)
        preds.append(p)
    P = pd.concat(preds, ignore_index=True)
    P["date"] = pd.to_datetime(P["date"])
    raw = pl.scan_parquet(RAW).filter(pl.col("datetime") >= pl.lit(P["date"].min())) \
        .select("company_id", "datetime", "open", "high", "low", "close").collect().to_pandas()
    raw["datetime"] = pd.to_datetime(raw["datetime"]).dt.normalize()
    days = sorted(raw["datetime"].unique())
    nxt = {d: days[i + 1] for i, d in enumerate(days[:-1])}
    P["session"] = P["date"].map(lambda d: nxt.get(pd.Timestamp(d)))
    D = P.merge(raw.rename(columns={"datetime": "session"}), on=["company_id", "session"], how="inner")
    D = D[(D.close_today > 20)]
    liq = D.groupby("session")["median_turnover_60d"].rank(ascending=False) <= 300
    out = [f"NSEMLENGINE range orders - {D.session.nunique()} sessions "
           f"({D.session.min():%d %b} .. {D.session.max():%d %b %Y}), Rs 1 lakh per stock, Shoonya charges "
           f"Rs {charges():.0f} per round trip ({charges() / VALUE * 1e4:.1f} bps)", ""]
    hdr = (f"{'variant':34s} {'trades/day':>7s} {'both':>7s} {'buy1':>6s} {'sell1':>6s}  {'bps/trade':>7s} {'win':>6s}  "
           f"{'Rs/day avg':>10s} {'worst day':>10s} {'best day':>10s}  {'t':>5s}  Rs per day")
    top100 = D.groupby("session")["median_turnover_60d"].rank(ascending=False) <= 100
    for uname, U in (("TOP 300 by turnover", D[liq]), ("TOP 100 by turnover", D[top100])):
        out += [f"== {uname}  ({len(U) / U.session.nunique():.0f} stocks/day)", hdr]
        c0 = (U.low_p50 + U.high_p50) / 2
        half = (U.high_p50 - U.low_p50) / 2
        for name, b, s in (
                ("ML  low_p50 / high_p50", U.low_p50, U.high_p50),
                ("ML  low_p10 / high_p90 (wide)", U.low_p10, U.high_p90),
                ("ML  p50 band widened x1.3", c0 - 1.3 * half, c0 + 1.3 * half),
                ("ML  p50 band widened x1.7", c0 - 1.7 * half, c0 + 1.7 * half)):
            out.append(summarise(name, simulate(U, b.to_numpy(), s.to_numpy())))
        sig = U.daily_vol_pct / 100 * U.close_today
        for k in (0.3, 0.5, 0.75, 1.0):
            out.append(summarise(f"naive prev close +/- {k} x daily vol", simulate(
                U, (U.close_today - k * sig).to_numpy(), (U.close_today + k * sig).to_numpy())))
        out.append("")
        r = simulate(U, U.low_p50.to_numpy(), U.high_p50.to_numpy())
        r["kind"] = np.where(r.both, "both", np.where(r.buy_only, "buy only", "sell only"))
        g = r[r.traded]
        out.append("   ML p50 by fill type, bps/trade: " + ", ".join(
            f"{k} {v / VALUE * 1e4:+.1f} (n={n})" for k, v, n in
            zip(*[g.groupby("kind")["net"].mean().index, g.groupby("kind")["net"].mean().values, g.groupby("kind").size().values])))
        out.append("")
    out += ["both = both orders filled (range captured); buy1 / sell1 = only one side filled, closed at the close.",
            "bps/trade = average net return per traded stock-day after charges; t = mean daily P&L / its standard error",
            "(|t| > 2 = unlikely to be luck). Daily OHLC cannot show slippage or partial fills: live results will be worse."]
    text = "\n".join(out)
    print(text)
    Path(ML.parent / "range_orders_test.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
