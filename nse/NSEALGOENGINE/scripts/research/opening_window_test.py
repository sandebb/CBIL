"""
Should some intraday strategies trade before 09:30?  Uses 1-minute bars from `nsealgo fetch-history` (data/history.db)
and NSEDATAENGINE's daily prices / RSI for the signals (all known before the open, except the gap, known at 09:15).

Universe: decided EACH DAY from what was known the evening before (see signals()); the download list
config/universes/liquid_union.csv = every stock that qualified on at least one day of the test window.
Signals
  GAPDN   open more than 1% below yesterday's close   -> long
  GAPUP   open more than 1% above yesterday's close   -> short
  RSI80   yesterday's RSI-14 above 80                  -> short
Entries at 09:15 (opening-auction price = the first bar's open), 09:20, 09:30, 09:45; exit 14:55 (all intraday out by
15:00). Stops (checked on every 1-minute bar from the entry): none, 1%, 2%, 1 x ATR, yesterday's low/high, opening
range low/high - see stop_level(). Costs: Shoonya intraday charges 4.7 bps + slippage 2 bps at the auction, 5 bps otherwise.
Also: how much more the price moves per minute in 09:15-09:30 than later in the day.

    .venv\\Scripts\\python scripts\\research\\opening_window_test.py
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from nsealgo.config import load_config, resolve_path  # noqa: E402
from nsealgo.data.history import HistoryStore  # noqa: E402

DATA = Path(os.environ.get("NSEDATAENGINE_DATA_DIR", __import__("os").environ.get("NSE_ROOT", r"D:\CBIL") + r"/code\NSEDATAENGINE\data"))
ENTRIES = ("09:15", "09:20", "09:30", "09:45")
EXIT = "14:55"
STOPS = ("none", "1%", "2%", "ATR", "PDL", "ORL")
STOP_SLIP_BPS = 5.0          # a stop fills this much worse than its level (stop-limit / market in a fast move)
CHARGES = 4.7


REF = Path(os.environ.get("NSEDATAENGINE_REF_DIR", str(DATA.parent / "data_ref")))
MIN_TURNOVER, MIN_PRICE, LOCK_DAYS, MAX_PLEDGE = 25e7, 50.0, 20, 50.0


def signals(keys, start, end) -> pd.DataFrame:
    """Per stock and day, using ONLY what was known before that day's open (point in time):
    yesterday's close and RSI-14, the 60-day median turnover up to yesterday, days locked at the price limit
    (high = low) in the last 20 sessions, the latest promoter-pledge disclosure, the day's ASM/GSM snapshot."""
    syms = [k.split(":")[1] for k in keys]
    since = pd.Timestamp(start) - pd.Timedelta(days=150)
    flt = [("datetime", ">=", since), ("company_id", "in", syms)]
    px = pd.read_parquet(DATA / "raw_ohlcv.parquet", columns=["company_id", "datetime", "high", "low", "close", "volume"],
                         filters=flt)
    rsi = pd.read_parquet(DATA / "features_output_clean.parquet", columns=["company_id", "datetime", "rsi_14"],
                          filters=flt)
    d = px.merge(rsi, on=["company_id", "datetime"], how="left").sort_values(["company_id", "datetime"])
    d["turn"] = d["close"] * d["volume"]
    d["locked"] = (d["high"] == d["low"]).astype(float)
    g = d.groupby("company_id")
    d["prev_close"], d["rsi_y"] = g["close"].shift(1), g["rsi_14"].shift(1)
    d["prev_high"], d["prev_low"] = g["high"].shift(1), g["low"].shift(1)
    tr = np.maximum(d["high"] - d["low"], np.maximum((d["high"] - d["prev_close"]).abs(), (d["low"] - d["prev_close"]).abs()))
    d["atr_y"] = tr.groupby(d["company_id"]).transform(lambda x: x.rolling(14, min_periods=10).mean().shift(1))
    d["turn60_y"] = g["turn"].transform(lambda x: x.rolling(60, min_periods=40).median().shift(1))
    d["locked20_y"] = g["locked"].transform(lambda x: x.rolling(LOCK_DAYS, min_periods=1).sum().shift(1))
    d["date"] = pd.to_datetime(d["datetime"]).dt.date
    d["pledge_pct"] = np.nan
    pf = REF / "pledge.parquet"
    if pf.exists():
        pl_ = pd.read_parquet(pf, columns=["company_id", "disclosed", "pledged_pct_of_promoter"]).dropna()
        pl_["disclosed"] = (pd.to_datetime(pl_["disclosed"]) + pd.Timedelta(days=1)).astype("datetime64[ns]")
        d["datetime"] = pd.to_datetime(d["datetime"]).astype("datetime64[ns]")       # usable from the next day ^
        pl_["company_id"] = pl_["company_id"].astype(str)
        d["company_id"] = d["company_id"].astype(str)
        d = pd.merge_asof(d.sort_values("datetime"), pl_.sort_values("disclosed"), left_on="datetime",
                          right_on="disclosed", by="company_id", direction="backward")
        d["pledge_pct"] = d["pledged_pct_of_promoter"]
    d["surveillance"] = False
    sf = REF / "surveillance.parquet"
    if sf.exists():
        sv = pd.read_parquet(sf, columns=["company_id", "date"])
        sv["date"] = pd.to_datetime(sv["date"]).dt.date
        d["surveillance"] = pd.MultiIndex.from_frame(d[["company_id", "date"]]).isin(
            pd.MultiIndex.from_frame(sv[["company_id", "date"]]))
    d["liquid"] = (d["turn60_y"] >= MIN_TURNOVER) & (d["prev_close"] >= MIN_PRICE)
    d["clean"] = d["liquid"] & (d["locked20_y"].fillna(0) == 0) & ~(d["pledge_pct"] > MAX_PLEDGE) & ~d["surveillance"]
    d = d[(d["date"] >= start) & (d["date"] <= end)]
    return d.set_index(["company_id", "date"])


def stop_level(name, side, e, bars, s, t_entry):
    """Stop price for one trade, or None (no stop / not available).
      1% / 2%  fixed distance from the entry
      ATR      1 x yesterday's 14-day average true range
      PDL      yesterday's low (long) / high (short)
      ORL      the opening range 09:15-09:29 low (long) / high (short); only for entries from 09:30"""
    if name == "none":
        return None
    if name in ("1%", "2%"):
        k = float(name[:-1]) / 100
        return e * (1 - k) if side > 0 else e * (1 + k)
    if name == "ATR":
        a = s.get("atr_y")
        return (e - a if side > 0 else e + a) if a and a > 0 else None
    if name == "PDL":
        lv = s.get("prev_low") if side > 0 else s.get("prev_high")
        return lv if lv and ((side > 0 and lv < e) or (side < 0 and lv > e)) else None
    if name == "ORL":
        if t_entry < "09:30":
            return None
        rng = bars.loc["09:15":"09:29"]
        if rng.empty:
            return None
        lv = rng["low"].min() if side > 0 else rng["high"].max()
        return lv if (side > 0 and lv < e) or (side < 0 and lv > e) else None
    return None


def trade(bars: pd.DataFrame, side: int, t_entry: str, stop: str, s) -> float | None:
    """Net bps of one trade on one day's 1-minute bars (index = minute time 'HH:MM'). The stop is checked on every
    minute from the entry minute on: the first bar whose low (long) / high (short) reaches it closes the trade at the
    stop - or at that bar's open if the price jumped past it - minus STOP_SLIP_BPS."""
    if t_entry not in bars.index or EXIT not in bars.index:
        return None
    e = bars.at[t_entry, "open"]
    path = bars.loc[t_entry:EXIT]
    exit_px = path.iloc[-1]["close"]
    if stop != "none":
        lvl = stop_level(stop, side, e, bars, s, t_entry)
        if lvl is None:
            return None
        hit = path["low"] <= lvl if side > 0 else path["high"] >= lvl
        if hit.any():
            b = path[hit].iloc[0]
            px = min(b["open"], lvl) if side > 0 else max(b["open"], lvl)
            exit_px = px * (1 - STOP_SLIP_BPS / 1e4) if side > 0 else px * (1 + STOP_SLIP_BPS / 1e4)
    slip = 2.0 if t_entry == "09:15" else 5.0
    return side * (exit_px / e - 1) * 1e4 - CHARGES - slip - 2.5          # 2.5 bps exit slippage


def main():
    cfg = load_config(ROOT / "config" / "system.yaml")
    h = HistoryStore(resolve_path(cfg, "data_dir") / "history.db", read_only=True)
    keys = [k for k in h.keys("minute")]
    if not keys:
        print("no minute history yet - run: nsealgo fetch-history --broker fyers --universe liquid_union "
              "--minute-universe liquid_union --days 30 --minute-days 125")
        return
    days = sorted({d for k in keys[:20] for d in h.minute_dates(k)})
    start, end = days[0], days[-1]
    sig = signals(keys, start, end)
    res, vol = [], []
    for n, k in enumerate(keys):
        m = h.minute(k, start, end)
        if m.empty:
            continue
        m = m.copy()
        m["date"], m["hm"] = m.index.date, m.index.strftime("%H:%M")
        sym = k.split(":")[1]
        for d, bars in m.groupby("date"):
            bars = bars.set_index("hm")
            r = (bars["close"] / bars["open"] - 1).abs() * 1e4
            vol.append(r.groupby(np.where(bars.index < "09:30", "09:15-09:30",
                                          np.where(bars.index < "10:00", "09:30-10:00", "10:00-14:55"))).mean())
            if (sym, d) not in sig.index or "09:15" not in bars.index:
                continue
            s = sig.loc[(sym, d)]
            if isinstance(s, pd.DataFrame):
                s = s.iloc[0]
            if not s["liquid"]:
                continue
            gap = bars.at["09:15", "open"] / s["prev_close"] - 1 if s["prev_close"] > 0 else np.nan
            for name, side, on in (("GAPDN", 1, gap < -0.01), ("GAPUP", -1, gap > 0.01), ("RSI80", -1, s["rsi_y"] > 80)):
                if not on:
                    continue
                for te in ENTRIES:
                    for st in STOPS:
                        v = trade(bars, side, te, st, s)
                        if v is not None:
                            res.append((name, te, st, d, v, bool(s["clean"])))
        if (n + 1) % 50 == 0:
            print(f"  {n + 1}/{len(keys)} stocks", flush=True)
    R = pd.DataFrame(res, columns=["signal", "entry", "stop", "date", "bps", "clean"])
    excluded = R[(R.entry == "09:15") & (R.stop == "none") & ~R.clean]
    V = pd.DataFrame(vol).mean()
    out = [f"Opening-window test: {len(keys)} stocks, {len(days)} sessions ({start} .. {end}), exit {EXIT}, "
           f"net of charges + slippage", "",
           "average absolute 1-minute move (bps): " + ", ".join(f"{k} {v:.1f}" for k, v in V.items()), ""]
    out.append(f"universe each day (point in time): 60-day median turnover >= Rs {MIN_TURNOVER / 1e7:.0f} cr and price "
               f">= Rs {MIN_PRICE:.0f}; 'clean' also excludes: locked at the price limit in the last {LOCK_DAYS} sessions, "
               f"promoter pledge > {MAX_PLEDGE:.0f}%, on the ASM/GSM list that day")
    if len(excluded):
        out.append(f"trades removed by the clean filter: {len(excluded)}, their average {excluded.bps.mean():+.1f} bps "
                   f"(09:15 entry, no stop)")
    out.append("")
    R = pd.concat([R.assign(set="all liquid"), R[R.clean].assign(set="clean")])
    for (sgn, which), g in R.groupby(["signal", "set"]):
        sgn = f"{sgn} - {which}"
        out.append(f"{sgn}  ({g[(g.entry == '09:15') & (g.stop == 'none')].shape[0]} trades)")
        out.append(f"   {'entry':6s} " + " ".join(f"{'stop ' + s:>22s}" for s in STOPS))
        for te in ENTRIES:
            cells = []
            for st in STOPS:
                x = g[(g.entry == te) & (g.stop == st)]
                if x.empty:
                    cells.append("-")
                    continue
                daily = x.groupby("date")["bps"].mean()
                sd = daily.std(ddof=1)
                t = daily.mean() / (sd / np.sqrt(len(daily))) if len(daily) > 2 and sd > 1e-6 else np.nan
                cells.append(f"{x.bps.mean():+7.1f} bps {100 * (x.bps > 0).mean():3.0f}% t{t:+5.1f}")
            out.append(f"   {te:6s} " + " ".join(f"{c:>22s}" for c in cells))
        out.append("")
    out.append("bps = average net return per trade; % = winning trades; t = per-day average / its standard error.")
    out.append(f"stops: 1% / 2% from entry; ATR = 1 x 14-day average true range; PDL = yesterday's low (long) / high "
               f"(short); ORL = 09:15-09:29 opening-range low / high (entries from 09:30 only). Checked minute by minute; "
               f"a hit exits at the stop (or the bar's open if it gapped through) minus {STOP_SLIP_BPS:.0f} bps.")
    text = "\n".join(out)
    print(text)
    (ROOT / "runs").mkdir(exist_ok=True)
    (ROOT / "runs" / "opening_window_test.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
