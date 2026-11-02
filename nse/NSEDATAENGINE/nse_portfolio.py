"""
nse_portfolio.py - turn backtest trades into ONE portfolio with Nsealgo v3.11's money management.

Why
  nse_backtest.py tests every signal on its own: unlimited money, unlimited positions. A real account has
  10 lakh (say), at most 12 swing positions, and v3.11 sizes each trade by risk. PEAD signals cluster in
  results season, so many of them can never be taken. This script replays the trades of chosen strategies
  day by day through v3.11's own rules and reports what the ACCOUNT would have done: equity curve,
  drawdowns, exposure, and which signals had to be skipped and why.

What is taken from a backtest run (backtest_results\\<run>\\trades.parquet)
  for each chosen strategy:variant - the fill (day + price), stop, and exit (day + price). Entries and exits
  are exactly the ones the single-signal backtest simulated; only sizing and capacity are added here.

v3.11 rules applied (src/nsealgo/core/risk.py, core/context.py, config.py; values from config/system.yaml)
  * risk per trade = equity x min(swing_cap 1%, 1/4 x Kelly f*). f* = p - (1-p)/R from the strategy's prior
    (pead 45% / 2.0, pullback 60% / 0.9, ...) updated with its own closed trades (prior weight 20). A strategy
    whose f* turns negative after 30 trades is switched off - as v3.11 does.
  * size multipliers: turbulent regime x0.5 for liquidity-taking swing strategies; probation strategies
    (pullback) x0.5; crisis: no new swing entries (already removed: only regime-allowed signals are used)
  * quantity = risk / (entry - stop), then capped by: Rs 25 lakh per order; 0.25% of 20-day traded value;
    20% of equity per position; worst-1% overnight gap x qty <= 2 x planned risk (gap_multiple); and the
    book's 97.5% one-day expected shortfall <= 3% of equity (es_cap, 500 days of history)
  * at most 12 swing positions; pullback at most 3 new per day; never two positions in the same stock
  * delivery (CNC): no leverage - a buy needs the cash
  * costs: v3.11's CostModel (STT 0.1% each side, stamp, exchange, SEBI, GST, DP charge) + its impact model
    (half-spread 1 bp + 0.7 x daily volatility x sqrt(order value / 20-day traded value))
Not modelled: sector cap (no sector data), the crowding breaker (momentum only), crisis-day liquidation of
liquidity-providing positions (a handful of days), intraday timing (exits of a day are booked before that
day's entries; same-day candidates are taken in --book order, then by score).

Outputs (portfolio_results\\<name>\\): summary.txt, equity.csv (daily), trades.csv (taken), skipped.csv,
monthly_returns.csv, run_config.json
"""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import argparse
import datetime as dt
import json
import math
import sys
import time
from pathlib import Path

import nse_common as NC  # noqa: E402  (installs missing packages)
import nse_contract as C  # noqa: E402  folders, file names and columns (contract\\schema.yaml)
from nse_common import HelpfulParser, parse_args_or_help, project_home, np, pl  # noqa: E402
for _mod, _pip in (("pandas", "pandas"), ("yaml", "PyYAML")):
    NC.REQUIRED_PACKAGES[_mod] = _pip
import pandas as pd  # noqa: E402
import bt_strategies as BS  # noqa: E402

EXAMPLES = r"""
examples:
  the two strategies with an edge, 10 lakh, v3.11 limits (run the backtest with the variants first):
    python nse_backtest.py run --data-dir data_fresh_2019 --fno-universe exact --name v311_sf_var ^
           --strategies pead,pead_200dma,pullback,resid_momentum,resid_momentum_hold63,resid_momentum_200dma_hold63
    python nse_portfolio.py run --run v311_sf_var --data-dir data_fresh_2019

  another mix / capital / limits:
    python nse_portfolio.py run --run v311_sf_var --data-dir data_fresh_2019 ^
           --book pead_200dma:target_nostop,pullback:rules_nostop,resid_momentum_hold63:rules_nostop
    python nse_portfolio.py run --run v311_sf_var --data-dir data_fresh_2019 --capital 2500000 --max-positions 15

  compare several books in one go (one summary line each):
    python nse_portfolio.py compare --run v311_sf_var --data-dir data_fresh_2019 ^
           --books "pead:target_nostop|pead_200dma:target_nostop|pead_200dma:target_nostop,pullback:rules_nostop"
"""

BASE = {"pead_200dma": "pead", "resid_momentum_hold63": "resid_momentum",
        "resid_momentum_200dma_hold63": "resid_momentum"}
MAX_NEW_PER_DAY = {"pullback": 3}


# =====================================================================================================
# v3.11 pieces
# =====================================================================================================
class StratStats:
    """Running R-multiple statistics of one strategy (core/risk.py StratStats)."""

    def __init__(self):
        self.trades = self.wins = 0
        self.sum_win_r = self.sum_loss_r = 0.0

    def add(self, r):
        self.trades += 1
        if r > 0:
            self.wins += 1
            self.sum_win_r += r
        else:
            self.sum_loss_r += -r


def kelly_fraction(stats, prior, cfg, horizon_cap):
    """core/risk.py RiskManager.kelly: posterior-shrunk Kelly, quarter-Kelly, capped; 0 = switched off."""
    p0, r0 = prior
    n0 = float(cfg.get("prior_weight", 20))
    s = stats
    losses = s.trades - s.wins
    p = (s.wins + p0 * n0) / (s.trades + n0)
    avg_win = (s.sum_win_r + r0 * n0 * p0) / (s.wins + n0 * p0)
    avg_loss = (s.sum_loss_r + 1.0 * n0 * (1 - p0)) / (losses + n0 * (1 - p0))
    f = p - (1 - p) / (avg_win / max(avg_loss, 1e-9))
    if f <= 0 and s.trades >= int(cfg.get("min_trades_to_disable", 30)):
        return 0.0, f
    return (max(0.0, min(horizon_cap, cfg.get("kelly_fraction", 0.25) * f)) if f > 0 else horizon_cap * 0.25), f


def posture_mult(state, style):
    """core/regime.py posture for a SWING strategy: (allowed, size multiplier)."""
    if state == "calm":
        return True, 1.0
    if state == "turbulent":
        return (False, 0.0) if style == "liquidity_providing" else (True, 0.5)
    return False, 0.0


# =====================================================================================================
# SIMULATION
# =====================================================================================================
def load_inputs(args, book, log):
    run_dir = Path(args.run) if Path(args.run).is_dir() else C.location("backtest_results") / args.run
    tp = run_dir / "trades.parquet"
    if not tp.exists():
        sys.exit(f"{tp} not found")
    t = pl.read_parquet(tp)
    have = set(t.select(pl.concat_str("strategy", pl.lit(":"), "variant")).to_series().unique().to_list())
    missing = [f"{s}:{v}" for s, v in book if f"{s}:{v}" not in have]
    if missing:
        sys.exit(f"not in {run_dir.name}: {missing}. Strategies in the run: "
                 f"{sorted(set(t['strategy'].unique().to_list()))} - re-run nse_backtest.py with --strategies ...")
    sel = pl.lit(False)
    for s, v in book:
        sel = sel | ((pl.col("strategy") == s) & (pl.col("variant") == v))
    t = t.filter(sel, pl.col("filled"), pl.col("regime_allowed"))
    if args.start:
        t = t.filter(pl.col("order_date") >= dt.datetime.fromisoformat(args.start))
    if args.end:
        t = t.filter(pl.col("order_date") <= dt.datetime.fromisoformat(args.end))
    cand = t.select("strategy", "variant", "company_id", "signal_date", "order_date", "fill_price", "buy_price", "stop",
                    "exit_date", "exit_price", "exit_reason", "regime", "score").to_pandas()
    data_dir = Path(args.data_dir) if args.data_dir else C.location("master")
    first = cand["order_date"].min()
    load_from = first - pd.Timedelta(days=1150)                       # 750 sessions for the gap quantile
    cids = sorted(cand["company_id"].unique())
    raw = (pl.scan_parquet(C.file_path("raw_ohlcv", "master", folder=data_dir))
             .filter(pl.col("datetime") >= load_from, pl.col("company_id").is_in(cids))
             .select("company_id", "datetime", "open", "close", "volume").collect().to_pandas())
    piv = {f: raw.pivot(index="datetime", columns="company_id", values=f).sort_index() for f in ("open", "close", "volume")}
    mk = pl.read_parquet(C.file_path("market", "master", folder=data_dir)).select("datetime", "nifty_close").to_pandas().set_index("datetime")
    log(f"{len(cand):,} candidate trades from {run_dir.name} ({', '.join(f'{s}:{v}' for s, v in book)}); "
        f"prices for {len(cids)} stocks from {data_dir}")
    return cand, piv, mk, data_dir, run_dir


def ew500_index(data_dir, start, end):
    """Equal-weight index of the 500 most traded stocks (20-day turnover, previous day) - the benchmark a
    stock picker in this universe has to beat."""
    r = (pl.scan_parquet(C.file_path("raw_ohlcv", "master", folder=data_dir))
           .filter(pl.col("datetime") >= start - dt.timedelta(days=60), pl.col("datetime") <= end)
           .select("company_id", "datetime", "close", "volume").sort("company_id", "datetime")
           .with_columns(ret=pl.col("close") / pl.col("close").shift(1).over("company_id") - 1,
                         to=(pl.col("close") * pl.col("volume")).rolling_mean(20).over("company_id"))
           .with_columns(tp=pl.col("to").shift(1).over("company_id")).collect())
    r = r.with_columns(rk=pl.col("tp").rank("ordinal", descending=True).over("datetime"))
    ew = (r.filter(pl.col("rk") <= 500, pl.col("ret").abs() < 0.5).group_by("datetime").agg(pl.col("ret").mean())
           .sort("datetime").to_pandas().set_index("datetime")["ret"])
    return ew[(ew.index >= start) & (ew.index <= end)]


def simulate(args, book, cand, piv, E, log):
    cfg = dict(E.cfg_defaults["risk"])
    cfg.update({"swing_cap": args.swing_cap, "max_swing_positions": args.max_positions,
                "max_position_frac": args.max_position_frac, "es_cap": args.es_cap})
    costs = E.costs.CostModel(E.cfg_defaults["costs"])
    C, O, V = piv["close"], piv["open"], piv["volume"]
    dates = C.index
    rets = C.pct_change(fill_method=None)
    adv20 = (C * V).rolling(20, min_periods=5).mean().shift(1)        # known before the order day
    sig20 = rets.rolling(20, min_periods=10).std().shift(1)
    gapr = (O / C.shift(1) - 1)
    order = {s: i for i, (s, _) in enumerate(book)}
    cand = cand.copy()
    cand["_o"] = cand["strategy"].map(order)
    # same-day candidates: --book order, then score (if the strategy has one), then a seeded random draw -
    # NOT the alphabet, which would always favour the same names when slots are scarce
    cand["_r"] = np.random.default_rng(args.seed).random(len(cand))
    cand["_s"] = cand["score"].fillna(-np.inf)
    cand = cand.sort_values(["order_date", "_o", "_s", "_r"], ascending=[True, True, False, True])
    by_day = {d: g for d, g in cand.groupby("order_date")}
    stats = {s: StratStats() for s, _ in book}
    meta = {}
    for s, _ in book:
        cls = E.classes[BASE.get(s, s)]
        meta[s] = dict(prior=cls.prior, style=cls.style, probation=bool(getattr(cls, "probation", False)))

    start = cand["order_date"].min()
    end = max(cand["exit_date"].max(), cand["order_date"].max())
    days = dates[(dates >= start) & (dates <= end)]
    cash, pos, taken, skipped, eq_rows = float(args.capital), {}, [], [], []
    last_close = {}
    equity_prev = float(args.capital)

    def fill_cost(side, price, qty, key, d):
        c = costs.fill_costs(product="CNC", side=side, price=price, qty=qty)
        a, sg = adv20.at[d, key] if key in adv20.columns else np.nan, sig20.at[d, key] if key in sig20.columns else np.nan
        imp = costs.impact_fraction(None if not np.isfinite(sg) else float(sg), price * qty,
                                    None if not np.isfinite(a) else float(a))
        return c + imp * price * qty

    def book_es(extra_key, extra_qty, price, d):
        """97.5% one-day historical expected shortfall of the book plus a new position (rupees)."""
        hold = {k: p["qty"] for k, p in pos.items()}
        hold[extra_key] = hold.get(extra_key, 0) + extra_qty
        i = dates.get_loc(d)
        R = rets.iloc[max(0, i - 500):i][list(hold)].dropna()
        if len(R) < 60:
            return None
        val = np.array([hold[k] * (last_close.get(k) or price) for k in R.columns])
        return E.ind.expected_shortfall(R.to_numpy() @ val, 0.975)

    for d in days:
        # ---- 1. exits booked on this day --------------------------------------------------------------
        for key in [k for k, p in pos.items() if p["exit_date"] == d]:
            p = pos.pop(key)
            px = p["exit_price"]
            sc = fill_cost("SELL", px, p["qty"], key, d)
            cash += p["qty"] * px - sc
            pnl = p["qty"] * (px - p["fill"]) - p["buy_cost"] - sc
            r_mult = pnl / max(p["qty"] * p["risk_ps"], 1e-9)
            stats[p["strategy"]].add(r_mult)
            taken.append({**p["row"], "qty": p["qty"], "value": p["qty"] * p["fill"], "risk_rs": p["qty"] * p["risk_ps"],
                          "costs": p["buy_cost"] + sc, "pnl": pnl, "r_multiple": r_mult,
                          "ret_pct": pnl / (p["qty"] * p["fill"]) * 100, "kelly_risk_frac": p["frac"], "mult": p["mult"]})
        # ---- 2. entries -----------------------------------------------------------------------------
        g = by_day.get(d)
        n_new = {}
        if g is not None:
            for row in g.to_dict("records"):
                s, key = row["strategy"], row["company_id"]

                def skip(reason):
                    skipped.append({**row, "reason": reason})
                if key in pos:
                    skip("already_holding")
                    continue
                if len(pos) >= cfg["max_swing_positions"]:
                    skip("max_positions")
                    continue
                if n_new.get(s, 0) >= MAX_NEW_PER_DAY.get(BASE.get(s, s), 10**9):
                    skip("max_new_per_day")
                    continue
                frac, fstar = kelly_fraction(stats[s], meta[s]["prior"], cfg, cfg["swing_cap"])
                if frac <= 0:
                    if args.no_switch_off:
                        frac = cfg["swing_cap"] * 0.25          # v3.11's size when f* <= 0 but not yet switched off
                    else:
                        skip("strategy_switched_off_negative_kelly")
                        continue
                ok, pm = posture_mult(row["regime"], meta[s]["style"])
                if not ok:
                    skip("regime_blocked")
                    continue
                mult = pm * (0.5 if meta[s]["probation"] else 1.0)
                entry, stop = float(row["fill_price"]), float(row["stop"])
                risk_amt = max(equity_prev, 0.0) * frac * mult
                per = max(entry - stop, entry * float(cfg.get("min_risk_pct", 0.001)))
                qty = math.floor(risk_amt / per)
                caps = {"max_order_value": math.floor(cfg["max_order_value"] / (entry * (1 + cfg.get("limit_band_pct", 0.01)))),
                        "max_position_frac": math.floor(cfg["max_position_frac"] * equity_prev / entry)}
                a = adv20.at[d, key] if key in adv20.columns else np.nan
                if np.isfinite(a) and a > 0:
                    caps["adv_cap"] = math.floor(cfg["adv_cap"] * a / entry)
                i = dates.get_loc(d)
                gq = gapr.iloc[max(0, i - 750):i][key].dropna() if key in gapr.columns else pd.Series(dtype=float)
                gq = (float(abs(min(gq.quantile(0.01), 0.0))) or 0.01) if len(gq) >= 25 else 0.05
                caps["gap_cap"] = math.floor(cfg["gap_multiple"] * risk_amt / (entry * gq))
                binding = min(caps, key=caps.get)
                q0 = qty
                qty = max(0, min([qty] + list(caps.values())))
                why = binding if qty < q0 else "risk"
                if qty > 0:
                    es = book_es(key, qty, entry, d)
                    cap_rs = cfg["es_cap"] * equity_prev
                    if es is not None and es > cap_rs and es > 0:
                        qty = int(math.floor(qty * cap_rs / es))
                        why = "es_cap"
                if qty > 0:
                    bc = fill_cost("BUY", entry, qty, key, d)
                    if qty * entry + bc > cash:                     # delivery: no leverage
                        qty = int(max(0, (cash * 0.999) // (entry * 1.003)))
                        why = "cash"
                if qty <= 0:
                    skip(f"size_zero_{why}")
                    continue
                bc = fill_cost("BUY", entry, qty, key, d)
                cash -= qty * entry + bc
                n_new[s] = n_new.get(s, 0) + 1
                pos[key] = {"strategy": s, "qty": qty, "fill": entry, "risk_ps": per, "buy_cost": bc,
                            "exit_date": row["exit_date"], "exit_price": float(row["exit_price"]), "frac": frac,
                            "mult": mult, "row": {**row, "size_limited_by": why}}
        # ---- 3. mark to market ------------------------------------------------------------------------
        for k in pos:
            v = C.at[d, k] if k in C.columns else np.nan
            if np.isfinite(v):
                last_close[k] = float(v)
        mv = sum(p["qty"] * last_close.get(k, p["fill"]) for k, p in pos.items())
        equity_prev = cash + mv
        eq_rows.append({"date": d, "equity": equity_prev, "cash": cash, "invested": mv, "positions": len(pos)})
    # positions still open at the end are marked at the last close (already in equity)
    for k, p in pos.items():
        taken.append({**p["row"], "qty": p["qty"], "value": p["qty"] * p["fill"], "risk_rs": p["qty"] * p["risk_ps"],
                      "costs": p["buy_cost"], "pnl": p["qty"] * (last_close.get(k, p["fill"]) - p["fill"]) - p["buy_cost"],
                      "r_multiple": np.nan, "ret_pct": np.nan, "kelly_risk_frac": p["frac"], "mult": p["mult"],
                      "exit_reason": "open_at_end"})
    eq = pd.DataFrame(eq_rows).set_index("date")
    return eq, pd.DataFrame(taken), pd.DataFrame(skipped)


# =====================================================================================================
# REPORT
# =====================================================================================================
def metrics(eq, capital):
    e = eq["equity"]
    r = e.pct_change().fillna(e.iloc[0] / capital - 1)
    yrs = max((e.index[-1] - e.index[0]).days / 365.25, 1e-9)
    cagr = (e.iloc[-1] / capital) ** (1 / yrs) - 1
    vol = r.std() * math.sqrt(248)
    dd = e / e.cummax() - 1
    # longest time below a previous peak
    under, longest, start = dd < 0, 0, None
    for d, u in under.items():
        if u and start is None:
            start = d
        if not u and start is not None:
            longest = max(longest, (d - start).days)
            start = None
    if start is not None:
        longest = max(longest, (e.index[-1] - start).days)
    return {"final_equity": e.iloc[-1], "total_return_pct": (e.iloc[-1] / capital - 1) * 100, "cagr_pct": cagr * 100,
            "vol_pct": vol * 100, "sharpe_rf6.5": (cagr - 0.065) / vol if vol > 0 else np.nan,
            "max_drawdown_pct": dd.min() * 100, "max_dd_date": dd.idxmin(), "longest_underwater_days": longest,
            "calmar": (cagr / abs(dd.min())) if dd.min() < 0 else np.nan,
            "avg_invested_pct": (eq["invested"] / eq["equity"]).mean() * 100,
            "avg_positions": eq["positions"].mean()}


def bench_metrics(series_ret, capital_index):
    lvl = (1 + series_ret.fillna(0)).cumprod()
    yrs = max((lvl.index[-1] - lvl.index[0]).days / 365.25, 1e-9)
    cagr = lvl.iloc[-1] ** (1 / yrs) - 1
    dd = lvl / lvl.cummax() - 1
    vol = series_ret.std() * math.sqrt(248)
    return {"total_return_pct": (lvl.iloc[-1] - 1) * 100, "cagr_pct": cagr * 100, "vol_pct": vol * 100,
            "max_drawdown_pct": dd.min() * 100, "sharpe_rf6.5": (cagr - 0.065) / vol if vol > 0 else np.nan}


def write_report(out, args, book, eq, taken, skipped, mk, ew, log):
    out.mkdir(parents=True, exist_ok=True)
    eq.to_csv(out / "equity.csv")
    taken.to_csv(out / "trades.csv", index=False)
    skipped.to_csv(out / "skipped.csv", index=False)
    m = metrics(eq, args.capital)
    idx = eq.index
    nifty = mk["nifty_close"].reindex(idx).ffill().pct_change()
    ew = ew.reindex(idx).fillna(0)
    bn, be = bench_metrics(nifty, 1), bench_metrics(ew, 1)
    mret = eq["equity"].resample("ME").last().pct_change()
    mret.iloc[0] = eq["equity"].resample("ME").last().iloc[0] / args.capital - 1
    yret = eq["equity"].resample("YE").last()
    yret = yret / yret.shift(1).fillna(args.capital) - 1
    pd.DataFrame({"portfolio_pct": mret * 100}).to_csv(out / "monthly_returns.csv")
    L = [f"Portfolio backtest  {idx[0]:%Y-%m-%d} .. {idx[-1]:%Y-%m-%d}   ({args.run})",
         f"book: {', '.join(f'{s}:{v}' for s, v in book)}",
         f"capital Rs {args.capital:,.0f}; max {args.max_positions} positions; risk cap {args.swing_cap:.2%} per trade; "
         f"max {args.max_position_frac:.0%} of equity per position; ES cap {args.es_cap:.0%}", "",
         f"{'':28s}{'portfolio':>12s}{'Nifty 50':>12s}{'EW top-500':>12s}"]
    for k, lab in [("total_return_pct", "total return %"), ("cagr_pct", "CAGR %"), ("vol_pct", "volatility %"),
                   ("max_drawdown_pct", "max drawdown %"), ("sharpe_rf6.5", "Sharpe (rf 6.5%)")]:
        L.append(f"{lab:28s}{m[k]:12.2f}{bn[k]:12.2f}{be[k]:12.2f}")
    L += [f"{'calmar (CAGR / max DD)':28s}{m['calmar']:12.2f}",
          f"{'deepest drawdown on':28s}{m['max_dd_date']:%Y-%m-%d}",
          f"{'longest under water (days)':28s}{m['longest_underwater_days']:12d}",
          f"{'average invested %':28s}{m['avg_invested_pct']:12.1f}",
          f"{'average open positions':28s}{m['avg_positions']:12.1f}",
          f"{'final equity Rs':28s}{m['final_equity']:12,.0f}", "", "Calendar years (%):"]
    for d, v in yret.items():
        L.append(f"  {d.year}: {v * 100:7.2f}")
    L += ["", "Per strategy (trades taken):"]
    if len(taken):
        closed = taken[taken["exit_reason"] != "open_at_end"]
        for s, g in closed.groupby("strategy"):
            L.append(f"  {s:30s} {len(g):4d} trades  win {(g['pnl'] > 0).mean():5.1%}  avg {g['ret_pct'].mean():6.2f}%  "
                     f"avg R {g['r_multiple'].mean():5.2f}  P&L Rs {g['pnl'].sum():12,.0f}  costs Rs {g['costs'].sum():10,.0f}  "
                     f"avg size Rs {g['value'].mean():10,.0f}")
        lim = taken["size_limited_by"].value_counts()
        L += ["", "What set the position size (trades taken): " + ", ".join(f"{k} {v}" for k, v in lim.items())]
    L += ["", "Signals NOT taken:"]
    if len(skipped):
        for (s, r), n in skipped.groupby(["strategy", "reason"]).size().items():
            L.append(f"  {s:30s} {r:40s} {n:5d}")
    else:
        L.append("  none")
    L += ["", "Notes: entries/exits are the single-signal backtest's; v3.11 money management added on top (see the",
          "script header). Not modelled: sector cap, crowding breaker, crisis liquidation, intraday order of events."]
    (out / "summary.txt").write_text("\n".join(L), encoding="utf-8")
    (out / "run_config.json").write_text(json.dumps({k: (v if not isinstance(v, Path) else str(v))
                                                     for k, v in vars(args).items() if k != "func"}, indent=2, default=str))
    return m, bn, be, "\n".join(L)


def parse_book(text):
    book = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        s, _, v = part.partition(":")
        book.append((s.strip(), (v or "rules_nostop").strip()))
    return book


def cmd_run(args, quiet=False):
    t0 = time.time()
    log = lambda m: print(f"{dt.datetime.now():%H:%M:%S}  {m}", flush=True)
    E = BS.load_engine(args.engine_dir)
    import importlib
    E.costs = importlib.import_module("nsealgo.core.costs")
    book = parse_book(args.book)
    cand, piv, mk, data_dir, run_dir = load_inputs(args, book, log)
    eq, taken, skipped = simulate(args, book, cand, piv, E, log)
    ew = ew500_index(data_dir, eq.index[0].to_pydatetime(), eq.index[-1].to_pydatetime())
    name = args.name or f"{run_dir.name}__{'+'.join(s for s, _ in book)}"[:120]
    out = Path(args.out_dir) if args.out_dir else C.location("portfolio_results") / name
    m, bn, be, text = write_report(out, args, book, eq, taken, skipped, mk, ew, log)
    if not quiet:
        print(text)
    log(f"done in {time.time() - t0:.0f}s -> {out}")
    return m


def cmd_compare(args):
    """Each book is simulated with --seeds different random orders of same-day candidates; the table shows
    the median and the range, because when slots are scarce WHICH signals get in is partly luck."""
    rows = []
    base = Path(args.out_dir) if args.out_dir else C.location("portfolio_results")
    for i, b in enumerate(args.books.split("|"), 1):
        a = argparse.Namespace(**vars(args))
        a.book, a.name = b, None
        a.out_dir = str(base / f"book{i}_{'+'.join(s for s, _ in parse_book(b))}"[:200]) if args.out_dir else None
        ms = []
        for sd in range(1, args.seeds + 1):
            a.seed = sd
            if a.out_dir and args.seeds > 1:
                a.out_dir = str(Path(a.out_dir).with_name(Path(a.out_dir).name.split("__seed")[0] + f"__seed{sd}"))
            ms.append(cmd_run(a, quiet=True))
        row = {"book": b}
        for k in ("cagr_pct", "max_drawdown_pct", "sharpe_rf6.5", "calmar", "avg_invested_pct", "avg_positions"):
            v = np.array([m[k] for m in ms], float)
            row[k] = round(float(np.median(v)), 2)
            if args.seeds > 1 and k in ("cagr_pct", "max_drawdown_pct"):
                row[k + "_range"] = f"{v.min():.1f} .. {v.max():.1f}"
        rows.append(row)
    df = pd.DataFrame(rows)
    out = base / f"compare_{dt.datetime.now():%Y%m%d_%H%M}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    with pd.option_context("display.width", 250, "display.max_columns", 20):
        print(df.to_string(index=False))
    print(f"-> {out}")


def main():
    ap = HelpfulParser(description=__doc__, epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    fmt = dict(epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name, hlp in (("run", "simulate one book"), ("compare", "simulate several books, one line each")):
        p = sub.add_parser(name, help=hlp, **fmt)
        p.add_argument("--run", required=True, help="backtest run name (folder in backtest_results) or its path")
        p.add_argument("--data-dir", help="prices used by the backtest (default %%NSEDATAENGINE_HOME%%\\data)")
        if name == "run":
            p.add_argument("--book", default="pead_200dma:target_nostop,pullback:rules_nostop",
                           help="strategy:variant list, in priority order (default: %(default)s)")
        else:
            p.add_argument("--books", required=True, help="books separated by |, each like the --book of run")
        p.add_argument("--capital", type=float, default=1_000_000, help="starting capital in rupees (default 10 lakh)")
        p.add_argument("--max-positions", type=int, default=12, help="v3.11 max_swing_positions (default 12)")
        p.add_argument("--swing-cap", type=float, default=0.01, help="max risk per trade, fraction of equity (default 0.01)")
        p.add_argument("--max-position-frac", type=float, default=0.2, help="max position value / equity (default 0.2)")
        p.add_argument("--es-cap", type=float, default=0.03, help="book 97.5%% 1-day ES cap / equity (default 0.03)")
        p.add_argument("--no-switch-off", action="store_true",
                       help="do not switch a strategy off when its Kelly f* turns negative after 30 trades (v3.11 does)")
        p.add_argument("--seed", type=int, default=1, help="random order of same-day candidates (default 1)")
        if name == "compare":
            p.add_argument("--seeds", type=int, default=5, help="runs per book with different seeds (default 5)")
        p.add_argument("--start", help="first order day YYYY-MM-DD (default: first trade in the run)")
        p.add_argument("--end", help="last order day YYYY-MM-DD")
        p.add_argument("--engine-dir", help="v3.11 project folder (default: %%NSEALGO_SRC%% or ${NSE_ROOT}/code/NSEALGOENGINE)")
        p.add_argument("--name", help="output folder name")
        p.add_argument("--out-dir", help="output folder")
    args = parse_args_or_help(ap)
    {"run": cmd_run, "compare": cmd_compare}[args.cmd](args)


if __name__ == "__main__":
    main()
