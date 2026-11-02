"""
Overnight trade - SIZING by volume and rank. How many rupees of each ranked stock to buy so the orders fit the two thin
sessions the trade lives in (post-close entry, pre-open auction exit), against equal amounts in the top 1 / 5 / 9 / 18,
for capital from Rs 1 lakh to Rs 1 crore.

    python scripts\\research\\overnight_sizing_backtest.py [--run phase1] [--model blend] [--k 40]
    -> runs\\overnight_sizing.txt              the capacity curve and the checks
       runs\\overnight_sizing_nightly.csv      every night x capital x strategy
       runs\\overnight_sizing_positions.csv    every OPT position (what was ordered, filled, earned)
       runs\\overnight_edge.json               the edge model fitted on all nights (used by: nsealgo size-overnight)
       runs\\overnight_candidates_<run>_<model>.parquet   the nightly candidate lists (ranks 1..k, locked removed)
       runs\\ML_overnight_sizing.xlsx          the workbook (needs openpyxl: pip install openpyxl)

A night, the same for every strategy:
  ORDER   the strategy's rupees per stock -> whole shares at the closing price
  FILL    post-close session: we are first in the queue at 15:40, so we get at most FILL_SHARE x the session's ACTUAL
          volume that night (measured from the 1-minute archive on Nov 2023 - Sep 2025 nights and from the at-close
          snapshots; otherwise its expected size). What does not fill is not bought.
  EXIT    next pre-open auction: the open, pushed down by our own sale  KAPPA x daily sigma x (our sale / that auction's
          volume)  (measured on Oct 2025 -> nights and from the pre-open recorder; otherwise its expected size)
  COSTS   Shoonya delivery on the filled value (STT 0.1% x 2, stamp 0.015%, NSE + SEBI + GST, DP Rs 10.62 per stock)
Strategies:  EQ top N  = equal rupees in the top N ranks, blind to volume (the ML_overnight_backtest_full.xlsx trade)
             VOL top N = equal rupees in the top N, but none above OPT's per-stock caps (volume-aware, rank-blind)
             OPT       = nsealgo.sizing.overnight.allocate: expected edge from the predicted gap, per-stock caps from
                         the liquidity table, water-filling; variants test it against worse fills / more impact.
OPT decides with what was known at 15:37 that day: the edge model is refitted every night on EARLIER nights only, volumes
are 20-day medians up to the day before. The liquidity table's per-stock shares are measured over the whole period
(volumes only - they use no prices or returns). The first BURN nights only train the edge model (no results).
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import glob
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from nsealgo.config import load_config, resolve_path  # noqa: E402
from nsealgo.sizing import liquidity as L  # noqa: E402
from nsealgo.sizing import overnight as O  # noqa: E402

_CFG = load_config(ROOT / "config" / "system.yaml")
DATA, RUNS = resolve_path(_CFG, "data_dir"), resolve_path(_CFG, "runs_dir")
ML = O.ml_home(ROOT)
ARCHIVE = Path(os.environ.get("NSEALGOENGINE_MINUTE_ARCHIVE", "").strip().strip('"') or str(DATA / "minute_archive"))
LAKH = 100_000
CAPITALS = [1, 2, 3, 5, 10, 20, 30, 50, 75, 100]       # lakh
EQ_N = [1, 5, 9, 18]
LOCK_MIN = 10            # of the 30 one-minute bars 15:00-15:29 flat at the day high -> locked at the upper band
BANDS = (0.02, 0.05, 0.10, 0.20)
BURN = 60                # nights used only to train the edge model
FILL_SHARE = 1.0         # of the post-close session's actual volume our order can get (first in the queue)
MIN_PRICE = 50.0         # the trade's price floor (Rs); research scripts may lower it
KAPPA_REAL = 0.5         # auction impact used to SCORE every strategy (OPT plans with its own Params.kappa)


def log(m):
    print(f"{time.strftime('%H:%M:%S')}  {m}", flush=True)


# ---- candidates ------------------------------------------------------------------------------------------------
def _universe(run: str, model: str) -> pd.DataFrame:
    """Every stock-night with a walk-forward (or live) predicted gap, in the trade's universe (known the evening before:
    60-day median turnover >= Rs 25 cr, close >= Rs 50, no high = low day in 20) - as ml_overnight_backtest.py."""
    fs = sorted(glob.glob(str(ML / "work" / "results" / run / f"pred_{model}_f*.parquet")))
    if not fs:
        raise SystemExit(f"no predictions in {ML / 'work' / 'results' / run} (pred_{model}_f*.parquet)")
    p = pd.concat([pd.read_parquet(f, columns=["company_id", "datetime", "y_g_n_q50"]) for f in fs])
    p = p.drop_duplicates(["company_id", "datetime"], keep="last")
    ds = pd.read_parquet(ML / "work" / "dataset_v2.parquet",
                         columns=["company_id", "datetime", "high", "low", "close", "volume", "sigma", "next_datetime",
                                  "o_next", "v_next"],
                         filters=[("datetime", ">=", p["datetime"].min() - pd.Timedelta(days=150))])
    ds = ds.sort_values(["company_id", "datetime"]).reset_index(drop=True)
    g = ds.groupby("company_id", sort=False)
    ds["prev_close"] = g["close"].shift(1)
    tv = ds["close"] * ds["volume"]
    ds["turn60"] = tv.groupby(ds["company_id"]).transform(lambda s: s.rolling(60, min_periods=40).median())
    ds["tv20"] = tv.groupby(ds["company_id"]).transform(lambda s: s.shift(1).rolling(20, min_periods=10).median())
    ds["lock20"] = (ds["high"] == ds["low"]).astype(float).groupby(ds["company_id"]).transform(
        lambda s: s.rolling(20, min_periods=1).sum())
    wf = p.merge(ds, on=["company_id", "datetime"], how="inner")
    wf["pred_gap"] = wf["y_g_n_q50"] * wf["sigma"]
    wf["src"] = "walk-forward"
    last, live = wf["datetime"].max(), []
    for f in sorted(glob.glob(str(ML / "work" / "predictions" / "predictions_????????.parquet"))):
        d = pd.Timestamp(Path(f).stem[-8:])
        if d > last:
            x = pd.read_parquet(f, columns=["company_id", "exp_gap_pct"])
            x["datetime"] = d
            live.append(x)
    if live:
        lv = pd.concat(live).merge(ds, on=["company_id", "datetime"], how="inner")
        lv["pred_gap"] = np.log1p(lv["exp_gap_pct"] / 100)
        lv["src"] = "live"
        wf = pd.concat([wf, lv], ignore_index=True)
    x = wf[(wf["turn60"] >= 25e7) & (wf["close"] >= MIN_PRICE) & (wf["lock20"] == 0) & wf["o_next"].notna()].copy()
    x["gross_bps"] = (x["o_next"] / x["close"] - 1) * 1e4
    x = x[x["gross_bps"].abs() < 2000].copy()
    x["pred_bps"] = x["pred_gap"] * 1e4
    x["buy_day"] = pd.to_datetime(x["datetime"]).astype("datetime64[ns]")
    x["sell_day"] = pd.to_datetime(x["next_datetime"]).astype("datetime64[ns]")
    x["sigma_bps"] = x["sigma"] * 1e4
    return x[["company_id", "buy_day", "sell_day", "src", "pred_bps", "close", "high", "low", "prev_close", "volume",
              "o_next", "v_next", "sigma_bps", "tv20", "gross_bps"]]


def _no_bars() -> pd.DataFrame:
    return pd.DataFrame({"buy_day": pd.Series(dtype="datetime64[ns]"), "flat_bars": pd.Series(dtype=float),
                         "n30": pd.Series(dtype=float), "company_id": pd.Series(dtype=object)})


def _archive_flat_bars(need: pd.DataFrame) -> pd.DataFrame:
    """need: company_id, buy_day. From the 1-minute archive: flat_bars = bars 15:00-15:29 that traded only at the day's
    own high (high = low = day high), n30 = bars 15:00-15:29. Stocks / days the archive does not have are absent."""
    out = []
    for cid, g in need.groupby("company_id"):
        d = ARCHIVE / str(cid).replace("/", "_")
        if not d.exists():
            continue
        days = pd.DatetimeIndex(g["buy_day"].unique())
        files = []
        for f in sorted(d.glob("*_*.parquet")):
            try:
                a, b = (pd.Timestamp(v) for v in f.stem.split("_"))
            except ValueError:
                continue
            if ((days >= a) & (days <= b)).any():
                files.append(f)
        if not files:
            continue
        m = pd.concat([pd.read_parquet(f, columns=["ts", "high", "low"]) for f in files]).drop_duplicates("ts")
        m["day"] = pd.to_datetime(m["ts"]).astype("datetime64[ns]").dt.normalize()
        m = m[m["day"].isin(days)]
        if m.empty:
            continue
        dh = m.groupby("day")["high"].transform("max")
        hm = m["ts"].dt.hour * 60 + m["ts"].dt.minute
        last30 = (hm >= 900) & (hm <= 929)
        flat = last30 & (m["high"] == m["low"]) & (m["high"] >= dh * 0.9999)
        r = pd.DataFrame({"buy_day": m["day"], "flat": flat, "last30": last30}).groupby("buy_day") \
            .agg(flat_bars=("flat", "sum"), n30=("last30", "sum")).reset_index()
        r["company_id"] = cid
        out.append(r[r["n30"] > 0])
    return pd.concat(out, ignore_index=True) if out else _no_bars()


def _db_flat_bars(need: pd.DataFrame) -> pd.DataFrame:
    """The same from runs\\close_lock_bars.parquet (15:00-15:29 bars of history.db, written by ml_overnight_backtest.py)."""
    p = RUNS / "close_lock_bars.parquet"
    if not p.exists():
        return _no_bars()
    b = pd.read_parquet(p)
    b["buy_day"] = pd.to_datetime(b["buy_day"]).astype("datetime64[ns]")
    b = b.merge(need[["company_id", "buy_day", "high"]].rename(columns={"high": "day_high"}), on=["company_id", "buy_day"])
    b["flat"] = (b["high"] == b["low"]) & (b["high"] >= b["day_high"] * 0.9999)
    return b.groupby(["company_id", "buy_day"]).agg(flat_bars=("flat", "sum"), n30=("flat", "size")).reset_index()


def candidates(run: str, model: str, k: int, refresh: bool = False) -> pd.DataFrame:
    """The nightly candidate lists: buyable stocks (not locked at the upper band at the close), ranked by predicted gap,
    ranks 1..k. Locked = closed at the day high AND (>= LOCK_MIN of the 15:00-15:29 one-minute bars flat at that high;
    without minute data: the close within 0.15% of a 2 / 5 / 10 / 20% price band). Cached in runs\\."""
    cache = RUNS / (f"overnight_candidates_{run}_{model}.parquet" if MIN_PRICE == 50 else
                    f"overnight_candidates_{run}_{model}_p{MIN_PRICE:g}.parquet")
    srcs = glob.glob(str(ML / "work" / "results" / run / f"pred_{model}_f*.parquet")) + \
        glob.glob(str(ML / "work" / "predictions" / "predictions_????????.parquet")) + [str(ML / "work" / "dataset_v2.parquet")]
    if cache.exists() and not refresh and cache.stat().st_mtime > max(os.path.getmtime(s) for s in srcs):
        c = pd.read_parquet(cache)
        if c["rank"].max() >= k:
            return c[c["rank"] <= k].copy()
    t0 = time.time()
    x = _universe(run, model)
    log(f"universe: {len(x):,} stock-nights, {x['buy_day'].nunique()} nights ({time.time() - t0:.0f}s)")
    x["r0"] = x.groupby("buy_day")["pred_bps"].rank(method="first", ascending=False)
    x = x[x["r0"] <= k + 40].copy()                           # enough spare names to replace the locked ones
    x["at_high"] = x["close"] >= x["high"] * 0.9995
    need = x.loc[x["at_high"], ["company_id", "buy_day", "high"]]
    fb = pd.concat([f for f in (_db_flat_bars(need), _archive_flat_bars(need)) if len(f)] or [_no_bars()],
                   ignore_index=True).drop_duplicates(["company_id", "buy_day"], keep="first")
    fb["buy_day"] = pd.to_datetime(fb["buy_day"]).astype("datetime64[ns]")
    fb["flat_bars"] = pd.to_numeric(fb["flat_bars"])
    x = x.merge(fb[["company_id", "buy_day", "flat_bars"]], on=["company_id", "buy_day"], how="left")
    band = np.zeros(len(x), bool)
    for b in BANDS:
        band |= ((x["close"] / (x["prev_close"] * (1 + b)) - 1).abs() <= 0.0015).to_numpy()
    x["locked"] = x["at_high"] & np.where(x["flat_bars"].notna(), x["flat_bars"].fillna(0) >= LOCK_MIN, band)
    log(f"locked at the upper band at the close: {int(x['locked'].sum())} of {int(x['at_high'].sum())} candidates "
        f"that closed at the day high ({int(x['flat_bars'].notna().sum())} judged from 1-minute bars)")
    x = x[~x["locked"]].copy()
    x["rank"] = x.groupby("buy_day")["pred_bps"].rank(method="first", ascending=False).astype(int)
    c = x[x["rank"] <= k].drop(columns=["r0", "at_high", "flat_bars", "locked"]).sort_values(["buy_day", "rank"])
    c = c.reset_index(drop=True)
    RUNS.mkdir(exist_ok=True)
    c.to_parquet(cache, index=False)
    log(f"candidates: {len(c):,} rows, {c['buy_day'].nunique()} nights, ranks 1..{k} -> {cache} ({time.time() - t0:.0f}s)")
    return c


# ---- liquidity: usual (what the decision sees) and actual (what the night delivered) ---------------------------
TABLE = DATA / "liquidity_table.parquet"


def liquidity(c: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Adds pc_rs / pre_rs = the expected rupee size of the night's post-close session and the next morning's auction
    (liquidity.expected_rs: the stock's share x today's turnover^w x 20-day median^(1-w) - known at 15:37), and
    pc_rs_real / pre_rs_real = the actual session / auction where measured (pc_src / pre_src say how), else the
    expected ones. Returns (c, liquidity table)."""
    t = L.build(ROOT, ARCHIVE, TABLE, log=log)
    p = L.paths(ROOT, ARCHIVE)
    md = pd.read_parquet(TABLE.with_name("minute_day_volume.parquet"))
    md["date"] = pd.to_datetime(md["date"]).astype("datetime64[ns]")
    raw = L.daily_volume(p["raw"])
    x = L.archive_days(md, raw)
    c = c.copy()
    c["pc_share"], c["pre_share"] = L.shares(t, c["company_id"])
    c["pc_rs"], c["pre_rs"] = L.expected_rs(t, c["company_id"], c["close"] * c["volume"], c["tv20"])
    # the night's post-close session: archive days whose 09:15 bar held the auction (outside volume = this session),
    # or the at-close snapshot (daily volume - volume at 15:30)
    b = x.loc[x["included"], ["company_id", "date", "share"]].rename(columns={"date": "buy_day", "share": "pc_arch"})
    rc = L.recorded_close(p["at_close"]).merge(raw, on=["company_id", "date"])
    rc = rc.assign(pc_snap=((rc["volume"] - rc["v1530"]) / rc["volume"]).clip(lower=0))
    rc = rc[["company_id", "date", "pc_snap"]].rename(columns={"date": "buy_day"})
    c = c.merge(b, on=["company_id", "buy_day"], how="left").merge(rc, on=["company_id", "buy_day"], how="left")
    c["pc_real"] = c["pc_snap"].fillna(c["pc_arch"])
    c["pc_src"] = np.where(c["pc_snap"].notna(), "snapshot", np.where(c["pc_arch"].notna(), "archive", "expected"))
    # the next morning's auction: the pre-open recorder, or archive days without the auction in the 09:15 bar
    # (outside volume - the stock's usual post-close share)
    s = x.loc[~x["included"], ["company_id", "date", "share"]].rename(columns={"date": "sell_day", "share": "out_sell"})
    rp = L.recorded_preopen(p["preopen"]).merge(raw, on=["company_id", "date"])
    rp = rp.assign(pre_rec=(rp["final_qty"] / rp["volume"]).clip(upper=1.0))[["company_id", "date", "pre_rec"]] \
        .rename(columns={"date": "sell_day"})
    c = c.merge(s, on=["company_id", "sell_day"], how="left").merge(rp, on=["company_id", "sell_day"], how="left")
    c["pre_real"] = c["pre_rec"].fillna((c["out_sell"] - c["pc_share"]).clip(lower=0))
    c["pre_src"] = np.where(c["pre_rec"].notna(), "recorder", np.where(c["out_sell"].notna(), "archive", "expected"))
    c["pc_rs_real"] = (c["pc_real"] * c["close"] * c["volume"]).fillna(c["pc_rs"])
    c["pre_rs_real"] = (c["pre_real"] * c["o_next"] * c["v_next"]).fillna(c["pre_rs"])
    c["tvr"] = c["close"] * c["volume"] / c["tv20"]
    c = c.drop(columns=["pc_arch", "pc_snap", "out_sell", "pre_rec"])
    return c, t


# ---- the simulation --------------------------------------------------------------------------------------------
STRATS = [("EQ top 1", "eq", 1), ("EQ top 5", "eq", 5), ("EQ top 9", "eq", 9), ("EQ top 18", "eq", 18),
          ("VOL top 5", "vol", 5), ("VOL top 18", "vol", 18),
          ("OPT top 5", "opt", {"max_stocks": 5}), ("OPT top 9", "opt", {"max_stocks": 9}),
          ("OPT top 18", "opt", {"max_stocks": 18}),
          ("OPT", "opt", {}),
          ("OPT | auction impact x2", "opt", {"kappa_real": 2 * KAPPA_REAL}),
          ("OPT | half the post-close fills", "opt", {"fill": FILL_SHARE / 2}),
          ("OPT | caps 25% of the session", "opt", {"pc_part": 0.25}),
          ("OPT | caps 100% of the session", "opt", {"pc_part": 1.0}),
          ("OPT | up to 100% in one stock", "opt", {"max_weight": 1.0})]
MAIN = ["EQ top 1", "EQ top 5", "EQ top 9", "EQ top 18", "VOL top 5", "VOL top 18", "OPT"]


def simulate(c: pd.DataFrame, base: O.Params, half_life: float) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (nightly results, OPT positions, the edge model of every night)."""
    from dataclasses import fields, replace
    pf = {f.name for f in fields(O.Params)}
    nights = np.sort(c["buy_day"].unique())
    by_night = {d: g.sort_values("rank") for d, g in c.groupby("buy_day")}
    hist = c[["buy_day", "pred_bps", "gross_bps"]]
    rows, pos, edges = [], [], []
    t0 = time.time()
    for i, d in enumerate(nights):
        em = O.fit_edge(hist[hist["buy_day"] < d], half_life=half_life)   # nights already sold by this afternoon
        edges.append({"buy_day": d, "a": em.a, "b": em.b, "nights_used": em.nights})
        if i < BURN:
            continue
        g = by_night[d]
        price, sell = g["close"].to_numpy(float), g["o_next"].to_numpy(float)
        sig, rank = g["sigma_bps"].to_numpy(float), g["rank"].to_numpy()
        edge = em.edge_bps(g["pred_bps"].to_numpy(float))
        pc_rs, pre_rs = g["pc_rs"].to_numpy(float), g["pre_rs"].to_numpy(float)
        pc_real, pre_real = g["pc_rs_real"].to_numpy(float), np.maximum(g["pre_rs_real"].to_numpy(float), 1.0)
        for cap_l in CAPITALS:
            capital = cap_l * LAKH
            for name, kind, opt in STRATS:
                if kind == "eq":
                    x = np.where(rank <= opt, capital / opt, 0.0)
                    qty = np.floor(x / price)
                    fill_share, kappa_real, cap, by = FILL_SHARE, KAPPA_REAL, None, None
                elif kind == "vol":
                    qty = O.capped_equal_arrays(price, O.caps_arrays(price, pc_rs, pre_rs, capital, base), capital, opt)
                    fill_share, kappa_real, cap, by = FILL_SHARE, KAPPA_REAL, None, None
                else:
                    p = replace(base, **{k: v for k, v in opt.items() if k in pf})
                    m = min(p.max_stocks, len(price))                  # the top m ranks only (g is in rank order)
                    qty, cap, by = np.zeros(len(price)), np.full(len(price), np.nan), np.zeros(len(price), int)
                    qty[:m], cap[:m], by[:m], _, _ = O.allocate_arrays(price[:m], edge[:m], sig[:m], pc_rs[:m],
                                                                       pre_rs[:m], capital, p)
                    fill_share, kappa_real = opt.get("fill", FILL_SHARE), opt.get("kappa_real", KAPPA_REAL)
                fq = np.minimum(qty, np.floor(fill_share * pc_real / price))
                bv = fq * price
                imp = np.minimum(kappa_real * sig * (fq * sell) / pre_real, 1000.0)
                sv = fq * sell * (1 - imp / 1e4)
                pnl = sv - bv - O.costs_rs(bv, sv)
                rows.append((d, cap_l, name, float((qty * price).sum()), float(bv.sum()), int((qty > 0).sum()),
                             int((fq > 0).sum()), float(pnl.sum()), float((fq * (sell - price)).sum()),
                             float((fq * sell * imp / 1e4).sum())))
                if name == "OPT" or (cap_l == 5 and name in ("EQ top 5", "OPT top 5")):
                    k_ = np.nonzero(qty > 0)[0]
                    for j in k_:
                        pos.append((name, d, g["sell_day"].iloc[j], cap_l, int(rank[j]), g["company_id"].iloc[j],
                                    float(g["pred_bps"].iloc[j]), float(edge[j]), float(cap[j]) if cap is not None else np.nan,
                                    O.CAP_BY[by[j]] if by is not None else "",
                                    float(qty[j] * price[j]), float(bv[j]), float(imp[j]), float(g["gross_bps"].iloc[j]),
                                    float(pnl[j]), g["pc_src"].iloc[j], g["pre_src"].iloc[j]))
        if (i + 1) % 100 == 0:
            log(f"  {i + 1} of {len(nights)} nights ({time.time() - t0:.0f}s)")
    nightly = pd.DataFrame(rows, columns=["buy_day", "capital_lakh", "strategy", "ordered_rs", "filled_rs", "n_ordered",
                                          "n_filled", "pnl_rs", "gross_rs", "impact_rs"])
    positions = pd.DataFrame(pos, columns=["strategy", "buy_day", "sell_day", "capital_lakh", "rank", "company_id", "pred_bps",
                                           "edge_bps", "cap_rs", "cap_by", "ordered_rs", "filled_rs", "impact_bps",
                                           "gross_bps", "pnl_rs", "postclose_volume_from", "auction_volume_from"])
    return nightly, positions, pd.DataFrame(edges)


def summarize(nightly: pd.DataFrame) -> pd.DataFrame:
    """One row per scenario x capital x strategy."""
    out = []
    fri = pd.to_datetime(nightly["buy_day"]).dt.weekday == 4
    skip = nightly.copy()
    skip.loc[fri, ["ordered_rs", "filled_rs", "n_ordered", "n_filled", "pnl_rs", "gross_rs", "impact_rs"]] = 0
    for scen, nf in (("every night", nightly), ("skip Friday", skip)):
        span = (nightly["buy_day"].max() - nightly["buy_day"].min()).days / 365.25
        for (cap_l, strat), g in nf.groupby(["capital_lakh", "strategy"], sort=False):
            pn = g["pnl_rs"].to_numpy()
            sd = pn.std(ddof=1)
            sv = g["filled_rs"].sum() + g["gross_rs"].sum()
            out.append({"scenario": scen, "capital_lakh": cap_l, "strategy": strat, "nights": len(g),
                        "total_rs": pn.sum(), "rs_per_night": pn.mean(), "return_pct_pa": 100 * pn.sum() / (cap_l * LAKH) / span,
                        "avg_ordered_rs": g["ordered_rs"].mean(), "avg_filled_rs": g["filled_rs"].mean(),
                        "deployed_pct": 100 * g["filled_rs"].mean() / (cap_l * LAKH),
                        "fill_pct": 100 * g["filled_rs"].sum() / max(g["ordered_rs"].sum(), 1),
                        "avg_stocks": g["n_filled"].mean(),
                        "impact_bps": 1e4 * g["impact_rs"].sum() / max(sv, 1),
                        "impact_rs_per_night": g["impact_rs"].mean(),
                        "net_bps_on_filled": 1e4 * pn.sum() / max(g["filled_rs"].sum(), 1),
                        "worst_night_rs": pn.min(), "best_night_rs": pn.max(),
                        "pct_nights_up": 100 * (pn > 0).mean(), "t": pn.mean() / (sd / np.sqrt(len(pn))) if sd > 0 else np.nan})
    return pd.DataFrame(out)


def report(summ: pd.DataFrame, c: pd.DataFrame, table: pd.DataFrame, edges: pd.DataFrame, base: O.Params,
           nights: int, first, last, nightly_by_year: pd.DataFrame, positions: pd.DataFrame | None = None) -> str:
    fmt = lambda v: f"{v:+,.0f}"
    s0 = summ[summ["scenario"] == "every night"].set_index(["strategy", "capital_lakh"])
    split = [f"OVERNIGHT TRADE - SPLITTING THE MONEY AMONG THE TOP N STOCKS   ({nights} nights {first:%Y-%m-%d} .. "
             f"{last:%Y-%m-%d}; averages per night, after all charges)", "",
             "Same stocks both ways - the top N by predicted gap. EQUAL = the same rupees in each. OPTIMIZED = rupees set by "
             "each stock's price, traded volume, volatility and expected move (nsealgo size-overnight --top N).",
             "unfilled = rupees ordered that the post-close session could not fill; slippage = rupees lost because your own "
             "sale pushed the opening price down.", ""]
    for n_ in (5, 9, 18):
        split.append(f"TOP {n_}")
        split.append(f"  {'capital':>10s} | {'EQUAL: profit':>13s} {'unfilled':>9s} {'slippage':>8s} | "
                     f"{'OPTIMIZED: profit':>17s} {'invested':>9s} {'slippage':>8s} | {'extra profit':>12s}")
        for cap_l in CAPITALS:
            e_, o_ = s0.loc[(f"EQ top {n_}", cap_l)], s0.loc[(f"OPT top {n_}", cap_l)]
            split.append(f"  Rs {cap_l:4.0f} lakh | {fmt(e_['rs_per_night']):>13s} {e_['avg_ordered_rs'] - e_['avg_filled_rs']:9,.0f} "
                         f"{e_['impact_rs_per_night']:8,.0f} | {fmt(o_['rs_per_night']):>17s} {o_['avg_filled_rs']:9,.0f} "
                         f"{o_['impact_rs_per_night']:8,.0f} | {fmt(o_['rs_per_night'] - e_['rs_per_night']):>12s}")
        split.append("")
    lines = split + [f"OVERNIGHT TRADE - SIZING BY VOLUME AND RANK   ({nights} nights {first:%Y-%m-%d} .. {last:%Y-%m-%d}, after a "
             f"{BURN}-night start that only trains the edge model)", "",
             "Buy in the 15:40-16:00 post-close session at the close, sell in the next pre-open auction. The post-close "
             "session is thin: a stock's usual session is",
             f"{100 * float(table.loc[table['company_id'] == L.ALL, 'pc_share'].iloc[0]):.3f}% of its daily volume "
             f"(market median), the auction {100 * float(table.loc[table['company_id'] == L.ALL, 'pre_share'].iloc[0]):.3f}%.",
             f"Candidates are busier than usual on their night (today's turnover {c['tvr'].median():.1f}x the 20-day median), "
             f"so for ranks 1-18 the expected post-close session is a median Rs {c.loc[c['rank'] <= 18, 'pc_rs'].median() / 1e5:.2f} "
             f"lakh (10% below Rs {c.loc[c['rank'] <= 18, 'pc_rs'].quantile(0.1) / 1e5:.2f} lakh) and the next morning's "
             f"auction Rs {c.loc[c['rank'] <= 18, 'pre_rs'].median() / 1e5:.1f} lakh.", "",
             "Rs per night after all costs, the post-close fill limit and the auction impact (KAPPA "
             f"{KAPPA_REAL}), every night:"]
    s = summ[summ["scenario"] == "every night"]
    piv = s.pivot(index="capital_lakh", columns="strategy", values="rs_per_night")[MAIN]
    ret = s.pivot(index="capital_lakh", columns="strategy", values="return_pct_pa")[MAIN]
    short = lambda m: m.replace("EQ top ", "EQ").replace("VOL top ", "VOL")
    lines.append(f"  {'capital':>9s} " + " ".join(f"{m:>10s}" for m in MAIN) + "  |  % a year on the capital: "
                 + " ".join(f"{short(m):>6s}" for m in MAIN))
    for cap_l in piv.index:
        lines.append(f"  {cap_l:6.0f} L " + " ".join(f"{fmt(v):>10s}" for v in piv.loc[cap_l]) + "  |  "
                     + " ".join(f"{v:6.1f}" for v in ret.loc[cap_l]))
    o = s[s["strategy"] == "OPT"].set_index("capital_lakh")
    lines += ["", "OPT in detail (every night):",
              f"  {'capital':>9s} {'stocks':>6s} {'deployed':>8s} {'filled':>7s} {'impact':>7s} {'net bps':>8s} "
              f"{'worst night':>12s} {'nights up':>9s} {'t':>5s}"]
    for cap_l, r in o.iterrows():
        lines.append(f"  {cap_l:6.0f} L {r['avg_stocks']:6.1f} {r['deployed_pct']:7.0f}% {r['fill_pct']:6.0f}% "
                     f"{r['impact_bps']:6.1f}b {r['net_bps_on_filled']:+8.1f} {fmt(r['worst_night_rs']):>12s} "
                     f"{r['pct_nights_up']:8.0f}% {r['t']:5.1f}")
    if positions is not None and len(positions):
        m = positions[(positions["strategy"] == "OPT") & (positions["postclose_volume_from"] != "expected")]
        if len(m):
            f = m.groupby("capital_lakh").apply(lambda g: 100 * g["filled_rs"].sum() / max(g["ordered_rs"].sum(), 1),
                                                include_groups=False)
            lines += ["", "OPT orders on nights whose post-close volume was MEASURED (the session is very noisy, a factor ~5 "
                      "either way): filled " + ", ".join(f"{k:.0f}L {v:.0f}%" for k, v in f.items())]
    lines += ["", "Equal amounts, how much really filled (every night):  fill % of the ordered rupees / average stocks filled"]
    for m in [m for m in MAIN if m.startswith("EQ")]:
        e = s[s["strategy"] == m].set_index("capital_lakh")
        lines.append(f"  {m:10s} " + "  ".join(f"{cap_l:.0f}L {r['fill_pct']:3.0f}%/{r['avg_stocks']:4.1f}"
                                               for cap_l, r in e.iterrows()))
    lines += ["", "Robustness of OPT - Rs per night (every night):"]
    var = [m for m in s["strategy"].unique() if m.startswith("OPT")]
    pv = s.pivot(index="capital_lakh", columns="strategy", values="rs_per_night")[var]
    lines.append(f"  {'capital':>9s} " + " ".join(f"{m.replace('OPT | ', '')[:22]:>23s}" for m in var))
    for cap_l in pv.index:
        lines.append(f"  {cap_l:6.0f} L " + " ".join(f"{fmt(v):>23s}" for v in pv.loc[cap_l]))
    k = summ[summ["scenario"] == "skip Friday"]
    pk = k.pivot(index="capital_lakh", columns="strategy", values="rs_per_night")[MAIN]
    wk = k.pivot(index="capital_lakh", columns="strategy", values="worst_night_rs")[MAIN]
    lines += ["", "Skip Friday (no Friday-close -> Monday-open trade) - Rs per night over ALL nights / worst night:"]
    for cap_l in pk.index:
        lines.append(f"  {cap_l:6.0f} L " + " ".join(f"{fmt(pk.loc[cap_l, m]):>9s}/{fmt(wk.loc[cap_l, m]):>9s}" for m in MAIN))
    nyr = nightly_by_year
    lines += ["", "By year - Rs per night (every night):"]
    for cap_l in (5, 20):
        y = nyr[nyr["capital_lakh"] == cap_l].pivot(index="year", columns="strategy", values="pnl_rs")[MAIN]
        lines.append(f"  Rs {cap_l} lakh   " + " ".join(f"{short(m):>8s}" for m in MAIN))
        for yr, r in y.iterrows():
            lines.append(f"    {yr}     " + " ".join(f"{fmt(v):>8s}" for v in r))
    e = edges.iloc[BURN::max(1, (len(edges) - BURN) // 6)]
    lines += ["", "Edge model through time (expected move before costs = a + b x predicted gap, bps; refitted nightly on "
              "earlier nights, half-life 250 nights):",
              "  " + "   ".join(f"{pd.Timestamp(r.buy_day):%Y-%m}: a {r.a:+.0f}, b {r.b:.2f}" for r in e.itertuples())]
    lines += ["", f"OPT settings: post-close cap {base.pc_part:.0%} of the usual session, auction cap {base.auction_part:.0%} "
              f"of the usual auction, auction impact KAPPA {base.kappa}, max {base.max_weight:.0%} of the capital in one "
              f"stock, at most {base.max_stocks} candidates. Fills: first in the post-close queue, up to "
              f"{FILL_SHARE:.0%} of the session's actual volume.",
              "net bps = profit after everything per rupee actually bought; impact = our own sale's push on the open; "
              "t = average nightly profit / its standard error."]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="phase1")
    ap.add_argument("--model", default="blend")
    ap.add_argument("--k", type=int, default=40, help="candidates per night (ranks 1..k)")
    ap.add_argument("--half-life", type=float, default=250.0, help="edge model: weight halves every N nights back")
    ap.add_argument("--refresh", action="store_true", help="rebuild the candidate lists")
    ap.add_argument("--no-workbook", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    base = O.Params(max_stocks=a.k)
    c = candidates(a.run, a.model, a.k, refresh=a.refresh)
    c, table = liquidity(c)
    log(f"liquidity: post-close volume of the night measured for {100 * (c['pc_src'] != 'expected').mean():.0f}% of the "
        f"candidate stock-nights, the morning auction for {100 * (c['pre_src'] != 'expected').mean():.0f}%")
    nightly, positions, edges = simulate(c, base, a.half_life)
    summ = summarize(nightly)
    nights = nightly["buy_day"].nunique()
    nyr = nightly.assign(year=pd.to_datetime(nightly["buy_day"]).dt.year).groupby(["capital_lakh", "strategy", "year"],
                                                                                 as_index=False)["pnl_rs"].mean()
    text = report(summ, c, table, edges, base, nights, nightly["buy_day"].min(), nightly["buy_day"].max(), nyr, positions)
    print(text)
    RUNS.mkdir(exist_ok=True)
    (RUNS / "overnight_sizing.txt").write_text(text, encoding="utf-8")
    nightly.to_csv(RUNS / "overnight_sizing_nightly.csv", index=False)
    positions.to_csv(RUNS / "overnight_sizing_positions.csv", index=False)
    summ.to_csv(RUNS / "overnight_sizing_summary.csv", index=False)
    edges.to_csv(RUNS / "overnight_sizing_edge.csv", index=False)
    O.fit_edge(c[["buy_day", "pred_bps", "gross_bps"]], half_life=a.half_life).save(RUNS / "overnight_edge.json")
    log(f"written runs\\overnight_sizing*.csv / .txt and runs\\overnight_edge.json ({time.time() - t0:.0f}s)")
    if not a.no_workbook:
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            log("no workbook: openpyxl is not installed (pip install openpyxl)")
            return
        from overnight_sizing_workbook import write as write_workbook
        write_workbook(RUNS / "ML_overnight_sizing.xlsx", summ, nightly, positions, c, table, edges, base, text)
        log(f"workbook -> {RUNS / 'ML_overnight_sizing.xlsx'}")


if __name__ == "__main__":
    main()
