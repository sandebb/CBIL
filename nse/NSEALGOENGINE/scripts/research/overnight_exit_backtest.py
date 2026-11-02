"""
Overnight trade - SELL AT A TARGET instead of at the next open. Same nights, same stocks, same buy (post-close session at
the close, same fills) and same sizing as overnight_sizing_backtest.py; only the SALE changes.

    python scripts\\research\\overnight_exit_backtest.py [--run phase1] [--model blend] [--max-seconds N]
    -> runs\\overnight_exit.txt             the results (plain tables)
       runs\\overnight_exit_summary.csv     every capital x strategy x exit rule
       runs\\overnight_exit_nightly.csv     every night x capital x strategy x exit rule
       runs\\exit_bars\\<stock>.parquet     cache: the sell days' 1-minute bars (09:15-15:29) of the candidate stocks
    The first run reads the 1-minute archive (several minutes); with --max-seconds it stops and the next run continues.

The sale, each morning, per stock:
  TARGET   T = open + f x (ML predicted high - open). open = the day's actual open (the pre-open auction price - the
           indicative equilibrium price is on screen by 09:07, before the auction ends). Predicted high = the ML's P50 high
           (live.py: max(P50 open, P50 close) x exp(P50 upper wick x sigma)), known the evening before.
           f = 0.5 is the midpoint asked for; 0.25 / 0.75 / 1.0 are also tested.
  AUCTION  if T <= open (the ML predicted a high below the actual open): sell in the pre-open auction like today (same
           auction impact as the sizing backtest).
  LIMIT    else a limit sell at T from 09:15. Filled only if a 1-minute bar's high reached T + 1 tick (0.05) - trading
           AT the price is not enough, it must trade through - and then at most LIMIT_PART of the volume of the bars
           whose high reached T (we are not alone at that price). Filled at T, no impact.
  FALLBACK what is not sold by the cut-off is sold at market: at 12:00 (the 12:00 bar's open), at 15:15 (the 15:15 bar's
           open) or at the close (the official close, sold across 15:00-15:30). Impact KAPPA x daily sigma x (our sale /
           the volume of the next 15 minutes; for the close: 15:00-15:29).
  NO DATA  sell days with no 1-minute bars (or bars that disagree with the daily open by more than 1%) sell at the open.
Variant 'pred open mid': T = midpoint of the ML predicted open and predicted high, placed in the pre-open (if the auction
price is above T it sells in the auction at the auction price).
Charges: Shoonya delivery, the same as selling at the open.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import glob
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import overnight_sizing_backtest as B  # noqa: E402  (also puts src\ on the path and reads config\system.yaml)
from nsealgo.sizing import overnight as O  # noqa: E402

RUNS, ARCHIVE, ML, LAKH, log = B.RUNS, B.ARCHIVE, B.ML, B.LAKH, B.log
BARS = RUNS / "exit_bars"
TOP = [5, 9, 18]
CAPITALS = [1, 5, 20]                 # lakh
FRACS = [0.25, 0.5, 0.75, 1.0]
CUTS = {"12:00": 720, "15:15": 915, "close": 930}
TICK = 0.05
LIMIT_PART = 0.10
KAPPA = B.KAPPA_REAL
START, END = 555, 930                 # 09:15 .. 15:29 bars
BASE = "sell at open (now)"


def _fname(f: float) -> str:
    return "midpoint" if f == 0.5 else f"{int(f * 100)}% to high"


RULES = {BASE: None}
for _f in FRACS:
    for _cut in CUTS:
        RULES[f"{_fname(_f)}, else at {_cut}"] = ("act", _f, _cut, None)
RULES["pred open mid, else at close"] = ("pred", 0.5, "close", None)
# GATED: wait for the target only when the ML expects the stock to close above the open; otherwise sell at the open.
#   [ML close > ML open]     the evening forecast: P50 close above P50 open (known the night before)
#   [ML close > actual open] the P50 close above the actual auction price (known at 09:07 from the indicative price)
GATES = {"ml": "ML close > ML open", "act": "ML close > actual open"}
for _g, _lab in GATES.items():
    RULES[f"midpoint, else at 12:00 [{_lab}]"] = ("act", 0.5, "12:00", _g)
    RULES[f"midpoint, else at close [{_lab}]"] = ("act", 0.5, "close", _g)
    RULES[f"75% to high, else at 12:00 [{_lab}]"] = ("act", 0.75, "12:00", _g)
    RULES[f"hold to close, no target [{_lab}]"] = ("act", None, "close", _g)
GATED = [r for r in RULES if r.endswith("]")]


# ---- inputs ----------------------------------------------------------------------------------------------------
def add_predictions(c: pd.DataFrame, run: str, model: str) -> pd.DataFrame:
    """pred_open / pred_high (P50, rupees) of each candidate night, and the sell day's official close c_next."""
    fs = sorted(glob.glob(str(ML / "work" / "results" / run / f"pred_{model}_f*.parquet")))
    p = pd.concat([pd.read_parquet(f, columns=["company_id", "datetime", "y_g_n_q50", "y_r_n_q50", "y_u_n_q50"]) for f in fs])
    p = p.drop_duplicates(["company_id", "datetime"], keep="last").rename(columns={"datetime": "buy_day"})
    p["buy_day"] = pd.to_datetime(p["buy_day"]).astype("datetime64[ns]")
    x = c.merge(p, on=["company_id", "buy_day"], how="left")
    s = x["sigma_bps"] / 1e4
    op, cp = x["close"] * np.exp(x["y_g_n_q50"] * s), x["close"] * np.exp(x["y_r_n_q50"] * s)
    x["pred_open"], x["pred_high"] = op, np.maximum(op, cp) * np.exp(x["y_u_n_q50"] * s)
    x["pred_close"] = cp
    live = []
    for f in sorted(glob.glob(str(ML / "work" / "predictions" / "predictions_????????.parquet"))):
        y = pd.read_parquet(f, columns=["company_id", "open_p50", "high_p50", "close_p50"])
        y["buy_day"] = pd.Timestamp(Path(f).stem[-8:])
        live.append(y)
    if live:
        lv = pd.concat(live)
        lv["buy_day"] = pd.to_datetime(lv["buy_day"]).astype("datetime64[ns]")
        x = x.merge(lv, on=["company_id", "buy_day"], how="left")
        m = x["src"].eq("live") & x["high_p50"].notna()
        x.loc[m, "pred_open"], x.loc[m, "pred_high"] = x.loc[m, "open_p50"], x.loc[m, "high_p50"]
        x.loc[m, "pred_close"] = x.loc[m, "close_p50"]
        x = x.drop(columns=["open_p50", "high_p50", "close_p50"])
    ds = pd.read_parquet(ML / "work" / "dataset_v2.parquet", columns=["company_id", "datetime", "c_next", "h_next"],
                         filters=[("datetime", ">=", c["buy_day"].min())])
    ds = ds.rename(columns={"datetime": "buy_day"})
    ds["buy_day"] = pd.to_datetime(ds["buy_day"]).astype("datetime64[ns]")
    x = x.merge(ds, on=["company_id", "buy_day"], how="left")
    return x.drop(columns=["y_g_n_q50", "y_r_n_q50", "y_u_n_q50"])


def _no_bars() -> pd.DataFrame:
    return pd.DataFrame({"day": pd.Series(dtype="datetime64[ns]"), "mins": pd.Series(dtype="int16"),
                         "open": pd.Series(dtype=float), "high": pd.Series(dtype=float), "volume": pd.Series(dtype=float)})


def cache_bars(need: pd.DataFrame, max_seconds: float | None) -> bool:
    """need: company_id, sell_day. Writes runs\\exit_bars\\<stock>.parquet (day, mins, open, high, volume). True when all done."""
    BARS.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    todo = [(cid, g) for cid, g in need.groupby("company_id")
            if not (BARS / f"{str(cid).replace('/', '_')}.parquet").exists()]
    log(f"minute bars: {len(todo)} of {need['company_id'].nunique()} stocks still to read from {ARCHIVE}")
    for i, (cid, g) in enumerate(todo):
        if max_seconds and time.time() - t0 > max_seconds:
            log(f"stopped after {max_seconds:.0f}s - run again to continue ({len(todo) - i} stocks left)")
            return False
        days = pd.DatetimeIndex(g["sell_day"].unique())
        d = ARCHIVE / str(cid).replace("/", "_")
        parts = []
        if d.exists():
            for f in sorted(d.glob("*_*.parquet")):
                try:
                    a, b = (pd.Timestamp(v) for v in f.stem.split("_"))
                except ValueError:
                    continue
                if ((days >= a) & (days <= b + pd.Timedelta(days=1))).any():
                    parts.append(pd.read_parquet(f, columns=["ts", "open", "high", "volume"]))
        m = _no_bars()
        if parts:
            m = pd.concat(parts).drop_duplicates("ts")
            m["ts"] = pd.to_datetime(m["ts"]).astype("datetime64[ns]")
            m["day"] = m["ts"].dt.normalize()
            m["mins"] = (m["ts"].dt.hour * 60 + m["ts"].dt.minute).astype("int16")
            m = m[m["day"].isin(days) & (m["mins"] >= START) & (m["mins"] < END)]
            m = m[["day", "mins", "open", "high", "volume"]].astype({"open": float, "high": float, "volume": float})
            m = m.sort_values(["day", "mins"])
        m.to_parquet(BARS / f"{str(cid).replace('/', '_')}.parquet", index=False)
        if (i + 1) % 50 == 0:
            log(f"  {i + 1} of {len(todo)} stocks ({time.time() - t0:.0f}s)")
    return True


def exit_stats(c: pd.DataFrame) -> pd.DataFrame:
    """One row per candidate stock-night x exit rule: route (auction / limit / nodata), target T, vol_above (shares
    traded in bars that reached T + tick before the cut-off), fb_px / fb_vol (fallback price and the shares traded in
    its window), hit (any bar reached T + tick)."""
    out = []
    for cid, g in c.groupby("company_id"):
        f = BARS / f"{str(cid).replace('/', '_')}.parquet"
        b = pd.read_parquet(f) if f.exists() else _no_bars()
        b["day"] = pd.to_datetime(b["day"]).astype("datetime64[ns]")
        byday = {d: x for d, x in b.groupby("day")}
        for r in g.itertuples(index=False):
            x = byday.get(r.sell_day)
            ok = x is not None and len(x) >= 200 and np.isfinite(r.pred_high) and np.isfinite(r.c_next) \
                and abs(x["open"].iloc[0] / r.o_next - 1) <= 0.01
            if ok:
                mins, hi, op, vol = (x["mins"].to_numpy(), x["high"].to_numpy(), x["open"].to_numpy(),
                                     x["volume"].to_numpy())
            for rule, spec in RULES.items():
                if spec is None:
                    continue
                kind, fr, cut, gate = spec
                row = [cid, r.buy_day, rule, "nodata", np.nan, 0.0, np.nan, 1.0, False]
                if ok:
                    base = r.o_next if kind == "act" else r.pred_open
                    T = 1e12 if fr is None else base + fr * (r.pred_high - base)      # None: no target, hold
                    wait = gate is None or (gate == "ml" and r.pred_close > r.pred_open) or \
                        (gate == "act" and r.pred_close > r.o_next)
                    if not wait or not np.isfinite(T) or T <= r.o_next:
                        row[3:5] = ["auction", T]
                    else:
                        cm = CUTS[cut]
                        reach = (hi >= T + TICK) & (mins < cm)
                        if cut == "close":
                            fb_px, w = r.c_next, (mins >= 900) & (mins < 930)
                        else:
                            k = np.nonzero(mins >= cm)[0]
                            if len(k):
                                fb_px, w = op[k[0]], (mins >= cm) & (mins < cm + 15)
                            else:
                                fb_px, w = r.c_next, (mins >= 900) & (mins < 930)
                        row[3:] = ["limit", T, float(vol[reach].sum()), float(fb_px), max(float(vol[w].sum()), 1.0),
                                   bool(reach.any())]
                out.append(row)
    return pd.DataFrame(out, columns=["company_id", "buy_day", "rule", "route", "T", "vol_above", "fb_px", "fb_vol", "hit"])


# ---- positions (the buy side, exactly as overnight_sizing_backtest.simulate) -------------------------------------
def positions(c: pd.DataFrame, hist: pd.DataFrame, base: O.Params, half_life: float) -> pd.DataFrame:
    """hist: buy_day, pred_bps, gross_bps of ALL 40 candidates (the edge model is fitted on them, as in the sizing backtest)."""
    strats = [(f"EQUAL top {n}", "eq", n) for n in TOP] + [(f"OPTIMIZED top {n}", "opt", n) for n in TOP]
    nights = np.sort(c["buy_day"].unique())
    by_night = {d: g.sort_values("rank") for d, g in c.groupby("buy_day")}
    rows, t0 = [], time.time()
    for i, d in enumerate(nights):
        if i < B.BURN:
            continue
        em = O.fit_edge(hist[hist["buy_day"] < d], half_life=half_life)
        g = by_night[d]
        price, sig, rank = g["close"].to_numpy(float), g["sigma_bps"].to_numpy(float), g["rank"].to_numpy()
        edge = em.edge_bps(g["pred_bps"].to_numpy(float))
        pc_rs, pre_rs, pc_real = g["pc_rs"].to_numpy(float), g["pre_rs"].to_numpy(float), g["pc_rs_real"].to_numpy(float)
        for cap_l in CAPITALS:
            capital = cap_l * LAKH
            for name, kind, n in strats:
                if kind == "eq":
                    qty = np.floor(np.where(rank <= n, capital / n, 0.0) / price)
                else:
                    p = replace(base, max_stocks=n)
                    m = min(n, len(price))
                    qty = np.zeros(len(price))
                    qty[:m] = O.allocate_arrays(price[:m], edge[:m], sig[:m], pc_rs[:m], pre_rs[:m], capital, p)[0]
                fq = np.minimum(qty, np.floor(B.FILL_SHARE * pc_real / price))
                for j in np.nonzero(fq > 0)[0]:
                    rows.append((d, cap_l, name, g["company_id"].iloc[j], fq[j]))
        if (i + 1) % 100 == 0:
            log(f"  positions: {i + 1} of {len(nights)} nights ({time.time() - t0:.0f}s)")
    return pd.DataFrame(rows, columns=["buy_day", "capital_lakh", "strategy", "company_id", "qty"])


def evaluate(pos: pd.DataFrame, c: pd.DataFrame, st: pd.DataFrame) -> pd.DataFrame:
    """Every position x rule -> sale value and profit after charges."""
    cols = ["company_id", "buy_day", "close", "o_next", "sigma_bps", "pre_rs_real"]
    p = pos.merge(c[cols], on=["company_id", "buy_day"], how="left")
    bv = p["qty"] * p["close"]
    pre = np.maximum(p["pre_rs_real"].to_numpy(float), 1.0)
    imp_auc = np.minimum(KAPPA * p["sigma_bps"] * (p["qty"] * p["o_next"]) / pre, 1000.0)
    sv_open = p["qty"] * p["o_next"] * (1 - imp_auc / 1e4)
    out = []
    for rule in RULES:
        q = p[["buy_day", "capital_lakh", "strategy", "company_id", "qty"]].copy()
        q["rule"], q["bv"] = rule, bv
        if RULES[rule] is None:
            q["sv"], q["route"], q["at_target"] = sv_open, "auction", 0.0
            q["d_rs"] = 0.0
        else:
            s = p[["company_id", "buy_day"]].merge(st[st["rule"] == rule], on=["company_id", "buy_day"], how="left")
            route = s["route"].fillna("nodata").to_numpy()
            lim = route == "limit"
            lq = np.where(lim, np.minimum(p["qty"], np.floor(LIMIT_PART * s["vol_above"].fillna(0))), 0.0)
            rq = np.where(lim, p["qty"] - lq, 0.0)
            fb_px, fb_vol = s["fb_px"].to_numpy(float), s["fb_vol"].to_numpy(float)
            imp_fb = np.minimum(KAPPA * p["sigma_bps"].to_numpy() * rq / np.maximum(fb_vol, 1.0), 1000.0)
            sv_lim = lq * s["T"].to_numpy(float) + rq * fb_px * (1 - imp_fb / 1e4)
            q["sv"] = np.where(lim, np.nan_to_num(sv_lim), sv_open)
            q["route"], q["at_target"] = route, np.where(lim, lq / p["qty"], 0.0)
            q["d_rs"] = q["sv"] - sv_open
        q["pnl"] = q["sv"] - q["bv"] - O.costs_rs(q["bv"], q["sv"])
        out.append(q)
    return pd.concat(out, ignore_index=True)


# ---- report ----------------------------------------------------------------------------------------------------
def summarize(ev: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    nightly = ev.groupby(["buy_day", "capital_lakh", "strategy", "rule"], as_index=False, sort=False) \
        .agg(pnl=("pnl", "sum"), bv=("bv", "sum"), sv=("sv", "sum"))
    # nights on which a strategy bought nothing count as Rs 0 (not left out of the average)
    keys = nightly[["capital_lakh", "strategy", "rule"]].drop_duplicates()
    grid = keys.merge(pd.DataFrame({"buy_day": np.sort(ev["buy_day"].unique())}), how="cross")
    nightly = grid.merge(nightly, on=["buy_day", "capital_lakh", "strategy", "rule"], how="left") \
        .fillna({"pnl": 0.0, "bv": 0.0, "sv": 0.0})
    base = nightly[nightly["rule"] == BASE][["buy_day", "capital_lakh", "strategy", "pnl"]].rename(columns={"pnl": "pnl0"})
    nightly = nightly.merge(base, on=["buy_day", "capital_lakh", "strategy"])
    rt = ev.assign(lim=ev["route"].eq("limit") * ev["bv"], auc=ev["route"].eq("auction") * ev["bv"],
                   nod=ev["route"].eq("nodata") * ev["bv"], tgt=ev["at_target"] * ev["bv"]) \
        .groupby(["capital_lakh", "strategy", "rule"], sort=False)[["bv", "lim", "auc", "nod", "tgt"]].sum()
    out = []
    for (cap_l, strat, rule), g in nightly.groupby(["capital_lakh", "strategy", "rule"], sort=False):
        pn, dd = g["pnl"].to_numpy(), (g["pnl"] - g["pnl0"]).to_numpy()
        sd, sdd = pn.std(ddof=1), dd.std(ddof=1)
        r = rt.loc[(cap_l, strat, rule)]
        out.append({"capital_lakh": cap_l, "strategy": strat, "rule": rule, "nights": len(g), "rs_per_night": pn.mean(),
                    "vs_open_rs": dd.mean(), "vs_open_t": dd.mean() / (sdd / np.sqrt(len(dd))) if sdd > 0 else np.nan,
                    "net_bps": 1e4 * pn.sum() / max(g["bv"].sum(), 1), "worst_night_rs": pn.min(),
                    "pct_nights_up": 100 * (pn > 0).mean(), "t": pn.mean() / (sd / np.sqrt(len(pn))) if sd > 0 else np.nan,
                    "pct_sold_at_target": 100 * r["tgt"] / r["bv"], "pct_waited": 100 * r["lim"] / r["bv"],
                    "pct_auction": 100 * r["auc"] / r["bv"], "pct_no_minute_data": 100 * r["nod"] / r["bv"]})
    return pd.DataFrame(out), nightly


def why(ev: pd.DataFrame, rule: str, strat: str = "OPTIMIZED top 5", cap_l: int = 5) -> list[str]:
    """Where the difference comes from: the positions split by what happened to the limit order."""
    e = ev[(ev["rule"] == rule) & (ev["strategy"] == strat) & (ev["capital_lakh"] == cap_l)]
    cat = np.select([e["route"].eq("auction"), e["route"].eq("nodata"), e["at_target"] >= 0.999, e["at_target"] > 0],
                    ["sold at the open (gate said no, or high below open)", "no minute data (sold at the open)",
                     "all sold at the target", "part sold at the target, rest at the fallback"],
                    "target never reached, all sold at the fallback")
    g = e.assign(cat=cat).groupby("cat")[["bv", "d_rs"]].sum()
    nights = ev["buy_day"].nunique()
    out = [f"WHERE THE DIFFERENCE COMES FROM - {strat}, Rs {cap_l} lakh, '{rule}' (vs selling the same shares at the open):",
           f"  {'what happened':52s} {'% of rupees':>11s} {'bps vs open':>11s} {'Rs per night':>12s}"]
    for k, r in g.sort_values("bv", ascending=False).iterrows():
        out.append(f"  {k:52s} {100 * r['bv'] / g['bv'].sum():10.0f}% {1e4 * r['d_rs'] / r['bv']:+11.0f} "
                   f"{r['d_rs'] / nights:+12,.0f}")
    return out


def gated_section(S, nightly, ev, c) -> list[str]:
    f0 = lambda v: f"{v:+,.0f}"
    W = 58
    L_ = ["=" * 120, "WAIT FOR THE TARGET ONLY WHEN THE ML PREDICTS THE CLOSE ABOVE THE OPEN (otherwise sell at the open as now)",
          "  [ML close > ML open]     = the evening forecast says the stock closes above where it opens",
          "  [ML close > actual open] = the ML's predicted close is above the actual auction price (checked at 09:07)",
          "  hold to close, no target = no limit: those stocks are simply sold at the close", ""]
    # does the ML tell the faders apart?
    nights = np.sort(c["buy_day"].unique())
    t5 = c[(c["rank"] <= 5) & (c["buy_day"] >= nights[B.BURN])].dropna(subset=["pred_close", "c_next", "h_next"])
    oc = (t5["c_next"] / t5["o_next"] - 1) * 1e4
    ho = (t5["h_next"] / t5["o_next"] - 1) * 1e4
    mid = t5["h_next"] >= (t5["o_next"] + t5["pred_high"]) / 2
    L_ += ["DOES THE ML TELL THE FADERS APART?  top 5 stocks each night, what the stock actually did after the open:",
           f"  {'group':40s} {'% of stock-days':>15s} {'close vs open (avg)':>19s} {'(median)':>9s} {'closed above open':>17s} "
           f"{'high vs open':>12s} {'reached midpoint':>16s}"]
    for lab, m in [("all top 5", np.ones(len(t5), bool)),
                   ("ML close > ML open", t5["pred_close"] > t5["pred_open"]),
                   ("ML close <= ML open", t5["pred_close"] <= t5["pred_open"]),
                   ("ML close > actual open", t5["pred_close"] > t5["o_next"]),
                   ("ML close <= actual open", t5["pred_close"] <= t5["o_next"])]:
        m = np.asarray(m)
        L_.append(f"  {lab:40s} {100 * m.mean():14.0f}% {oc[m].mean():+18.0f}b {oc[m].median():+8.0f}b "
                  f"{100 * (oc[m] > 0).mean():16.0f}% {ho[m].median():+11.0f}b {100 * mid[m].mean():15.0f}%")
    L_.append("")
    rows = [BASE, "midpoint, else at 12:00", "midpoint, else at close"] + GATED
    for nn in TOP:
        L_.append(f"TOP {nn} at Rs 5 lakh")
        L_.append(f"  {'exit rule':{W}s} | {'EQUAL':>7s} {'vs now':>7s} | {'OPTIMIZED':>9s} {'vs now':>7s} {'t':>5s} | "
                  f"{'waited':>6s} {'at target':>9s} {'worst night':>11s}")
        for r in rows:
            e, o = S.loc[(5, f"EQUAL top {nn}", r)], S.loc[(5, f"OPTIMIZED top {nn}", r)]
            L_.append(f"  {r:{W}s} | {f0(e['rs_per_night']):>7s} {f0(e['vs_open_rs']):>7s} | {f0(o['rs_per_night']):>9s} "
                      f"{f0(o['vs_open_rs']):>7s} {o['vs_open_t']:5.1f} | {o['pct_waited']:5.0f}% {o['pct_sold_at_target']:8.0f}% "
                      f"{f0(o['worst_night_rs']):>11s}")
        L_.append("")
    best = max(GATED, key=lambda r: S.loc[(5, "OPTIMIZED top 5", r), "rs_per_night"])
    L_ += [f"BEST GATED RULE for OPTIMIZED top 5 at Rs 5 lakh: '{best}'", "  by capital (Rs per night, now -> this rule):  " +
           "   ".join(f"Rs {cl} L: {f0(S.loc[(cl, 'OPTIMIZED top 5', BASE), 'rs_per_night'])} -> "
                      f"{f0(S.loc[(cl, 'OPTIMIZED top 5', best), 'rs_per_night'])}" for cl in CAPITALS)]
    y = nightly[(nightly["capital_lakh"] == 5) & (nightly["strategy"] == "OPTIMIZED top 5") & nightly["rule"].isin([BASE, best])]
    y = y.assign(year=pd.to_datetime(y["buy_day"]).dt.year).pivot_table(index="year", columns="rule", values="pnl")
    L_.append("  by year (Rs per night, now -> this rule):  " + "   ".join(f"{yr}: {f0(r[BASE])} -> {f0(r[best])}"
                                                                   for yr, r in y.iterrows()))
    L_ += [""] + why(ev, best) + ["", "=" * 120, ""]
    return L_


def report(s: pd.DataFrame, nightly: pd.DataFrame, c: pd.DataFrame, st: pd.DataFrame, ev: pd.DataFrame) -> str:
    f0 = lambda v: f"{v:+,.0f}"
    n, first, last = nightly["buy_day"].nunique(), nightly["buy_day"].min(), nightly["buy_day"].max()
    S = s.set_index(["capital_lakh", "strategy", "rule"])
    L_ = [f"OVERNIGHT TRADE - SELL AT A TARGET INSTEAD OF AT THE OPEN   ({n} nights {first:%Y-%m-%d} .. {last:%Y-%m-%d}; "
          f"Rs per night after all charges)", "",
          "Buy exactly as now (post-close session, same stocks, same amounts). Only the sale changes:",
          "  sell at open (now)       = sell everything in the pre-open auction (the current rule)",
          "  midpoint, else at 12:00  = limit sell at  open + half of (ML predicted high - open);  whatever has not sold by "
          "12:00 is sold at market then",
          "  ... else at 15:15 / close = the same, but the limit waits until 15:15 / the close",
          "  25% / 75% / 100% to high = the target a quarter / three quarters / all the way to the predicted high",
          "  If the ML's predicted high is below the actual open, that stock is sold in the auction as now.", ""]
    rules = list(RULES)
    main = [BASE] + [r for r in rules if r.startswith("midpoint")] + ["pred open mid, else at close"]
    for nn in TOP:
        L_.append(f"TOP {nn} at Rs 5 lakh")
        L_.append(f"  {'exit rule':30s} | {'EQUAL split':>11s} {'vs now':>7s} | {'OPTIMIZED split':>15s} {'vs now':>7s} "
                  f"{'t':>5s} | {'sold at target':>14s} {'worst night':>11s} {'nights up':>9s}")
        for r in main:
            e, o = S.loc[(5, f"EQUAL top {nn}", r)], S.loc[(5, f"OPTIMIZED top {nn}", r)]
            L_.append(f"  {r:30s} | {f0(e['rs_per_night']):>11s} {f0(e['vs_open_rs']):>7s} | {f0(o['rs_per_night']):>15s} "
                      f"{f0(o['vs_open_rs']):>7s} {o['vs_open_t']:5.1f} | {o['pct_sold_at_target']:13.0f}% "
                      f"{f0(o['worst_night_rs']):>11s} {o['pct_nights_up']:8.0f}%")
        L_.append("")
    L_ += gated_section(S, nightly, ev, c)
    L_ += ["HOW FAR TO AIM - OPTIMIZED top 5, Rs 5 lakh, Rs per night (difference vs selling at the open):",
           f"  {'target':18s} " + " ".join(f"{'else at ' + k:>18s}" for k in CUTS)]
    for fr in FRACS:
        L_.append(f"  {_fname(fr):18s} " + " ".join(
            f"{f0(S.loc[(5, 'OPTIMIZED top 5', f'{_fname(fr)}, else at {k}'), 'rs_per_night']):>9s} "
            f"({f0(S.loc[(5, 'OPTIMIZED top 5', f'{_fname(fr)}, else at {k}'), 'vs_open_rs']):>6s})" for k in CUTS))
    L_.append(f"  sell at open (now)  {f0(S.loc[(5, 'OPTIMIZED top 5', BASE), 'rs_per_night'])}")
    L_ += ["", "BY CAPITAL - OPTIMIZED split, Rs per night:  sell at open  /  midpoint else at close  /  midpoint else at 12:00"]
    for nn in TOP:
        L_.append(f"  top {nn:2d}  " + "   ".join(
            f"Rs {cl:2d} L: {f0(S.loc[(cl, f'OPTIMIZED top {nn}', BASE), 'rs_per_night'])} / "
            f"{f0(S.loc[(cl, f'OPTIMIZED top {nn}', 'midpoint, else at close'), 'rs_per_night'])} / "
            f"{f0(S.loc[(cl, f'OPTIMIZED top {nn}', 'midpoint, else at 12:00'), 'rs_per_night'])}" for cl in CAPITALS))
    y = nightly[(nightly["capital_lakh"] == 5) & (nightly["strategy"] == "OPTIMIZED top 5")]
    y = y.assign(year=pd.to_datetime(y["buy_day"]).dt.year).pivot_table(index="year", columns="rule", values="pnl")
    L_ += ["", "BY YEAR - OPTIMIZED top 5, Rs 5 lakh, Rs per night:",
           f"  {'year':6s} " + " ".join(f"{r[:24]:>25s}" for r in main)]
    for yr, r in y.iterrows():
        L_.append(f"  {yr:<6d} " + " ".join(f"{f0(r[m]):>25s}" for m in main))
    L_ += [""] + why(ev, "midpoint, else at close") + [""] + why(ev, "midpoint, else at 12:00")
    # how good is the predicted high as a target
    t5 = c[c["rank"] <= 5].dropna(subset=["pred_high", "h_next"])
    t5 = t5[t5["buy_day"] >= np.sort(c["buy_day"].unique())[B.BURN]]
    up = (t5["pred_high"] / t5["o_next"] - 1) * 1e4
    L_ += ["", "THE ML'S PREDICTED HIGH, top 5 stocks each night:",
           f"  predicted high above the actual open: {100 * (up > 0).mean():.0f}% of stock-nights (median "
           f"{up[up > 0].median():.0f} bps above the open)",
           f"  actual day high reached the predicted high: {100 * (t5['h_next'] >= t5['pred_high']).mean():.0f}%; "
           f"reached the midpoint: {100 * (t5['h_next'] >= (t5['o_next'] + t5['pred_high']) / 2).mean():.0f}% "
           f"(of those with the high above the open: "
           f"{100 * (t5.loc[up > 0, 'h_next'] >= (t5.loc[up > 0, 'o_next'] + t5.loc[up > 0, 'pred_high']) / 2).mean():.0f}%)",
           f"  actual high above the open, median {((t5['h_next'] / t5['o_next'] - 1) * 1e4).median():.0f} bps",
           "", "Notes: vs now = Rs per night more (+) or less (-) than selling at the open, on the same positions; t = that "
           "difference / its standard error (|t| > 2 = unlikely to be luck).",
           f"sold at target = % of the rupees sold at the limit price; the limit gets at most {LIMIT_PART:.0%} of the volume "
           "traded at or above the target, and only if the price traded at least one tick (0.05) above it.",
           f"Fallback market sales pay KAPPA {KAPPA} x sigma x (our sale / the next 15 minutes' volume). Sell days without "
           f"1-minute bars sell at the open ({100 * (st['route'] == 'nodata').mean():.1f}% of candidate stock-days)."]
    return "\n".join(L_)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="phase1")
    ap.add_argument("--model", default="blend")
    ap.add_argument("--half-life", type=float, default=250.0)
    ap.add_argument("--max-seconds", type=float, default=None, help="stop reading minute bars after N s (run again to resume)")
    ap.add_argument("--rebuild-liquidity", action="store_true", help="rebuild data\\liquidity_table.parquet first")
    a = ap.parse_args()
    t0 = time.time()
    c = B.candidates(a.run, a.model, 40)
    hist = c[["buy_day", "pred_bps", "gross_bps"]].copy()
    c = c[c["rank"] <= max(TOP)].copy()
    if not a.rebuild_liquidity and B.TABLE.exists():
        B.L.build = lambda *x, **k: pd.read_parquet(B.TABLE)          # use the table as it is
    c, _ = B.liquidity(c)
    c = add_predictions(c, a.run, a.model)
    log(f"candidates: {len(c):,} stock-nights; predicted high for {100 * c['pred_high'].notna().mean():.1f}%")
    nights = np.sort(c["buy_day"].unique())
    need = c[c["buy_day"] >= nights[B.BURN]][["company_id", "sell_day"]]
    if not cache_bars(need, a.max_seconds):
        return
    st = exit_stats(c[c["buy_day"] >= nights[B.BURN]])
    log(f"exit rules worked out for {st[['company_id', 'buy_day']].drop_duplicates().shape[0]:,} stock-nights "
        f"({time.time() - t0:.0f}s)")
    pos = positions(c, hist, O.Params(max_stocks=40), a.half_life)
    ev = evaluate(pos, c, st)
    s, nightly = summarize(ev)
    text = report(s, nightly, c, st, ev)
    print(text)
    (RUNS / "overnight_exit.txt").write_text(text, encoding="utf-8")
    s.to_csv(RUNS / "overnight_exit_summary.csv", index=False)
    nightly.to_csv(RUNS / "overnight_exit_nightly.csv", index=False)
    log(f"written runs\\overnight_exit.txt / _summary.csv / _nightly.csv ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
