"""
NSEUIENGINE - edge_report.py: does the strategy book have an edge AFTER COSTS, live and on paper, compared with the
v3.11 backtest?  Runs weekly (scheduled task NSEUIENGINE-EdgeReport, Saturday 10:00).

    python edge_report.py                 rebuild everything from the signal files + prices + the trade journal
    python edge_report.py --chase 0.5     the entry allowance used for the "fill rate with chase" column

Three views, per strategy:
  BACKTEST  bt_win_pct / bt_avg_ret_pct / bt_profit_factor, as printed in each night's signal file
  PAPER     every signal NSEDATAENGINE published, traded exactly by its exit plan on the real prices that followed
            (buy at the signal price as the backtest does; v3.11 exit rule -> next open from exits_for_<day>.csv;
            protective stop; target; strategy time stop; 3-month time exit).  Rebuilt from scratch every run.
  LIVE      what swing_trader.py really did (reports\\trade_journal.csv + open positions + unfilled orders), with
            the actual Fyers delivery charges, and the gap to the same signal's paper trade.

Charges are Fyers delivery (equity) charges, editable under "charges" in state\\trader_config.json:
  brokerage min(Rs 20, 0.3%) per side, STT 0.1% per side, NSE txn 0.00297%, SEBI Rs 10/crore, stamp 0.015% (buy),
  GST 18% on brokerage + txn + SEBI + DP, DP charge Rs 12.5 per sell.
Net returns are shown for Rs 5,000, Rs 25,000 and Rs 1,00,000 per position - the fixed parts (DP, brokerage cap)
hurt small positions most; STT (0.2% a round trip) is the floor at any size.

Writes:  reports\\edge_report.txt              the report (read this)
         reports\\edge\\paper_trades.csv        one row per signal: fill, exit, reason, gross / net returns
         reports\\edge\\edge_history.csv        one row per strategy per run - how the picture moves week to week
Reads only.  Never places or changes orders.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import signal_ml_tracker as T  # noqa: E402  (signal folder, price loader, report folder - one source of truth)

REPORTS = T.REPORTS
DATA = REPORTS.parent
OUT = REPORTS / "edge"
REPORT = REPORTS / "edge_report.txt"
JOURNAL = REPORTS / "trade_journal.csv"
STATE = DATA / "state" / "trader_state.json"
CONFIG = DATA / "state" / "trader_config.json"

COSTS = {"brokerage_pct": 0.3, "brokerage_cap": 20.0, "stt_pct": 0.1, "exch_pct": 0.00297, "sebi_per_cr": 10.0,
         "stamp_buy_pct": 0.015, "gst_pct": 18.0, "dp_per_sell": 12.5}
SIZES = (5_000, 25_000, 100_000)
MIN_TRADES = 20            # below this a strategy's numbers are noise
TIME_3M = 63               # sessions - the trader's 3-month time exit for target strategies


def f(v, d=np.nan):
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except (TypeError, ValueError):
        return d


def charges(buy_val: float, sell_val: float, c: dict = COSTS) -> float:
    """Rs of charges for one delivery round trip."""
    brk = min(c["brokerage_cap"], buy_val * c["brokerage_pct"] / 100) + \
        min(c["brokerage_cap"], sell_val * c["brokerage_pct"] / 100)
    turn = buy_val + sell_val
    stt = turn * c["stt_pct"] / 100
    exch = turn * c["exch_pct"] / 100
    sebi = turn * c["sebi_per_cr"] / 1e7
    stamp = buy_val * c["stamp_buy_pct"] / 100
    gst = (brk + exch + sebi + c["dp_per_sell"]) * c["gst_pct"] / 100
    return brk + stt + exch + sebi + stamp + c["dp_per_sell"] + gst


def net_pct(gross_pct: float, value: float, c: dict = COSTS) -> float:
    sell = value * (1 + gross_pct / 100)
    return (sell - value - charges(value, sell, c)) / value * 100


# --------------------------------------------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------------------------------------------
def load_signals(sig_dir: Path, since: date | None) -> pd.DataFrame:
    frames = []
    for fp in sorted(sig_dir.glob("signals_for_*.csv")):
        session = datetime.strptime(fp.stem.split("_")[-1], "%Y%m%d").date()
        if since and session < since:
            continue
        df = pd.read_csv(fp, encoding="utf-8-sig")
        if df.empty:
            continue
        meta_p = sig_dir / f"session_{session:%Y%m%d}.json"
        meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
        df["session"] = f"{session}"
        df["signal_date"] = meta.get("signal_date")
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    s = pd.concat(frames, ignore_index=True)
    for col in ("in_book", "regime_allowed"):
        s[col] = s[col].astype(str).str.lower().eq("true") if col in s else False
    return s


def load_exits(sig_dir: Path, pairs: set, since: date | None) -> tuple[dict, set]:
    """{session date: {(strategy, symbol), ...}} for the pairs we care about; and the set of sessions that HAVE a file."""
    by_day, have = {}, set()
    for fp in sorted(sig_dir.glob("exits_for_*.csv")):
        d = datetime.strptime(fp.stem.split("_")[-1], "%Y%m%d").date()
        if since and d < since:
            continue
        have.add(d)
        try:
            ex = pd.read_csv(fp, usecols=["strategy", "symbol"], encoding="utf-8-sig")
        except Exception:
            continue
        by_day[d] = {p for p in zip(ex["strategy"], ex["symbol"]) if p in pairs}
    return by_day, have


# --------------------------------------------------------------------------------------------------------------
# paper trade: one signal, exactly by its plan
# --------------------------------------------------------------------------------------------------------------
def simulate(r: dict, px: pd.DataFrame | None, exits: dict, have_exits: set, chase_pct: float) -> dict:
    out = {"status": "no_prices", "filled": False}
    if px is None or px.empty or not r.get("signal_date"):
        return out
    sd, ss = pd.Timestamp(r["signal_date"]), pd.Timestamp(r["session"])
    if sd not in px.index:
        return out
    after = px.loc[px.index >= ss]
    if after.empty:
        out["status"] = "pending"
        return out
    c = f(r.get("close"))
    if not (c > 0):
        return out
    base = px.at[sd, "close"]                  # ratios to the signal-day close survive later split adjustments
    rows = after[["open", "high", "low", "close"]].to_numpy(dtype=float) / base
    dates = list(after.index)
    buy = f(r.get("trigger_or_limit")) / c
    lim = f(r.get("limit_price"), f(r.get("trigger_or_limit"))) / c
    stop = f(r.get("stop"), 0) / c
    tgt = f(r.get("target"), 0) / c
    stopbuy = str(r.get("order")) == "BUY-STOP-LIMIT"
    n_try = int(f(r.get("valid_sessions"), 1) or 1) if stopbuy else 1

    if not stopbuy:
        out["fill_with_chase"] = bool(rows[0][2] <= buy * (1 + chase_pct / 100))
    fill_i, fill = None, np.nan
    for i in range(min(n_try, len(rows))):
        o, h, lo, _ = rows[i]
        if stopbuy:
            if h >= buy and (o <= lim or lo <= lim):
                fill_i, fill = i, min(max(o, buy), lim)
                break
        elif lo <= buy:
            fill_i, fill = i, min(o, buy)
            break
    if fill_i is None:
        out["status"] = "pending" if (stopbuy and len(rows) < n_try) else "not_filled"
        return out

    variant = str(r.get("variant") or "")
    plan = str(r.get("exit_plan") or "").lower()
    target_mode = variant.startswith("target")
    has_stop = stop > 0 and "no stop" not in plan
    max_hold = int(f(r.get("max_hold"), 0) or 0) or None
    at_open = abs(fill - rows[fill_i][0]) < 1e-9
    key = (r["strategy"], r["symbol"])
    out.update(status="open", filled=True, fill_rel=fill, entry_date=f"{dates[fill_i].date()}",
               fill_price=fill * c, missing_exit_files=0)

    def close(price_rel, reason, j):
        out.update(status="closed", exit_rel=price_rel, exit_price=price_rel * c, exit_reason=reason,
                   exit_date=f"{dates[j].date()}", sessions_held=j - fill_i,
                   gross_pct=(price_rel / fill - 1) * 100)
        return out

    for j in range(fill_i, len(rows)):
        o, h, lo, cl = rows[j]
        k = j - fill_i
        if k > 0:                                   # decisions taken before / at the open
            d = dates[j].date()
            if not target_mode:
                if d not in have_exits:
                    out["missing_exit_files"] += 1
                elif key in exits.get(d, ()):
                    return close(o, "exit_rule", j)
                if max_hold and k >= max_hold:
                    return close(o, "time_stop", j)
            if k >= TIME_3M:
                return close(o, "time_3m", j)
        hit_s = has_stop and lo <= stop
        hit_t = target_mode and tgt > 0 and h >= tgt and (k > 0 or at_open)
        if hit_s:                                   # both on one day -> the stop (conservative, as the backtest)
            return close(min(o, stop) if k > 0 else min(stop, fill), "stop", j)
        if hit_t:
            return close(max(o, tgt) if k > 0 else tgt, "target", j)
    out.update(mark_rel=rows[-1][3], open_pct=(rows[-1][3] / fill - 1) * 100, sessions_held=len(rows) - 1 - fill_i,
               mark_date=f"{dates[-1].date()}")
    return out


# --------------------------------------------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------------------------------------------
def summarise(g: pd.Series, costs: dict) -> dict:
    g = pd.to_numeric(g, errors="coerce").dropna()
    n = len(g)
    if n == 0:
        return {"n": 0}
    wins, losses = g[g > 0].sum(), -g[g < 0].sum()
    sd = g.std(ddof=1) if n > 1 else np.nan
    out = {"n": n, "win_pct": (g > 0).mean() * 100, "avg_pct": g.mean(), "sd": sd,
           "pf": wins / losses if losses > 0 else np.inf, "t": g.mean() / (sd / math.sqrt(n)) if n > 2 and sd > 0 else np.nan}
    for v in SIZES:
        nn = g.apply(lambda x: net_pct(x, v, costs))
        out[f"net_{v}"] = nn.mean()
        out[f"t_net_{v}"] = nn.mean() / (nn.std(ddof=1) / math.sqrt(n)) if n > 2 and nn.std(ddof=1) > 0 else np.nan
    return out


def verdict(s: dict, bt_avg: float) -> str:
    n = s.get("n", 0)
    if n < MIN_TRADES:
        return f"too early ({n}/{MIN_TRADES} closed trades)"
    z = (s["avg_pct"] - bt_avg) / (s["sd"] / math.sqrt(n)) if s["sd"] > 0 and math.isfinite(bt_avg) else np.nan
    if math.isfinite(z) and z < -2:
        return "BELOW BACKTEST - the edge is not showing up live"
    big = s.get(f"net_{SIZES[1]}", np.nan)
    if big > 0 and s.get(f"t_net_{SIZES[1]}", 0) > 2:
        return "EDGE AFTER COSTS (statistically clear)"
    if big > 0:
        return "positive after costs, not yet statistically clear"
    return "NO EDGE after costs"


def fmt(x, spec="+.2f", na="-"):
    return na if x is None or (isinstance(x, float) and not math.isfinite(x)) else format(x, spec)


# --------------------------------------------------------------------------------------------------------------
# live
# --------------------------------------------------------------------------------------------------------------
def live_section(paper: pd.DataFrame, px: dict, costs: dict, w) -> list:
    hist = []
    w("\nLIVE  (swing_trader.py, real orders)")
    st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    orders = [o for o in (st.get("orders") or {}).values() if o.get("kind") == "entry"]
    if orders:
        by = pd.Series([o.get("status", "?") for o in orders]).value_counts().to_dict()
        w(f"   entry orders sent: {len(orders)}  " + ", ".join(f"{k} {v}" for k, v in by.items()))
        miss = [o for o in orders if o.get("status") == "cancelled"]
        for o in miss:
            sig = o.get("signal") or {}
            pr = paper[(paper["symbol"] == o.get("symbol")) & (paper["signal_date"] == sig.get("signal_date"))]
            twin = "paper filled" if len(pr) and pr["filled"].any() else "paper also not filled"
            w(f"     unfilled: {o.get('symbol'):12s} {o.get('day', '')}  limit {fmt(f(o.get('price')), ',.2f')}  ({twin})")
    jr = pd.read_csv(JOURNAL) if JOURNAL.exists() else pd.DataFrame()
    if len(jr):
        jr["buy_val"] = jr["entry_price"] * jr["qty"]
        jr["sell_val"] = jr["exit_price"] * jr["qty"]
        jr["charges_rs"] = [charges(b, s, costs) for b, s in zip(jr["buy_val"], jr["sell_val"])]
        jr["net_rs"] = jr["sell_val"] - jr["buy_val"] - jr["charges_rs"]
        jr["net_ret_pct"] = jr["net_rs"] / jr["buy_val"] * 100
        w(f"   closed trades: {len(jr)}   gross Rs {jr['sell_val'].sum() - jr['buy_val'].sum():+,.0f}   "
          f"charges Rs {jr['charges_rs'].sum():,.0f}   NET Rs {jr['net_rs'].sum():+,.0f}")
        w(f"   {'Strategy':28s} {'n':>4s} {'Win%':>6s} {'Gross%':>7s} {'Net%':>7s} {'PF':>5s}   backtest win/avg/PF   verdict")
        for s, g in jr.groupby("strategy"):
            sm = summarise(g["return_pct"], costs)
            bt_avg = f(g["bt_avg_ret_pct"].iloc[-1])
            w(f"   {s:28s} {sm['n']:>4d} {sm['win_pct']:>6.1f} {sm['avg_pct']:>+7.2f} {g['net_ret_pct'].mean():>+7.2f} "
              f"{fmt(sm['pf'], '.2f'):>5s}   {fmt(f(g['bt_win_pct'].iloc[-1]), '.0f')}/{fmt(bt_avg)}/"
              f"{fmt(f(g['bt_profit_factor'].iloc[-1]), '.2f')}   {verdict(sm, bt_avg)}")
            hist.append({"view": "live", "strategy": s, **sm, "bt_avg_pct": bt_avg, "verdict": verdict(sm, bt_avg)})
        # implementation gap: live vs the same signal on paper
        m = jr.merge(paper[paper["status"] == "closed"][["signal_date", "symbol", "strategy", "fill_price", "exit_price",
                                                            "gross_pct"]],
                     on=["signal_date", "symbol", "strategy"], how="inner", suffixes=("", "_paper"))
        if len(m):
            w(f"   live vs paper on the same {len(m)} signals: entry {((m['entry_price'] / m['fill_price']) - 1).mean() * 1e4:+.0f} bps, "
              f"return {(m['return_pct'] - m['gross_pct']).mean():+.2f} pts per trade (live minus paper)")
    else:
        w("   closed trades: none yet")
    pos = (st.get("positions") or {}).values()
    if pos:
        w(f"   open positions: {len(pos)}")
        for p in pos:
            last = px.get(p["symbol"])
            ltp = float(last["close"].iloc[-1]) if last is not None and len(last) else np.nan
            w(f"     {p['symbol']:12s} {p['strategy']:24s} since {p['entry_date']}  entry {p['entry_price']:,.2f}  "
              f"last close {fmt(ltp, ',.2f')}  ({fmt((ltp / p['entry_price'] - 1) * 100)}%)  stop {f(p.get('stop')):,.2f}")
    return hist


# --------------------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chase", type=float, default=None, help="entry allowance %% for the fill-rate column "
                                                              "(default: entry_chase_pct from trader_config.json)")
    ap.add_argument("--since", help="only signals for sessions on/after YYYY-MM-DD")
    a = ap.parse_args(argv)

    def log(m):
        print(f"{datetime.now():%H:%M:%S}  {m}", flush=True)

    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig")) if CONFIG.exists() else {}
    costs = {**COSTS, **(cfg.get("charges") or {})}
    chase = a.chase if a.chase is not None else f(cfg.get("entry_chase_pct"), 0.5) or 0.5
    since = date.fromisoformat(a.since) if a.since else None
    if not T.SIG_DIR:
        log("NSEDATAENGINE signals folder not found")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)

    sig = load_signals(T.SIG_DIR, since)
    if sig.empty:
        REPORT.write_text("no signal files yet\n", encoding="utf-8")
        log("no signal files")
        return 0
    pairs = set(zip(sig["strategy"], sig["symbol"]))
    exits, have = load_exits(T.SIG_DIR, pairs, since)
    syms = set(sig["symbol"])
    st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    syms |= {p["symbol"] for p in (st.get("positions") or {}).values()}
    start = pd.Timestamp(min(d for d in sig["signal_date"].dropna())) - pd.Timedelta(days=7)
    log(f"{len(sig)} signals over {sig['session'].nunique()} sessions; loading prices for {len(syms)} stocks ...")
    px = T.load_prices(syms, start.to_pydatetime())
    last_px = max((p.index.max() for p in px.values()), default=None)

    sims = [simulate(r, px.get(r["symbol"]), exits, have, chase) for r in sig.to_dict("records")]
    paper = pd.concat([sig.reset_index(drop=True), pd.DataFrame(sims)], axis=1)
    for v in SIZES:
        paper[f"net_pct_{v}"] = paper["gross_pct"].apply(lambda x: net_pct(x, v, costs) if math.isfinite(f(x)) else np.nan) \
            if "gross_pct" in paper else np.nan
    paper.to_csv(OUT / "paper_trades.csv", index=False)

    lines = []
    w = lines.append
    today = f"{date.today()}"
    w(f"EDGE REPORT  {datetime.now():%Y-%m-%d %H:%M}   prices to {last_px.date() if last_px is not None else '-'}")
    w(f"signals: {len(sig)} over {sig['session'].nunique()} sessions ({sig['session'].min()} .. {sig['session'].max()})")
    w("A strategy needs at least 20 CLOSED trades before its numbers mean anything; 50+ to trust them.")
    w("\nROUND-TRIP CHARGES (Fyers delivery):  " + "   ".join(
        f"Rs {v:,}: {charges(v, v, costs) / v * 100:.2f}%" for v in SIZES))

    if "status" in paper:
        allowed = paper[paper["regime_allowed"]]
        w("\nPAPER  (every regime-allowed signal, traded exactly by its plan at the signal price)")
        w(f"   {'Strategy':28s} {'Sig':>4s} {'Fill%':>6s} {'+chase':>6s} {'Open':>5s} {'Closed':>6s} {'Win%':>6s} "
          f"{'Gross%':>7s} {'PF':>5s}  " + " ".join(f"{'Net@' + (str(v // 1000) + 'k'):>8s}" for v in SIZES)
          + "   backtest win/avg/PF   verdict")
        hist = []
        for s, g in allowed.groupby("strategy"):
            decided = g[g["status"].isin(["closed", "open", "not_filled"])]
            fill = decided["filled"].mean() * 100 if len(decided) else np.nan
            ch = pd.to_numeric(g.get("fill_with_chase"), errors="coerce").dropna()
            closed = g[g["status"] == "closed"]
            sm = summarise(closed["gross_pct"], costs) if len(closed) else {"n": 0}
            bt_avg = f(g["bt_avg_ret_pct"].iloc[-1])
            v = verdict(sm, bt_avg)
            w(f"   {s:28s} {len(g):>4d} {fmt(fill, '.0f'):>6s} {fmt(ch.mean() * 100 if len(ch) else np.nan, '.0f'):>6s} "
              f"{(g['status'] == 'open').sum():>5d} {sm['n']:>6d} {fmt(sm.get('win_pct'), '.1f'):>6s} "
              f"{fmt(sm.get('avg_pct')):>7s} {fmt(sm.get('pf'), '.2f'):>5s}  "
              + " ".join(f"{fmt(sm.get(f'net_{x}')):>8s}" for x in SIZES)
              + f"   {fmt(f(g['bt_win_pct'].iloc[-1]), '.0f')}/{fmt(bt_avg)}/{fmt(f(g['bt_profit_factor'].iloc[-1]), '.2f')}"
              f"   {v}")
            hist.append({"view": "paper", "strategy": s, **sm, "bt_avg_pct": bt_avg, "verdict": v})
        blocked = paper[~paper["regime_allowed"] & (paper["status"] == "closed")]
        if len(blocked):
            sb = summarise(blocked["gross_pct"], costs)
            w(f"   regime-BLOCKED signals (what the filter kept you out of): {sb['n']} closed, win {sb['win_pct']:.0f}%, "
              f"gross {sb['avg_pct']:+.2f}%  -> {'filter helped' if sb['avg_pct'] < 0 else 'filter cost money so far'}")
        reasons = paper[paper["status"] == "closed"]["exit_reason"].value_counts()
        if len(reasons):
            w("   exits: " + ", ".join(f"{k} {v}" for k, v in reasons.items()))
        miss = int(pd.to_numeric(paper.get("missing_exit_files"), errors="coerce").fillna(0).max())
        if miss:
            w(f"   WARNING: up to {miss} session(s) had no exits_for_<day>.csv (evening task did not run) - "
              f"exit-rule exits on those days are missed")
    else:
        hist = []

    hist += live_section(paper, px, costs, w)

    w("\nHOW TO READ IT")
    w("   Gross% is comparable with the backtest avg (both before charges). Net@ is after Fyers charges at that position size.")
    w("   'BELOW BACKTEST': live/paper average is >2 standard errors under the backtest - the backtest was too optimistic.")
    w("   'EDGE AFTER COSTS': net@25k average is >0 and >2 standard errors from zero. Only then size up.")
    w(f"\nfiles: {OUT / 'paper_trades.csv'}  |  {OUT / 'edge_history.csv'}")
    text = "\n".join(lines) + "\n"
    REPORT.write_text(text, encoding="utf-8")

    if hist:
        h = pd.DataFrame(hist)
        h.insert(0, "run_date", today)
        hp = OUT / "edge_history.csv"
        if hp.exists():
            old = pd.read_csv(hp)
            h = pd.concat([old[old["run_date"] != today], h], ignore_index=True)
        h.to_csv(hp, index=False)
    print(text)
    log(f"report {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
