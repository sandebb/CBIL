"""
Top-N test: rank each day's candidates and trade only the best N (N = 1 .. 300). How do net results change as N grows?

Universe each day (point in time, known the evening before): 60-day median turnover >= Rs 25 cr, price >= Rs 50, not
locked at the price limit in the last 20 sessions. Trades: open-to-close at the opening auction (daily bars, 2019 ->),
Rs 1 lakh per stock, net of Shoonya charges + 5 bps slippage (9.7 bps per trade).

Rankers (all known at or before the open)
  RSI14_SHORT   short; rank = yesterday's RSI-14, highest first
  RSI2_SHORT    short; rank = yesterday's 2-day Wilder RSI, highest first
  GAPDN_LONG    long;  rank = today's opening gap vs yesterday's close, most negative first (known at the 09:08 auction)
  GAPUP_SHORT   short; rank = opening gap, most positive first
  RANDOM        long and short at random: the baseline any ranking must beat

    python scripts\\research\\rank_topn_test.py           (-> runs\\rank_topn_test.txt + rank_topn_curve.csv)
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import os
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("NSEDATAENGINE_DATA_DIR", __import__("os").environ.get("NSE_ROOT", r"D:\CBIL") + r"/code\NSEDATAENGINE\data"))
COST = 9.7
SHOW = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20, 25, 30, 40, 50, 75, 100, 150, 200, 250, 300]


def load(start="2019-01-01") -> pl.DataFrame:
    s = pl.lit(start).str.to_date().cast(pl.Datetime("ns"))
    raw = (pl.scan_parquet(DATA / "raw_ohlcv.parquet").filter(pl.col("datetime") >= s - pl.duration(days=200))
           .select("company_id", "datetime", "open", "high", "low", "close", "volume").collect())
    rsi = (pl.scan_parquet(DATA / "features_output_clean.parquet").filter(pl.col("datetime") >= s - pl.duration(days=200))
           .select("company_id", "datetime", "rsi_14").collect())
    d = raw.join(rsi, on=["company_id", "datetime"], how="left").sort("company_id", "datetime")
    chg = pl.col("close").diff().over("company_id")
    up = chg.clip(lower_bound=0).ewm_mean(alpha=0.5, adjust=False).over("company_id")
    dn = (-chg).clip(lower_bound=0).ewm_mean(alpha=0.5, adjust=False).over("company_id")
    d = d.with_columns((100 - 100 / (1 + up / dn)).alias("rsi_2"),
                       (pl.col("close") * pl.col("volume")).rolling_median(60, min_samples=40).over("company_id").alias("turn"),
                       (pl.col("high") == pl.col("low")).cast(pl.Int32).rolling_sum(20, min_samples=1).over("company_id").alias("locked20"))
    d = d.with_columns([pl.col(c).shift(1).over("company_id").alias(f"{c}_y")
                        for c in ("rsi_14", "rsi_2", "turn", "close", "locked20")])
    d = d.with_columns(((pl.col("close") / pl.col("open") - 1) * 1e4).alias("oc"),
                       ((pl.col("open") / pl.col("close_y") - 1) * 1e4).alias("gap"))
    return d.filter((pl.col("datetime") >= s) & (pl.col("turn_y") >= 25e7) & (pl.col("close_y") >= 50)
                    & (pl.col("locked20_y").fill_null(0) == 0) & pl.col("oc").is_finite() & (pl.col("oc").abs() < 2000)
                    & pl.col("gap").is_finite())


def curve(d: pl.DataFrame, score: pl.Expr, side: int, name: str, filt: pl.Expr | None = None) -> pl.DataFrame:
    x = d.filter(filt) if filt is not None else d
    x = x.with_columns(score.alias("score")).filter(pl.col("score").is_not_null())
    x = x.with_columns(pl.col("score").rank("ordinal", descending=True).over("datetime").alias("rk"),
                       (side * pl.col("oc") - COST).alias("net"))
    days = d["datetime"].n_unique()
    rows = []
    for n in range(1, 301):
        y = x.filter(pl.col("rk") <= n)
        daily = y.group_by("datetime").agg(pl.col("net").mean().alias("avg"), pl.col("net").sum().alias("rs_k"),
                                           pl.len().alias("k"))
        if daily.height < 20:
            break
        a = daily["avg"].to_numpy()
        rows.append({"ranker": name, "N": n, "trades_per_day": y.height / days, "days_traded": daily.height,
                     "bps_per_trade": y["net"].mean(), "win_trade_pct": 100 * (y["net"] > 0).mean(),
                     "win_day_pct": 100 * (a > 0).mean(), "rs_per_day": daily["rs_k"].sum() * 10 / days,
                     "t": a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if a.std() > 0 else np.nan,
                     "worst_day_rs": daily["rs_k"].min() * 10})
    return pl.DataFrame(rows)


def main():
    d = load()
    rng = np.random.default_rng(7)
    d = d.with_columns(pl.Series("rnd", rng.random(d.height)), pl.Series("rside", rng.choice([-1, 1], d.height)))
    parts = [curve(d, pl.col("rsi_14_y"), -1, "RSI14_SHORT", pl.col("rsi_14_y") > 60),
             curve(d, pl.col("rsi_2_y"), -1, "RSI2_SHORT", pl.col("rsi_2_y") > 75),
             curve(d, -pl.col("gap"), 1, "GAPDN_LONG", pl.col("gap") < 0),
             curve(d, pl.col("gap"), -1, "GAPUP_SHORT", pl.col("gap") > 0)]
    rnd = d.with_columns((pl.col("oc") * pl.col("rside")).alias("oc"))
    parts.append(curve(rnd, pl.col("rnd"), 1, "RANDOM"))
    R = pl.concat(parts)
    R.write_csv(ROOT / "runs" / "rank_topn_curve.csv") if (ROOT / "runs").exists() else None
    out = [f"Top-N ranking test, {d['datetime'].min():%Y-%m-%d} .. {d['datetime'].max():%Y-%m-%d}, "
           f"{d['datetime'].n_unique()} sessions, open-to-close at the opening auction, Rs 1 lakh per stock, net of "
           f"{COST} bps. Universe: 60-day median turnover >= Rs 25 cr, price >= Rs 50, no price-limit lock in 20 sessions.", ""]
    for name, g in R.group_by("ranker", maintain_order=True):
        out.append(f"{name[0]}")
        out.append(f"   {'top N':>5s} {'trades/day':>10s} {'bps/trade':>9s} {'win trades':>10s} {'win days':>8s} "
                   f"{'Rs/day':>9s} {'worst day':>10s} {'t':>6s}")
        for r in g.filter(pl.col("N").is_in(SHOW)).iter_rows(named=True):
            out.append(f"   {r['N']:5d} {r['trades_per_day']:10.1f} {r['bps_per_trade']:+9.1f} {r['win_trade_pct']:9.0f}% "
                       f"{r['win_day_pct']:7.0f}% {r['rs_per_day']:+9,.0f} {r['worst_day_rs']:+10,.0f} {r['t']:+6.1f}")
        out.append("")
    out += ["bps/trade = average net return per trade; Rs/day = average daily P&L with Rs 1 lakh in each of the top N",
            "(fewer trades on days with fewer candidates); t = average daily return / its standard error (|t| > 2 = unlikely luck).",
            "Daily bars: entry at the opening-auction price, exit at the close, no stop. Full curve N = 1..300: runs\\rank_topn_curve.csv"]
    text = "\n".join(out)
    print(text)
    (ROOT / "runs").mkdir(exist_ok=True)
    R.write_csv(ROOT / "runs" / "rank_topn_curve.csv")
    (ROOT / "runs" / "rank_topn_test.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
