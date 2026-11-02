"""
RSI as an intraday stock selector: yesterday's RSI (14-day from NSEDATAENGINE's features, and a 2-day Wilder RSI
computed here) -> today's open-to-close return. Long = buy at the open, sell at the close; short = the reverse.
Liquid stocks only (top 300 by 60-day median turnover each day). Net = after Shoonya intraday charges (4.7 bps on
Rs 1 lakh) and 5 bps assumed slippage for the open and close auctions.

    python scripts\\research\\rsi_intraday_selection.py [--start 2019-01-01]
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import os
from pathlib import Path

import numpy as np
import polars as pl

DATA = Path(os.environ.get("NSEDATAENGINE_DATA_DIR", __import__("os").environ.get("NSE_ROOT", r"D:\CBIL") + r"/code\NSEDATAENGINE\data"))
COST_BPS = 4.7 + 5.0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--top", type=int, default=300)
    a = ap.parse_args(argv)
    start = pl.lit(a.start).str.to_date()
    raw = (pl.scan_parquet(DATA / "raw_ohlcv.parquet")
           .filter(pl.col("datetime") >= start.cast(pl.Datetime("ns")) - pl.duration(days=200))
           .select("company_id", "datetime", "open", "high", "low", "close", "volume").collect())
    rsi = (pl.scan_parquet(DATA / "features_output_clean.parquet")
           .filter(pl.col("datetime") >= start.cast(pl.Datetime("ns")) - pl.duration(days=200))
           .select("company_id", "datetime", "rsi_14").collect())
    d = raw.join(rsi, on=["company_id", "datetime"], how="left").sort("company_id", "datetime")
    chg = pl.col("close").diff().over("company_id")
    up = chg.clip(lower_bound=0).ewm_mean(alpha=0.5, adjust=False).over("company_id")
    dn = (-chg).clip(lower_bound=0).ewm_mean(alpha=0.5, adjust=False).over("company_id")
    d = d.with_columns((100 - 100 / (1 + up / dn)).alias("rsi_2"),
                       (pl.col("close") * pl.col("volume")).rolling_median(60).over("company_id").alias("turn"))
    d = d.with_columns([pl.col(c).shift(1).over("company_id").alias(f"{c}_y") for c in ("rsi_14", "rsi_2", "turn", "close")])
    d = d.with_columns(((pl.col("close") / pl.col("open") - 1) * 1e4).alias("oc_bps"),
                       ((pl.col("open") / pl.col("close_y") - 1) * 1e4).alias("gap_bps"))
    d = d.filter((pl.col("datetime") >= start.cast(pl.Datetime("ns"))) & pl.col("rsi_14_y").is_not_null()
                 & (pl.col("close_y") > 20) & pl.col("oc_bps").is_finite() & (pl.col("oc_bps").abs() < 2000))
    d = d.with_columns(pl.col("turn_y").rank(descending=True).over("datetime").alias("liq")).filter(pl.col("liq") <= a.top)
    d = d.with_columns((pl.col("oc_bps") - pl.col("oc_bps").mean().over("datetime")).alias("oc_excess"),
                       pl.col("datetime").dt.year().alias("year"))
    n_days = d["datetime"].n_unique()
    out = [f"RSI intraday selection: top {a.top} liquid stocks, {d['datetime'].min():%Y-%m-%d} .. {d['datetime'].max():%Y-%m-%d}, "
           f"{n_days} sessions, {d.height:,} stock-days. Net = minus {COST_BPS:.1f} bps per round trip.", ""]

    def table(col, edges, label):
        out.append(f"{label}: yesterday's value -> today's open-to-close (bps)")
        out.append(f"   {'bucket':>10s} {'stock-days':>11s} {'per day':>8s} {'avg o->c':>9s} {'vs market':>10s} "
                   f"{'long net':>9s} {'short net':>10s} {'t(vs mkt)':>9s}  avg o->c by year")
        b = d.with_columns(pl.col(col).cut(edges).alias("b"))
        for key, g in sorted(b.group_by("b"), key=lambda kv: str(kv[0][0])):
            x, ex = g["oc_bps"].to_numpy(), g["oc_excess"].to_numpy()
            daily = g.group_by("datetime").agg(pl.col("oc_excess").mean())["oc_excess"].to_numpy()
            t = daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily))) if len(daily) > 2 else np.nan
            yrs = g.group_by("year").agg(pl.col("oc_bps").mean()).sort("year")
            out.append(f"   {str(key[0]):>10s} {len(x):11,d} {len(x) / n_days:8.1f} {x.mean():+9.1f} {ex.mean():+10.1f} "
                       f"{x.mean() - COST_BPS:+9.1f} {-x.mean() - COST_BPS:+10.1f} {t:+9.1f}  "
                       + " ".join(f"{y}:{v:+.0f}" for y, v in zip(yrs["year"], yrs["oc_bps"])))
        out.append("")

    table("rsi_14_y", [20, 30, 40, 60, 70, 80], "RSI-14")
    table("rsi_2_y", [5, 10, 25, 75, 90, 95], "RSI-2 (Wilder)")
    gap_dn = d.filter(pl.col("gap_bps") < -100)
    out.append("RSI-14 < 30 AND opening gap down > 1%: "
               f"{gap_dn.filter(pl.col('rsi_14_y') < 30).height:,} stock-days, avg o->c "
               f"{gap_dn.filter(pl.col('rsi_14_y') < 30)['oc_bps'].mean():+.1f} bps "
               f"(all gap-downs > 1%: {gap_dn['oc_bps'].mean():+.1f} bps)")
    out += ["", "vs market = minus the average open-to-close of all top-liquid stocks that day (removes market moves);",
            "t(vs mkt) uses one value per day, |t| > 2 = unlikely to be luck. long/short net include charges + slippage."]
    text = "\n".join(out)
    print(text)
    (DATA.parent / "backtest_results").mkdir(exist_ok=True)
    (DATA.parent / "backtest_results" / "rsi_intraday_selection.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
