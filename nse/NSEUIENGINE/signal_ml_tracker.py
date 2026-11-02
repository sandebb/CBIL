"""
NSEUIENGINE - signal_ml_tracker.py: a growing record of every v3.11 signal next to NSEMLENGINE's forecast for that
session, and what actually happened afterwards - so that after a few weeks you can measure whether the ML forecasts
improve the swing signals (filter, rank, entry price).

    python signal_ml_tracker.py            record new sessions, fill in outcomes, rebuild the report
    python signal_ml_tracker.py --report   rebuild the report only

Runs with the Python that runs NSEDATAENGINE (pandas + polars); swing_trader.py evening calls it every night.
Reads only: NSEDATAENGINE signals + prices (through its contract), NSEMLENGINE predictions. Writes only:
  NSEUIENGINE\\reports\\signal_ml_ledger.csv (+ .parquet)   one row per session x strategy x stock
  NSEUIENGINE\\reports\\signal_ml_report.txt                  the analysis, rebuilt each run

Prices are stored relative to the signal-day close (1.02 = 2% above it), so later split adjustments in
NSEDATAENGINE's back-adjusted history never distort the record.

Outcome rules (same as the backtest): LIMIT buy filled if the session's low reached the buy price (at the open if it
opened below); PEAD buy-stop filled if the high reached the trigger and the open was not above the limit. Forward
returns are close-to-close from the fill. Stop / target "first" = which was touched first in the next 63 sessions
(both on one day -> stop, conservative).
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import json
import math
import os
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fyers_token import _read as env_value, data_dir  # noqa: E402  (stdlib only)
_ROOT = env_value("NSE_DATA_ROOT") or str((Path(env_value("NSE_ROOT")) if env_value("NSE_ROOT") else HERE.parents[1]) / "data")
REPORTS = (data_dir() if (env_value("NSEUIENGINE_DATA_DIR") or _ROOT)
           else Path(os.environ.get("NSEUIENGINE_HOME") or HERE)) / "reports"
LEDGER = REPORTS / "signal_ml_ledger.csv"
REPORT = REPORTS / "signal_ml_report.txt"
HORIZONS = (1, 5, 10, 20, 63)


def first_dir(*cands) -> Path | None:
    for c in cands:
        if c and Path(c).is_dir():
            return Path(c)
    return None


DATA_HOME = first_dir(os.environ.get("NSEDATAENGINE_HOME"), os.path.expandvars(os.path.expandvars(__import__("os").environ.get("NSE_ROOT", r"D:\CBIL") + r"/code/NSEDATAENGINE")), HERE.parent / "NSEDATAENGINE")
ML_HOME = first_dir(os.environ.get("NSEMLENGINE_HOME"), os.path.expandvars(os.path.expandvars(__import__("os").environ.get("NSE_ROOT", r"D:\CBIL") + r"/code/NSEMLENGINE")), HERE.parent / "NSEMLENGINE")
SIG_DIR = first_dir(env_value("NSEDATAENGINE_SIGNALS_DIR"), _ROOT and Path(_ROOT) / "NSEDATAENGINE" / "signals",
                    DATA_HOME / "signals" if DATA_HOME else None)
ML_DIR = first_dir(env_value("NSEMLENGINE_WORK_DIR") and Path(env_value("NSEMLENGINE_WORK_DIR")) / "predictions",
                   _ROOT and Path(_ROOT) / "NSEMLENGINE" / "work" / "predictions",
                   ML_HOME / "work" / "predictions" if ML_HOME else None)


def f(v, d=np.nan):
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except (TypeError, ValueError):
        return d


def fill_prob(buy, p10, p50, p90):
    """P(session low <= buy) read off the forecast quantiles of the low (piecewise-linear between them)."""
    if not all(map(math.isfinite, (buy, p10, p50, p90))) or p90 <= p10:
        return np.nan
    if buy <= p10:
        return max(0.0, 0.1 - 0.1 * (p10 - buy) / max(p50 - p10, 1e-9))
    if buy <= p50:
        return 0.1 + 0.4 * (buy - p10) / max(p50 - p10, 1e-9)
    if buy <= p90:
        return 0.5 + 0.4 * (buy - p50) / max(p90 - p50, 1e-9)
    return min(1.0, 0.9 + 0.1 * (buy - p90) / max(p90 - p50, 1e-9))


# --------------------------------------------------------------------------------------------------------------
# record: signals + forecast
# --------------------------------------------------------------------------------------------------------------
def record_session(sig_file: Path, log) -> pd.DataFrame | None:
    session = datetime.strptime(sig_file.stem.split("_")[-1], "%Y%m%d").date()
    meta_p = SIG_DIR / f"session_{session:%Y%m%d}.json"
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    sig = pd.read_csv(sig_file)
    if sig.empty:
        return pd.DataFrame()
    sig_date = meta.get("signal_date")
    ml = None
    if sig_date and ML_DIR:
        mp = ML_DIR / f"predictions_{sig_date.replace('-', '')}.csv"
        if mp.exists():
            ml = pd.read_csv(mp).drop_duplicates("company_id").set_index("company_id")
    if ml is None:
        age = (date.today() - session).days
        if age < 4:
            log(f"  {session}: NSEMLENGINE forecast for {sig_date} not there yet - will retry")
            return None
        log(f"  {session}: no NSEMLENGINE forecast for {sig_date} - recorded without it")
    rows = []
    for r in sig.to_dict("records"):
        c = f(r.get("close"))
        rel = (lambda x: f(x) / c) if c and c > 0 else (lambda x: np.nan)
        row = {
            "session": f"{session}", "signal_date": sig_date, "symbol": r["symbol"], "strategy": r["strategy"],
            "in_book": bool(r.get("in_book")), "regime": r.get("regime"), "regime_allowed": bool(r.get("regime_allowed")),
            "order": r.get("order"), "signal_close": c, "buy_rel": rel(r.get("trigger_or_limit")),
            "limit_rel": rel(r.get("limit_price")), "stop_rel": rel(r.get("stop")), "target_rel": rel(r.get("target")),
            "valid_sessions": f(r.get("valid_sessions"), 1), "max_hold": f(r.get("max_hold")),
            "bt_profit_factor": f(r.get("bt_profit_factor")), "bt_win_pct": f(r.get("bt_win_pct")),
            "turnover_rank": f(r.get("turnover_rank")), "ml_present": False,
        }
        if ml is not None and r["symbol"] in ml.index:
            m = ml.loc[r["symbol"]]
            mc = f(m.get("close_today"))
            mrel = (lambda k: f(m.get(k)) / mc) if mc and mc > 0 else (lambda k: np.nan)
            row.update({
                "ml_present": True, "ml_model": m.get("model"), "ml_exp_return_pct": f(m.get("exp_return_pct")),
                "ml_exp_gap_pct": f(m.get("exp_gap_pct")), "ml_exp_range_pct": f(m.get("exp_range_pct")),
                "ml_rank": f(m.get("rank_return")), "ml_daily_vol_pct": f(m.get("daily_vol_pct")),
                **{f"ml_{k}_rel": mrel(k) for k in ("open_p50", "low_p10", "low_p50", "low_p90", "high_p10", "high_p50",
                                                    "high_p90", "close_p10", "close_p50", "close_p90")},
            })
            if row["order"] == "BUY-STOP-LIMIT":      # buy-stop fills when the HIGH reaches the trigger
                hp = fill_prob(row["buy_rel"], row["ml_high_p10_rel"], row["ml_high_p50_rel"], row["ml_high_p90_rel"])
                row["ml_fill_prob"] = 1 - hp if math.isfinite(hp) else np.nan
            else:
                row["ml_fill_prob"] = fill_prob(row["buy_rel"], row["ml_low_p10_rel"], row["ml_low_p50_rel"],
                                                row["ml_low_p90_rel"])
        rows.append(row)
    log(f"  {session}: recorded {len(rows)} signals ({sum(x['ml_present'] for x in rows)} with an ML forecast)")
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------------------------------------
# outcomes from NSEDATAENGINE prices
# --------------------------------------------------------------------------------------------------------------
def load_prices(symbols, start) -> dict:
    import polars as pl
    sys.path.insert(0, str(DATA_HOME))
    import nse_contract as C
    p = C.file_path("raw_ohlcv", "master")
    df = (pl.scan_parquet(p).filter(pl.col("company_id").is_in(list(symbols)) & (pl.col("datetime") >= start))
          .select("company_id", "datetime", "open", "high", "low", "close").collect().to_pandas())
    df["datetime"] = pd.to_datetime(df["datetime"]).dt.normalize()
    return {s: g.drop(columns="company_id").set_index("datetime").sort_index() for s, g in df.groupby("company_id")}


def outcome(row: dict, px: pd.DataFrame | None) -> dict:
    out = {}
    if px is None or px.empty:
        return out
    sd, ss = pd.Timestamp(row["signal_date"]), pd.Timestamp(row["session"])
    if sd not in px.index or ss not in px.index:
        return out
    base = px.at[sd, "close"]                     # today's (re-adjusted) signal-day close: ratios stay valid
    after = px.loc[px.index >= ss]
    o, h, l, c = (after.iloc[0][k] / base for k in ("open", "high", "low", "close"))
    out.update(s_open_rel=o, s_high_rel=h, s_low_rel=l, s_close_rel=c, s_ret_pct=(c - 1) * 100, outcome_session=1)
    buy, lim = row["buy_rel"], row.get("limit_rel", row["buy_rel"])
    filled, fill, fill_i = False, np.nan, None
    n_try = int(row.get("valid_sessions") or 1) if row.get("order") == "BUY-STOP-LIMIT" else 1
    for i in range(min(n_try, len(after))):
        bo, bh, bl = (after.iloc[i][k] / base for k in ("open", "high", "low"))
        if row.get("order") == "BUY-STOP-LIMIT":
            if bh >= buy and (bo <= lim or bl <= lim):
                filled, fill, fill_i = True, min(max(bo, buy), lim), i
                break
        elif bl <= buy:
            filled, fill, fill_i = True, min(bo, buy), i
            break
    out.update(filled=filled, fill_rel=fill)
    if not filled:
        return out
    fill_at_open = bool(abs(fill - after.iloc[fill_i]["open"] / base) < 1e-9)
    path = after.iloc[fill_i:]
    rel = path / base
    for n in HORIZONS:
        if len(rel) > n:
            out[f"ret_{n}d_pct"] = (rel["close"].iloc[n] / fill - 1) * 100
    win = rel.iloc[:64]
    out["max_up_pct"] = (win["high"].max() / fill - 1) * 100
    out["max_down_pct"] = (win["low"].min() / fill - 1) * 100
    first = "none_yet"
    for i in range(len(win)):
        hit_s = win["low"].iloc[i] <= row["stop_rel"]
        # on the fill day the high only counts if the buy filled at the open (the backtest's rule)
        hit_t = win["high"].iloc[i] >= row["target_rel"] and (i > 0 or fill_at_open)
        if hit_s:
            first = "stop"
            break
        if hit_t:
            first = "target"
            break
    if first == "none_yet" and len(win) >= 64:
        first = "neither_63d"
    out["first_touch"] = first
    out["stopped_day1"] = bool(win["low"].iloc[0] <= row["stop_rel"])
    out["sessions_observed"] = len(path)
    return out


# --------------------------------------------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------------------------------------------
def report(led: pd.DataFrame) -> str:
    L = []
    w = L.append
    w(f"Signal x ML ledger report - {datetime.now():%Y-%m-%d %H:%M}")
    w("=" * 100)
    if led.empty:
        w("no data yet")
        return "\n".join(L)
    d = led.copy()
    for c in ("filled", "ml_present", "regime_allowed", "in_book"):
        if c in d:
            d[c] = d[c].astype(str).str.lower().isin(("true", "1"))
    done = d[d.get("outcome_session", pd.Series(np.nan, index=d.index)).notna()] if "outcome_session" in d else d.iloc[0:0]
    w(f"sessions recorded: {d['session'].nunique()} ({d['session'].min()} .. {d['session'].max()}); signal rows: "
      f"{len(d)}; with outcomes: {len(done)}; with an ML forecast: {int(d['ml_present'].sum())}")
    if done.empty:
        w("\noutcomes appear after NSEDATAENGINE has the session's prices (the evening update)")
        return "\n".join(L)
    u = done.drop_duplicates(["session", "symbol"])          # one row per stock and session for market-level stats
    w("\n1. FILLS  (limit at the signal close / PEAD buy-stop)")
    w(f"   all stocks: {u['filled'].mean() * 100:.1f}% filled ({int(u['filled'].sum())} of {len(u)})")
    m = u[u["ml_present"] & u["ml_fill_prob"].notna()] if "ml_fill_prob" in u else u.iloc[0:0]
    if len(m):
        w(f"   ML predicted fill chance (mean): {m['ml_fill_prob'].mean() * 100:.1f}%  vs actual "
          f"{m['filled'].mean() * 100:.1f}%  (n={len(m)})")
        bins = pd.cut(m["ml_fill_prob"], [0, .5, .7, .85, 1.0], include_lowest=True)
        g = m.groupby(bins, observed=True)["filled"].agg(["mean", "size"])
        for k, r in g.iterrows():
            w(f"     predicted {str(k):14s}: actual {r['mean'] * 100:5.1f}%  (n={int(r['size'])})")
        fu = u[u["filled"]]
        nf = u[~u["filled"]]
        w(f"   session close vs signal close: filled {fu['s_ret_pct'].mean():+.2f}% | not filled {nf['s_ret_pct'].mean():+.2f}%"
          f"  (the cost of not chasing)")
    w("\n2. DOES THE ML RANK PREDICT THE NEXT SESSION?  (all signal stocks, filled or not)")
    m2 = u[u["ml_present"] & u["ml_exp_return_pct"].notna() & u["s_ret_pct"].notna()]
    if len(m2) >= 5:
        ics = m2.groupby("session").apply(lambda g: g["ml_exp_return_pct"].rank().corr(g["s_ret_pct"].rank())
                                          if len(g) >= 5 else np.nan).dropna()
        w(f"   rank IC per session: mean {ics.mean():+.3f} over {len(ics)} sessions "
          f"(t = {ics.mean() / (ics.std(ddof=1) / math.sqrt(len(ics))) if len(ics) > 2 and ics.std() > 0 else float('nan'):+.2f};"
          f" README backtest: about +0.07)")
        q = pd.qcut(m2["ml_rank"].rank(method="first"), 3, labels=["low", "mid", "high"])
        for k, g in m2.groupby(q, observed=True):
            w(f"   ML rank {k:4s}: next-session {g['s_ret_pct'].mean():+.2f}%  (n={len(g)})")
    else:
        w("   not enough rows yet")
    w("\n3. FILLED TRADES BY ML RANK  (forward returns from the fill, %)")
    f_ = done[done["filled"] & done["ml_present"]].drop_duplicates(["session", "symbol"])
    cols = [f"ret_{n}d_pct" for n in HORIZONS if f"ret_{n}d_pct" in f_]
    if len(f_) >= 6 and cols:
        q = pd.qcut(f_["ml_rank"].rank(method="first"), 3, labels=["low", "mid", "high"])
        t = f_.groupby(q, observed=True)[cols].agg(["mean", "count"])
        w("   " + t.round(2).to_string().replace("\n", "\n   "))
    else:
        w("   not enough filled trades yet")
    w("\n4. BY STRATEGY  (filled, one row per stock and session)")
    fs = done[done["filled"]]
    if len(fs):
        agg = {"symbol": "size"}
        agg.update({c: "mean" for c in cols})
        t = fs.groupby("strategy").agg(agg).rename(columns={"symbol": "trades"})
        if "first_touch" in fs:
            ft = fs.groupby("strategy")["first_touch"].value_counts().unstack(fill_value=0)
            t = t.join(ft)
        w("   " + t.round(2).to_string().replace("\n", "\n   "))
    w("\nRead with care: a few sessions are noise. Judge after ~20 sessions / 200+ signals.")
    return "\n".join(L)


def wait_for_ml(minutes: int, log) -> None:
    """The evening order is: NSEDATAENGINE update (19:30) -> NSEMLENGINE forecast (20:00, longer on retrain nights)
    -> scan (20:30). Wait until the forecast for the newest signal session exists, at most `minutes`."""
    import time
    metas = sorted(SIG_DIR.glob("session_*.json"))
    if not metas or not ML_DIR:
        return
    sd = json.loads(metas[-1].read_text()).get("signal_date", "")
    target = ML_DIR / f"predictions_{sd.replace('-', '')}.csv"
    end = time.time() + minutes * 60
    while not target.exists() and time.time() < end:
        log(f"waiting for NSEMLENGINE forecast {target.name} ...")
        time.sleep(300)
    log(f"forecast {target.name} {'found' if target.exists() else 'still missing - recorded on a later run'}")


# --------------------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true", help="rebuild the report only")
    ap.add_argument("--wait-ml", type=int, default=0, help="minutes to wait for the newest session's NSEMLENGINE "
                                                          "forecast (the scheduled task uses 120)")
    a = ap.parse_args(argv)
    REPORTS.mkdir(parents=True, exist_ok=True)

    def log(m):
        print(f"{datetime.now():%H:%M:%S}  {m}", flush=True)

    led = pd.read_csv(LEDGER) if LEDGER.exists() else pd.DataFrame()
    if not a.report and a.wait_ml and SIG_DIR:
        wait_for_ml(a.wait_ml, log)
    if not a.report:
        if not SIG_DIR:
            log("NSEDATAENGINE signals folder not found")
            return 1
        have = set(led["session"].astype(str)) if len(led) else set()
        new = []
        for fpath in sorted(SIG_DIR.glob("signals_for_*.csv")):
            s = datetime.strptime(fpath.stem.split("_")[-1], "%Y%m%d").date()
            if f"{s}" not in have:
                rec = record_session(fpath, log)
                if rec is not None and len(rec):
                    new.append(rec)
        if new:
            led = pd.concat([led, *new], ignore_index=True) if len(led) else pd.concat(new, ignore_index=True)
        if len(led):
            open_rows = led.index if "first_touch" not in led else led.index[
                led["first_touch"].isna() | led["first_touch"].isin(["none_yet"]) |
                (pd.to_numeric(led.get("sessions_observed"), errors="coerce").fillna(0) < max(HORIZONS) + 1)]
            if len(open_rows) and DATA_HOME:
                start = pd.Timestamp(min(led.loc[open_rows, "signal_date"].dropna()))
                px = load_prices(set(led.loc[open_rows, "symbol"]), start.to_pydatetime())
                n = 0
                for i in open_rows:
                    o = outcome(led.loc[i].to_dict(), px.get(led.at[i, "symbol"]))
                    for k, v in o.items():
                        led.at[i, k] = v
                    n += bool(o)
                log(f"outcomes updated for {n} rows (prices to {max((p.index.max() for p in px.values()), default='-')})")
            led.to_csv(LEDGER, index=False)
            try:
                led.to_parquet(LEDGER.with_suffix(".parquet"), index=False)
            except Exception:
                pass
    REPORT.write_text(report(led), encoding="utf-8")
    log(f"ledger {LEDGER} ({len(led)} rows); report {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
