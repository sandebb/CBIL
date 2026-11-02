"""
nse_signals.py - the Nsealgo v3.11 swing signals for the NEXT trading session (paper trading).

    python nse_signals.py                  update the data, scan, print + save signals\\signals_for_<date>.csv/.html
    python nse_signals.py --open           ...and open the HTML page
    python nse_signals.py --no-update      scan the data as it is (skip nse_updater.py update)
    python nse_signals.py --capital 1000000 --risk-pct 1     paper position sizes (default 10 lakh, 1% risk)

What it does
  1. Runs `nse_updater.py update` (NSEDATAENGINE's daily update) so the data ends at the last trading day.
  2. Runs every strategy in bt_strategies.py - the SAME code nse_backtest.py uses, which imports v3.11's
     indicators, regime engine and parameters from NSEALGOENGINE - on the latest data, and keeps the signals
     of the last trading day. Those are the orders for the next session, placed exactly as the backtest
     placed them: a day LIMIT buy at the signal close (PEAD: buy-STOP-limit, re-armed for up to 3 sessions).
  3. Adds what the backtest cannot know on its last day: results meetings in the next 3 sessions (NSE board
     meetings + NSE's holiday calendar), so v3.11's results blackouts also apply to Monday's orders.
  4. Marks the strategies of your backtested book (default: the best portfolio in
     portfolio_results\\compare_*.csv) and shows each strategy's backtest record next to its signals.
  5. Paper sizing: risk per trade = capital x risk-pct, capped at 20% of capital per position and 0.25% of the
     stock's 20-day traded value (v3.11's caps). Kelly scaling, the 12-position limit and the ES cap are NOT
     applied - see nse_portfolio.py for those.

Only reads data and writes report files. Places no orders.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import datetime as dt
import html
import json
import math
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nse_common as NC  # noqa: E402  (installs missing packages)
import nse_contract as C  # noqa: E402
from nse_common import np, pl  # noqa: E402

for _mod, _pip in (("pandas", "pandas"), ("scipy", "scipy"), ("hmmlearn", "hmmlearn"), ("statsmodels", "statsmodels"),
                   ("yaml", "PyYAML"), ("psutil", "psutil"), ("threadpoolctl", "threadpoolctl"),
                   ("requests", "requests")):
    NC.REQUIRED_PACKAGES[_mod] = _pip
NC.ensure_packages()
import pandas as pd  # noqa: E402
import bt_strategies as BS  # noqa: E402

# The best book in portfolio_results\compare_20261003_0302.csv: PEAD sold at its target (no stop), pullback on
# its own exit rule. resid_momentum_hold63 was positive but lowered the portfolio's return.
DEFAULT_BOOK = "pead:target_nostop,pead_200dma:target_nostop,pullback:rules_nostop"
DEFAULT_RUNS = "v311_sf_exact2,v311_sf_variants"
NATIVE_VARIANT = "rules_stop"            # v3.11's own behaviour: its exit rule + its protective stop
VARIANT_TEXT = {
    "target_nostop": "sell limit at target; no stop",
    "target_stop": "sell limit at target + stop",
    "rules_nostop": "exit on v3.11's exit rule (next open); no stop",
    "rules_stop": "v3.11 exit rule + protective stop",
}
MAX_POS_PCT = 20.0                       # v3.11: max 20% of equity per position
ADV_PCT = 0.25                           # v3.11: max 0.25% of 20-day traded value per order


def log_to(path: Path):
    def log(m):
        line = f"{dt.datetime.now():%H:%M:%S}  {m}"
        print(line, flush=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    return log


# -------------------------------------------------------------------------------------------------------------
# calendar / data helpers
# -------------------------------------------------------------------------------------------------------------
def nse_holidays(log) -> tuple[set, str]:
    try:
        import requests
        s = requests.Session()
        s.headers.update(NC.HEADERS)
        s.get("https://www.nseindia.com/", timeout=15)
        r = s.get("https://www.nseindia.com/api/holiday-master?type=trading", timeout=20,
                  headers={"Accept": "application/json"})
        j = r.json()
        hol = set()
        for it in j.get("CM", []) or j.get("FO", []):
            try:
                hol.add(dt.datetime.strptime(str(it.get("tradingDate")).strip(), "%d-%b-%Y").date())
            except ValueError:
                pass
        if hol:
            return hol, f"NSE holiday list ({len(hol)} holidays)"
        raise ValueError("empty holiday list")
    except Exception as ex:
        y = dt.date.today().year
        fixed = {dt.date(yy, m, d) for yy in (y, y + 1) for m, d in ((1, 26), (5, 1), (8, 15), (10, 2), (12, 25))}
        log(f"NSE holiday list unavailable ({ex.__class__.__name__}); using weekdays minus the fixed-date holidays "
            f"(26 Jan, 1 May, 15 Aug, 2 Oct, 25 Dec) - check the next session yourself")
        return fixed, "weekdays minus fixed-date holidays (NSE holiday list unavailable)"


def sessions_after(day: dt.date, n: int, hol: set) -> list[dt.date]:
    out, d = [], day
    while len(out) < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5 and d not in hol:
            out.append(d)
    return out


def run_updater(log) -> None:
    upd = Path(__file__).with_name("nse_updater.py")
    log(f"updating the data: {Path(sys.executable).name} nse_updater.py update")
    r = subprocess.run([sys.executable, str(upd), "update"], cwd=str(upd.parent))
    if r.returncode != 0:
        log(f"WARNING: nse_updater.py exited with {r.returncode}; scanning the data as it is")


def load_stats(runs: list[str], log) -> dict:
    """(strategy, variant) -> summary row, from the backtest runs' summary.csv (signal set fresh_regime)."""
    out = {}
    for run in runs:
        f = C.location("backtest_results") / run / "summary.csv"
        if not f.exists():
            log(f"backtest run {run}: no summary.csv - its stats are not shown")
            continue
        s = pd.read_csv(f)
        if "signal_set" in s.columns and (s["signal_set"] == "fresh_regime").any():
            s = s[s["signal_set"] == "fresh_regime"]
        for r in s.to_dict("records"):
            out.setdefault((r.get("strategy"), r.get("variant")), r)
    return out


def _num(v, default=float("nan")):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


# -------------------------------------------------------------------------------------------------------------
# scan
# -------------------------------------------------------------------------------------------------------------
def scan(a) -> int:
    t0 = time.time()
    try:
        out_dir = C.location("signals")
    except Exception:
        out_dir = C.data_root() / "NSEDATAENGINE" / "signals"
    out_dir.mkdir(parents=True, exist_ok=True)
    log = log_to(out_dir / "scan_log.txt")
    log("=" * 80)
    if not a.no_update:
        run_updater(log)

    E = BS.load_engine(a.engine_dir)
    folder = Path(a.data_dir) if a.data_dir else None
    raw_p, mk_p = C.file_path("raw_ohlcv", "master", folder=folder), C.file_path("market", "master", folder=folder)
    raw = pl.scan_parquet(raw_p)
    last = raw.select(pl.col("datetime").max()).collect().item()
    L = pd.Timestamp(last).normalize()
    hol, cal_src = nse_holidays(log)
    fut = sessions_after(L.date(), 3, hol)
    S1 = fut[0]
    now = dt.datetime.now()
    log(f"v3.11 source: {E.src}")
    log(f"data: {raw_p} - last trading day {L:%a %d %b %Y}; orders are for {S1:%a %d %b %Y} ({cal_src})")
    stale = S1 < now.date() or (S1 == now.date() and now.hour >= 19)
    if stale:
        log(f"WARNING: the data ends {L:%d %b} but {S1:%d %b} has already traded - these are NOT the next "
            f"session's signals. Run the update (without --no-update) or check nse_updater.py.")

    # ---- prices (same window logic and liquidity filter as nse_backtest.py) ----------------------------------
    load_from = L.to_pydatetime() - dt.timedelta(days=int(BS.LOOKBACK_SESSIONS * 1.5) + 30)
    df = (raw.filter(pl.col("datetime") >= load_from)
             .select("company_id", "datetime", "open", "high", "low", "close", "volume").collect().to_pandas())
    mk = pl.read_parquet(mk_p).select("datetime", "nifty_close", "vix").to_pandas().set_index("datetime")
    mk = mk[mk.index <= L]
    piv = {f: df.pivot(index="datetime", columns="company_id", values=f).sort_index()
           for f in ("open", "high", "low", "close", "volume")}
    Cl, V = piv["close"], piv["volume"]
    keep = (Cl * V).rolling(20, min_periods=20).mean().where(Cl.notna()).rank(axis=1, ascending=False).le(500).any()
    for f in piv:
        piv[f] = piv[f].loc[:, keep]
    log(f"loaded {len(df):,} rows from {load_from:%Y-%m-%d}; {int(keep.sum())} stocks ever in the top 500 by turnover")

    # ---- results calendar: past day-0s for PEAD + meetings in the next 3 sessions for the blackouts -----------
    res = BS.fetch_results(C.location("events"), load_from.date(), max(now.date(), fut[-1]) + dt.timedelta(days=7),
                           NC.HEADERS, log, offline=a.offline)
    P = BS.Panels(piv["open"], piv["high"], piv["low"], piv["close"], piv["volume"], mk["nifty_close"], mk["vix"],
                  BS.day0_map(res, piv["close"].index))
    upcoming = {}
    if res is not None:
        for sym, d in res.itertuples(index=False):
            if L.date() <= d <= fut[-1]:
                upcoming.setdefault(sym, d)
    else:
        log("WARNING: no results calendar - PEAD cannot signal and results blackouts are not applied")
    if upcoming:
        log(f"{len(upcoming)} stocks have results meetings between {L:%d %b} and {fut[-1]:%d %b} (blackout applied)")
    base_blackout = P.blackout
    fut_cols = [s for s in upcoming if s in P.C.columns]

    def blackout(had=True, nxt=True):
        out = base_blackout(had, nxt)
        if nxt and fut_cols:                         # the backtest's look-ahead ends at the data; extend it
            out.loc[L, fut_cols] = True
        return out
    P.blackout = blackout

    # ---- F&O universe ----------------------------------------------------------------------------------------
    uni_note = "fno universe: top 200 by 20-day turnover (proxy)"
    if a.fno_universe == "exact":
        fpath = C.file_path("fno_universe", "ref")
        if fpath.exists():
            fmax = pd.Timestamp(pl.scan_parquet(fpath).select(pl.col("date").max()).collect().item())
            behind = int((P.dates > fmax).sum())
            if behind <= 5:
                import nse_backtest as NB
                P.fno_exact = NB._fno_mask(SimpleNamespace(fno_file=None), P, L, log)
                uni_note = f"fno universe: NSE's F&O list as of {fmax:%d %b %Y}"
            else:
                log(f"WARNING: {fpath.name} ends {fmax:%d %b %Y}, {behind} sessions behind - using the turnover proxy. "
                    f"Refresh it: python nse_fno_universe.py build")
        else:
            log(f"{fpath} not found - using the turnover proxy for the F&O universe")
    log(uni_note)

    # ---- regime (v3.11 HMM + 200-day, from data up to the previous session, as in the backtest) --------------
    workers = a.workers or os.cpu_count() or 1
    reg = BS.regime_states(E, mk["nifty_close"], mk["vix"], pd.DatetimeIndex([L]), workers, log)
    state = str(reg.get(L, "unknown"))
    log(f"regime on {L:%d %b}: {state}")

    # ---- strategies ------------------------------------------------------------------------------------------
    book = {}
    for item in [x.strip() for x in (a.book or "").split(",") if x.strip()]:
        s, _, v = item.partition(":")
        book[s] = v or NATIVE_VARIANT
    stats = load_stats([r.strip() for r in a.runs.split(",") if r.strip()], log)
    names = list(BS.STRATEGIES) if a.strategies == "all" else [x.strip() for x in a.strategies.split(",")]
    pos = {d: i for i, d in enumerate(P.dates)}
    iL = pos[L]
    rows, exits = [], []
    for name in names:
        if name not in BS.STRATEGIES:
            log(f"  {name}: unknown strategy - skipped")
            continue
        fn, style = BS.STRATEGIES[name]
        t1 = time.time()
        try:
            sig, flag = fn(E, P)
        except Exception as ex:
            log(f"  {name:28s} FAILED: {ex!r}")
            continue
        if isinstance(flag, pd.DataFrame) and L in flag.index:     # exit rule fired at the last close
            fired = flag.loc[L]
            fired = fired[fired.fillna(False).astype(bool)]
            exits += [{"strategy": name, "symbol": s, "exit_date": f"{L:%Y-%m-%d}"} for s in fired.index]
        if sig is None or len(sig) == 0:
            log(f"  {name:28s} 0 signals  ({time.time() - t1:.0f}s)")
            continue
        sig = sig.copy()
        sig["signal_date"] = pd.to_datetime(sig["signal_date"])
        today = sig[sig["signal_date"] == L]
        earlier = sig[sig["signal_date"] < L].groupby("company_id")["signal_date"].max()
        allowed = BS.regime_allowed(E, state, style) if state != "unknown" else False
        variant = book.get(name, NATIVE_VARIANT)
        st = stats.get((name, variant), {})
        for r in today.to_dict("records"):
            sym = r["company_id"]
            buy, stop, tgt = float(r["buy_price"]), float(r["stop"]), float(r["target"])
            lim = float(r.get("limit_price") or buy)
            hold = int(r["max_hold"]) if _num(r.get("max_hold")) < 10 ** 5 else 63
            note = []
            if sym in earlier.index and iL - pos.get(earlier[sym], -10 ** 6) <= hold:
                note.append(f"also signalled {earlier[sym]:%d %b} - may already be held")
            if sym in upcoming:
                note.append(f"results meeting {upcoming[sym]:%d %b}")
            t20 = _num(P.turnover20.at[L, sym]) if sym in P.turnover20.columns else float("nan")
            # paper sizing
            per = buy - stop
            qty = math.floor(a.capital * a.risk_pct / 100 / per) if per > 0 else 0
            cap_px = lim if r.get("order_type") == "stop" else buy
            qty = min(qty, math.floor(a.capital * MAX_POS_PCT / 100 / cap_px))
            if math.isfinite(t20) and t20 > 0:
                qty = min(qty, math.floor(t20 * ADV_PCT / 100 / cap_px))
            rows.append({
                "in_book": name in book, "strategy": name, "style": style, "symbol": sym,
                "order": "BUY-STOP-LIMIT" if r.get("order_type") == "stop" else "LIMIT",
                "trigger_or_limit": round(buy, 2), "limit_price": round(lim, 2),
                "valid_sessions": int(r.get("arm_days") or 1),
                "stop": round(stop, 2), "target": round(tgt, 2),
                "risk_pct": round(per / buy * 100, 2), "reward_pct": round((tgt - buy) / buy * 100, 2),
                "max_hold": hold, "exit_plan": VARIANT_TEXT.get(variant, variant), "variant": variant,
                "close": round(_num(P.C.at[L, sym]), 2),
                "turnover_rank": int(_num(P.rank.at[L, sym], 0)) or None,
                "score": round(_num(r.get("score")), 4) if math.isfinite(_num(r.get("score"))) else None,
                "regime": state, "regime_allowed": allowed,
                "paper_qty": max(qty, 0), "paper_value": round(max(qty, 0) * cap_px, 0),
                "bt_trades": st.get("closed_trades"), "bt_win_pct": st.get("win_rate_pct"),
                "bt_avg_ret_pct": st.get("avg_return_pct"), "bt_profit_factor": st.get("profit_factor"),
                "note": "; ".join(note),
            })
        log(f"  {name:28s} {len(today):3d} signal(s) for {S1:%d %b}{'' if allowed else '  [regime blocks new entries]'}"
            f"  ({time.time() - t1:.0f}s)")

    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values(["in_book", "regime_allowed", "strategy", "score"],
                              ascending=[False, False, True, False], na_position="last")
    stem = f"signals_for_{S1:%Y%m%d}"
    pd.DataFrame(exits, columns=["strategy", "symbol", "exit_date"]).to_csv(out_dir / f"exits_for_{S1:%Y%m%d}.csv",
                                                                          index=False)
    (out_dir / f"session_{S1:%Y%m%d}.json").write_text(json.dumps(
        {"signal_date": f"{L:%Y-%m-%d}", "session": f"{S1:%Y-%m-%d}", "regime": state, "stale": bool(stale),
         "next_sessions": [f"{d:%Y-%m-%d}" for d in fut], "holidays": sorted(f"{d:%Y-%m-%d}" for d in hol)},
        indent=2), encoding="utf-8")
    csv_p, html_p = out_dir / f"{stem}.csv", out_dir / f"{stem}.html"
    out.to_csv(csv_p, index=False)
    meta = dict(L=L, S1=S1, state=state, cal=cal_src, uni=uni_note, upcoming=len(upcoming), stale=stale,
                capital=a.capital, risk=a.risk_pct, book=book, runtime=time.time() - t0)
    write_html(out, meta, html_p)
    print_summary(out, meta)
    log(f"saved {csv_p}")
    log(f"saved {html_p}")
    log(f"done in {time.time() - t0:.0f}s")
    if a.open:
        webbrowser.open(html_p.as_uri())
    return 0


# -------------------------------------------------------------------------------------------------------------
# output
# -------------------------------------------------------------------------------------------------------------
def _sections(out: pd.DataFrame):
    if not len(out):
        return []
    return [("Your backtested book - regime allows", out[out.in_book & out.regime_allowed]),
            ("Other v3.11 strategies - regime allows", out[~out.in_book & out.regime_allowed]),
            ("Regime blocks new entries (v3.11 would NOT buy)", out[~out.regime_allowed])]


def print_summary(out: pd.DataFrame, m: dict) -> None:
    print()
    print(f"Signals from {m['L']:%a %d %b %Y} close -> orders for {m['S1']:%a %d %b %Y}   regime: {m['state']}")
    if m["stale"]:
        print("!! DATA IS STALE - see the warning above")
    if not len(out):
        print("No v3.11 strategy signalled. Nothing to place.\n")
        return
    cols = ["symbol", "strategy", "order", "trigger_or_limit", "stop", "target", "risk_pct", "paper_qty", "note"]
    head = ["Symbol", "Strategy", "Order", "Buy", "Stop", "Target", "Risk%", "Qty", "Note"]
    for title, part in _sections(out):
        if not len(part):
            continue
        print(f"\n{title}  ({len(part)})")
        data = [[str(r[c]) if r[c] is not None and r[c] == r[c] else "" for c in cols] for r in part.to_dict("records")]
        w = [min(max(len(head[i]), *(len(d[i]) for d in data)), 46) for i in range(len(cols))]
        print("  ".join(h.ljust(w[i]) if i in (0, 1, 2, 8) else h.rjust(w[i]) for i, h in enumerate(head)))
        for d in data:
            print("  ".join((d[i][:w[i]]).ljust(w[i]) if i in (0, 1, 2, 8) else d[i].rjust(w[i])
                            for i in range(len(cols))))
    print(f"\nPaper sizing: capital Rs {m['capital']:,.0f}, risk {m['risk']}% per trade, capped at {MAX_POS_PCT:.0f}% "
          f"of capital and {ADV_PCT}% of 20-day traded value. Full details: HTML / CSV.\n")


HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>NSE Signals</title><style>
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1b;--mut:#6b6b66;--line:#e4e4df;--up:#0f7b4f;--dn:#c0392b;--warn:#9a6700;--chip:#efefea}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--fg:#ecece8;--mut:#9a9a93;--line:#2e2e2b;
--up:#3fbf86;--dn:#ef6b5b;--warn:#e0b04a;--chip:#2a2a27}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 "Segoe UI",system-ui,sans-serif}
main{max-width:1280px;margin:0 auto;padding:24px 16px}h1{font-size:20px;margin:0 0 4px}
h2{font-size:15px;margin:22px 0 8px}.sub{color:var(--mut);font-size:12px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:16px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.k{color:var(--mut);font-size:12px}.v{font-size:18px;font-weight:600}
.wrap{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{font-size:12px;color:var(--mut);font-weight:600}tr:last-child td{border-bottom:0}
td.l,th.l{text-align:left}.chip{background:var(--chip);border-radius:4px;padding:1px 6px;font-size:11px}
.note{color:var(--warn);font-size:12px;white-space:normal;min-width:180px}.dn{color:var(--dn)}.up{color:var(--up)}
.warn{background:var(--card);border:1px solid var(--warn);color:var(--warn);border-radius:10px;padding:10px 14px;margin:12px 0}
.foot{color:var(--mut);font-size:12px;margin-top:16px;line-height:1.6}
</style></head><body><main>__BODY__</main></body></html>"""


def write_html(out: pd.DataFrame, m: dict, path: Path) -> None:
    e = html.escape
    parts = [f"<h1>v3.11 swing signals for {m['S1']:%A %d %b %Y}</h1>",
             f'<div class="sub">From the {m["L"]:%a %d %b %Y} close &middot; paper trading only &middot; '
             f'generated {dt.datetime.now():%d %b %Y %H:%M}</div>']
    if m["stale"]:
        parts.append('<div class="warn">The data does not include the latest session - these are not the next '
                     'session\'s signals. Run the update and scan again.</div>')
    n_book = int((out.in_book & out.regime_allowed).sum()) if len(out) else 0
    tiles = [("Regime", e(m["state"])), ("Signals (all strategies)", str(len(out))),
             ("Book signals the regime allows", str(n_book)),
             ("Results meetings next 3 sessions", str(m["upcoming"]))]
    parts.append('<div class="tiles">' + "".join(f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div>'
                                                  f'</div>' for k, v in tiles) + "</div>")
    if not len(out):
        parts.append("<p>No v3.11 strategy signalled. Nothing to place.</p>")
    heads = ["Symbol", "Strategy", "Order", "Buy / trigger", "Limit", "Valid", "Stop", "Target", "Risk %",
             "Reward %", "Exit plan", "Paper qty", "Value", "Backtest win % / avg % / PF (trades)", "Note"]
    for title, part in _sections(out):
        if not len(part):
            continue
        parts.append(f"<h2>{e(title)} ({len(part)})</h2><div class='wrap'><table><thead><tr>"
                     + "".join(f"<th class='{'l' if h in ('Symbol', 'Strategy', 'Order', 'Exit plan', 'Note') else ''}'>"
                               f"{e(h)}</th>" for h in heads) + "</tr></thead><tbody>")
        for r in part.to_dict("records"):
            bt = "&ndash;"
            if r.get("bt_trades") == r.get("bt_trades") and r.get("bt_trades") is not None:
                bt = (f"{_num(r['bt_win_pct']):.0f}% / {_num(r['bt_avg_ret_pct']):+.1f}% / "
                      f"{_num(r['bt_profit_factor']):.2f} ({int(_num(r['bt_trades'], 0))})")
            parts.append(
                "<tr>"
                f"<td class='l'><b>{e(str(r['symbol']))}</b></td>"
                f"<td class='l'><span class='chip'>{e(r['strategy'])}</span></td>"
                f"<td class='l'>{e(r['order'])}</td>"
                f"<td>{r['trigger_or_limit']:,.2f}</td>"
                f"<td>{r['limit_price']:,.2f}</td>"
                f"<td>{r['valid_sessions']} session{'s' if r['valid_sessions'] > 1 else ''}</td>"
                f"<td class='dn'>{r['stop']:,.2f}</td><td class='up'>{r['target']:,.2f}</td>"
                f"<td>{r['risk_pct']:.2f}</td><td>{r['reward_pct']:.2f}</td>"
                f"<td class='l'>{e(r['exit_plan'])}</td>"
                f"<td>{r['paper_qty']:,}</td><td>{r['paper_value']:,.0f}</td><td>{bt}</td>"
                f"<td class='l note'>{e(r['note'] or '')}</td></tr>")
        parts.append("</tbody></table></div>")
    book = ", ".join(f"{k} ({VARIANT_TEXT.get(v, v)})" for k, v in m["book"].items()) or "none"
    parts.append(
        "<div class='foot'>"
        f"Orders as the backtest placed them: LIMIT = day limit buy at the signal close on {m['S1']:%d %b} (cancelled if "
        "not filled); BUY-STOP-LIMIT (PEAD) = buy when the price reaches the trigger, not above the limit, re-placed "
        "each morning for the sessions shown.<br>"
        f"Book: {e(book)}. Backtest columns: signal set 'fresh_regime' of the runs given with --runs, for the exit "
        "plan shown (other strategies: v3.11's exit rule + stop).<br>"
        f"Paper qty: {m['risk']}% of Rs {m['capital']:,.0f} at risk to the stop, capped at {MAX_POS_PCT:.0f}% of capital "
        f"and {ADV_PCT}% of 20-day traded value. Not applied: Kelly scaling, the 12-position limit, the ES cap, "
        "positions you already hold.<br>"
        f"{e(m['uni'])}. Calendar: {e(m['cal'])}. Scan took {m['runtime']:.0f}s.</div>")
    path.write_text(HTML.replace("__BODY__", "\n".join(parts)), encoding="utf-8")


# -------------------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-update", action="store_true", help="skip nse_updater.py update")
    ap.add_argument("--open", action="store_true", help="open the HTML page")
    ap.add_argument("--strategies", default="all", help="comma-separated names, or 'all' (default)")
    ap.add_argument("--book", default=DEFAULT_BOOK, help=f"strategy:variant list marked as your book (default {DEFAULT_BOOK})")
    ap.add_argument("--runs", default=DEFAULT_RUNS, help=f"backtest runs whose stats are shown (default {DEFAULT_RUNS})")
    ap.add_argument("--capital", type=float, default=1_000_000, help="paper capital in Rs (default 10,00,000)")
    ap.add_argument("--risk-pct", type=float, default=1.0, help="risk per trade in %% of capital (default 1)")
    ap.add_argument("--fno-universe", choices=["exact", "proxy"], default="exact",
                    help="exact = NSE's F&O list (as the best backtests), proxy = top 200 by turnover")
    ap.add_argument("--data-dir", help="folder with raw_ohlcv.parquet + market.parquet (default: the updater data)")
    ap.add_argument("--engine-dir", help="NSEALGOENGINE folder (default: %%NSEALGO_SRC%% or ..\\NSEALGOENGINE)")
    ap.add_argument("--workers", type=int, default=0, help="processes for the regime model (default: all cores)")
    ap.add_argument("--offline", action="store_true", help="use only cached NSE results files")
    return scan(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
