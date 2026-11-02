"""
nse_backtest.py - whole-universe daily backtest of the Nsealgo v3.11 swing strategies.

For every trading day in the test window and every stock in the strategy's universe that day, each strategy
decides - with data up to that day's close only - whether v3.11 would buy. Every signal is then simulated
against the prices that actually followed, in four variants:

                          sell rule                                          protective stop
  rules_nostop    v3.11's own exit rule (e.g. close < 20-EMA), sold at the       no
                  next day's open; also its time stop
  rules_stop      same                                                           yes (v3.11's stop)
  target_nostop   sell limit at the predicted target = buy + payoff x risk       no
                  (payoff = the strategy's own v3.11 prior win/loss ratio)
  target_stop     same                                                           yes

Rules common to all variants
  Buy           a day order at the predicted buy price on the next trading day after the signal:
                  limit (all but PEAD): open <= buy -> filled at the open; low <= buy -> filled at buy;
                                        else cancelled at the close: no position, no P&L ("buy_not_filled")
                  PEAD buy-stop-limit, re-placed each morning for up to 3 sessions as v3.11 does:
                                        high >= trigger -> filled at max(open, trigger) unless the open is above
                                        the limit (trigger + 1%), in which case only if the low comes back to it
  Sell limit    open >= target -> sold at the open; high >= target -> sold at the target
  Stop          open <= stop -> sold at the open; low <= stop -> sold at the stop
  Same day      intraday order of events is unknown with daily bars, so: the buy day's high only counts for
                the target if the buy filled at the open; if a stop and a target are both touched on one day,
                the stop is assumed first (conservative); a stop touched on an intraday-filled buy day counts.
  Rule exits    v3.11 checks its exit rule at the close; the backtest sells at the next day's open.
  3 months      a position still open on the last session on/before buy day + 3 months is closed at
                (open + close) / 2 of that session ("time_exit_3m")
  Other         stock stops trading -> closed at (open+close)/2 of its last session ("stopped_trading");
                data ends first -> marked at the last close ("open_at_data_end", excluded from statistics)
P&L is in percent of the fill price, minus --cost-bps round-trip costs (default 0).

Every signal also carries v3.11's regime state for that day; the report shows each strategy over all
signals and over the signals v3.11's regime filter would have allowed.

Outputs (in <project>\\backtest_results\\<run name>, or --out-dir)
  trades.parquet / trades.csv   one row per signal per variant
  signals.parquet               the raw signals (stock, day, buy, stop, target, regime, score ...)
  summary.csv                   per strategy x variant x regime filter
  summary_by_year.csv           the same per calendar year
  report.txt                    readable summary + every simplification made
  run_config.json, log.txt
"""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import argparse
import datetime as dt
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nse_common as NC  # noqa: E402  (installs missing packages)
import nse_contract as C  # noqa: E402  folders, file names and columns (contract\\schema.yaml)
from nse_common import HelpfulParser, parse_args_or_help, project_home, np, pl  # noqa: E402

# extra packages the v3.11 code needs (pandas panels, scipy/hmmlearn for the regime model, ...)
for _mod, _pip in (("pandas", "pandas"), ("scipy", "scipy"), ("hmmlearn", "hmmlearn"), ("statsmodels", "statsmodels"),
                   ("yaml", "PyYAML"), ("psutil", "psutil"), ("threadpoolctl", "threadpoolctl")):
    NC.REQUIRED_PACKAGES[_mod] = _pip
NC.ensure_packages()
import pandas as pd  # noqa: E402
import bt_strategies as BS  # noqa: E402

EXAMPLES = r"""
examples:
  every v3.11 swing strategy over the last 5 years (data: %NSEDATAENGINE_HOME%\data):
    python nse_backtest.py run

  selected strategies / another window / costs of 0.25% per round trip:
    python nse_backtest.py run --strategies momentum,turtle,supertrend
    python nse_backtest.py run --start 2021-10-01 --end 2026-09-30
    python nse_backtest.py run --cost-bps 25

  the 5-year fresh build instead of the updater data (no survivorship bias, shorter history):
    python nse_backtest.py run --source fresh

  survivorship-free long build (nse_fresh_build.py --data-dir data_fresh_2019 build --start 2019-10-01)
  with NSE's exact daily F&O list (nse_fno_universe.py build) instead of "top 200 by turnover":
    python nse_backtest.py run --data-dir ${NSE_ROOT}/code\NSEDATAENGINE\data_fresh_2019 --fno-universe exact

  v3.11 source somewhere else:
    python nse_backtest.py run --engine-dir D:\code\NSEALGOENGINE

  only download / refresh NSE's results calendar (used by PEAD and the results blackouts):
    python nse_backtest.py results

  the three tweaked strategies only, reusing the regime of an earlier run (fast):
    python nse_backtest.py run --data-dir data_fresh_2019 --fno-universe exact --regime-from v311_sf_exact2 ^
           --strategies pead_200dma,resid_momentum_hold63,resid_momentum_200dma_hold63 --name v311_sf_variants

  signal matrix (date x script, BUY / SELL per strategy); several runs are combined into one table:
    python nse_backtest.py matrix --run v311_sf_exact2,v311_sf_variants

  list the strategies:
    python nse_backtest.py list
"""

VARIANTS = ("rules_nostop", "rules_stop", "target_nostop", "target_stop")


# =====================================================================================================
# TRADE SIMULATION (runs in worker processes, one company per task)
# =====================================================================================================
def _add_months(d, months):
    import calendar
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def _buy(o, h, l, i, kind, buy, limit):
    """Fill price and type for a day order on row i, or (None, None)."""
    if kind == "limit":
        if o[i] <= buy:
            return o[i], "at_open"
        if l[i] <= buy:
            return buy, "limit"
        return None, None
    # buy-stop-limit: triggers when price trades at/above `buy`, may pay up to `limit`
    if h[i] < buy:
        return None, None
    if o[i] >= buy:
        if o[i] <= limit:
            return o[i], "at_open"
        return (limit, "limit") if l[i] <= limit else (None, None)
    return buy, "stop_trigger"


def _sell(o, h, l, c, dates, j, j_end, n, data_end, horizon, fill, ftype, stop, tgt, use_stop, use_target, rule_fn):
    """Walk forward from the buy day j. Returns (exit row, exit price, reason)."""
    for d in range(j, j_end + 1):
        first = d == j
        # 1) protective stop (intraday; on the buy day it counts even for an intraday fill - conservative)
        if use_stop and l[d] <= stop:
            if not first and o[d] <= stop:
                return d, o[d], "stop"
            return d, stop, "stop"
        # 2) sell limit at the target
        if use_target:
            if first:
                if ftype == "at_open" and h[d] >= tgt:
                    return d, tgt, "target"
            elif o[d] >= tgt:
                return d, o[d], "target"
            elif h[d] >= tgt:
                return d, tgt, "target"
        # 3) v3.11 exit rule, checked at the close, executed at the next open
        if rule_fn is not None and d < j_end and rule_fn(d, d - j):
            if d + 1 < n:
                return d + 1, o[d + 1], "rule_exit"
            break
    # 4) nothing hit within 3 months
    k = j_end
    if j_end < n - 1 or dates[n - 1] >= horizon:
        return k, (o[k] + c[k]) / 2, "time_exit_3m"         # the 3 months are complete
    if dates[n - 1] < data_end:
        return k, (o[k] + c[k]) / 2, "stopped_trading"      # delisted / suspended before 3 months
    return k, c[k], "open_at_data_end"                       # still open on the last day of data


def simulate_company(task):
    cid, dates, o, h, l, c, sigs, flags, hold_months, cost, data_end = task
    n = len(dates)
    out = []
    for s in sigs:
        base = {"strategy": s["strategy"], "company_id": cid, "signal_date": s["signal_date"],
                "buy_price": s["buy_price"], "stop": s["stop"], "target": s["target"], "regime": s["regime"],
                "regime_allowed": s["regime_allowed"], "score": s["score"]}
        i0 = int(np.searchsorted(dates, np.datetime64(s["signal_date"], "ns"), side="right"))
        fill = ftype = None
        j = None
        for k in range(int(s["arm_days"])):
            if i0 + k >= n:
                break
            fill, ftype = _buy(o, h, l, i0 + k, s["order_type"], s["buy_price"], s["limit_price"])
            if fill is not None:
                j = i0 + k
                break
        if j is None:
            reason = "no_next_day" if i0 >= n else "buy_not_filled"
            for v in VARIANTS:
                out.append({**base, "variant": v, "filled": False, "exit_reason": reason})
            continue
        horizon = np.datetime64(_add_months(dates[j].astype("datetime64[D]").astype(dt.date), hold_months), "D").astype("datetime64[ns]")
        j_end = int(np.searchsorted(dates, horizon, side="right")) - 1
        flag = flags.get(s["strategy"])
        max_hold = s["max_hold"]
        risk = max(fill - s["stop"], 0.01)

        if s["rule"] == "momentum":            # close < 20-EMA, or no +1R progress after time_stop sessions
            ts = int(s["hold_param"])

            def rule_fn(d, held, flag=flag, ts=ts, j=j, fill=fill, risk=risk):
                if flag is not None and flag[d]:
                    return True
                return held >= ts and h[d - held + 1:d + 1].max() < fill + risk if held > 0 else False
        elif s["rule"] == "resid":             # s-score reverted / unavailable / results, or 2.5 half-lives
            mh = int(s["hold_param"])

            def rule_fn(d, held, flag=flag, mh=mh):
                return (flag is not None and flag[d]) or held >= mh
        else:
            def rule_fn(d, held, flag=flag, mh=max_hold):
                return (flag is not None and flag[d]) or held >= mh

        for v in VARIANTS:
            use_stop = v.endswith("_stop")
            use_target = v.startswith("target")
            k, px, reason = _sell(o, h, l, c, dates, j, j_end, n, data_end, horizon, fill, ftype, s["stop"], s["target"],
                                  use_stop, use_target, None if use_target else rule_fn)
            ret = (px - fill) / fill * 100 - cost
            out.append({**base, "variant": v, "filled": True, "order_date": dates[j], "fill_price": float(fill),
                        "fill_type": ftype, "exit_date": dates[k], "exit_price": float(px), "exit_reason": reason,
                        "days_held_trading": int(k - j),
                        "days_held_calendar": int((dates[k] - dates[j]) / np.timedelta64(1, "D")),
                        "return_pct": float(ret),
                        "max_drawdown_pct": float((l[j:k + 1].min() - fill) / fill * 100),
                        "max_gain_pct": float((h[j:k + 1].max() - fill) / fill * 100)})
    return out


# =====================================================================================================
# REPORTING
# =====================================================================================================
def summarise(t: pl.DataFrame, by):
    closed = pl.col("filled") & (pl.col("exit_reason") != "open_at_data_end")
    r = pl.col("return_pct")
    return (t.group_by(by).agg(
        signals=pl.len(),
        filled=pl.col("filled").sum(),
        fill_rate_pct=(pl.col("filled").mean() * 100).round(1),
        closed_trades=closed.sum(),
        win_rate_pct=((r.filter(closed) > 0).mean() * 100).round(1),
        avg_return_pct=r.filter(closed).mean().round(3),
        median_return_pct=r.filter(closed).median().round(3),
        avg_win_pct=r.filter(closed & (r > 0)).mean().round(3),
        avg_loss_pct=r.filter(closed & (r <= 0)).mean().round(3),
        profit_factor=(r.filter(closed & (r > 0)).sum() / -r.filter(closed & (r < 0)).sum()).round(2),
        target_hits=(pl.col("exit_reason") == "target").sum(),
        stop_hits=(pl.col("exit_reason") == "stop").sum(),
        rule_exits=(pl.col("exit_reason") == "rule_exit").sum(),
        time_exits=(pl.col("exit_reason") == "time_exit_3m").sum(),
        avg_time_exit_return_pct=r.filter(pl.col("exit_reason") == "time_exit_3m").mean().round(3),
        avg_days_to_target=pl.col("days_held_trading").filter(pl.col("exit_reason") == "target").mean().round(1),
        avg_days_held=pl.col("days_held_trading").filter(closed).mean().round(1),
        worst_trade_pct=r.filter(closed).min().round(2),
        best_trade_pct=r.filter(closed).max().round(2),
        avg_max_drawdown_pct=pl.col("max_drawdown_pct").filter(closed).mean().round(2),
        open_at_data_end=(pl.col("exit_reason") == "open_at_data_end").sum(),
    ).sort(by))


def mark_fresh(t: pl.DataFrame) -> pl.DataFrame:
    """fresh = the strategy did not already hold the stock (in that variant) on the signal day.

    Signals often repeat on consecutive days (a stock above its 55-day high stays above it). v3.11 never
    buys a stock it already holds, so 'fresh' signals are the ones it would actually act on; a repeat
    signal while a position is open is kept in the data but marked fresh = False. A signal whose buy order
    did not fill opens nothing, so the next day's signal can still be fresh."""
    t = t.sort("strategy", "company_id", "variant", "signal_date")
    keys = t.select("strategy", "company_id", "variant").to_numpy()
    sd = t["signal_date"].to_numpy()
    ed = (t["exit_date"].cast(pl.Datetime("ns")).to_numpy() if "exit_date" in t.columns
          else np.full(len(t), np.datetime64("NaT"), dtype="datetime64[ns]"))
    sd = sd.astype("datetime64[ns]")
    filled = t["filled"].to_numpy()
    fresh = np.ones(len(t), dtype=bool)
    busy_until, prev = None, None
    for i in range(len(t)):
        k = tuple(keys[i])
        if k != prev:
            busy_until, prev = None, k
        if busy_until is not None and sd[i] < busy_until:
            fresh[i] = False
            continue
        if filled[i] and not np.isnat(ed[i]):
            busy_until = ed[i]
    return t.with_columns(pl.Series("fresh", fresh))


def _with_filter(t):
    """Stack the signal sets the report compares:
         all             every signal, including repeats while already holding
         fresh           only signals v3.11 would act on (not already holding the stock)
         fresh_regime    fresh signals on days v3.11's regime filter allows new entries"""
    return pl.concat([t.with_columns(pl.lit("all").alias("signal_set")),
                      t.filter(pl.col("fresh")).with_columns(pl.lit("fresh").alias("signal_set")),
                      t.filter(pl.col("fresh") & pl.col("regime_allowed")).with_columns(pl.lit("fresh_regime").alias("signal_set"))])


def write_report(out_dir, cfg, summ, notes):
    cols = ["strategy", "variant", "signals", "fill_rate_pct", "closed_trades", "win_rate_pct", "avg_return_pct",
            "median_return_pct", "profit_factor", "target_hits", "stop_hits", "rule_exits", "time_exits",
            "avg_days_held"]
    L = [f"Nsealgo v3.11 swing backtest - {cfg['run_name']}", "=" * 110,
         f"data          : {cfg['data']}",
         f"test window   : {cfg['start']} .. {cfg['end']}",
         f"strategies    : {', '.join(cfg['strategies'])}",
         f"costs         : {cfg['cost_bps']} bps per round trip;  max holding {cfg['hold_months']} months", ""]
    with pl.Config(tbl_rows=500, tbl_cols=30, tbl_width_chars=250, tbl_hide_dataframe_shape=True,
                   tbl_hide_column_data_types=True, fmt_str_lengths=20):
        for ss, what in (("all", "every signal, including repeats while the stock is already held"),
                         ("fresh", "signals v3.11 would act on: the stock is not already held by the strategy"),
                         ("fresh_regime", "fresh signals on days v3.11's regime filter allows new entries")):
            L.append(f"--- signal set: {ss}  ({what})")
            L.append(str(summ.filter(pl.col("signal_set") == ss).select(cols)))
            L.append("")
    L.append("How to read: avg_return_pct is the average result per trade in % of the buy price; profit_factor =")
    L.append("sum of winning % / sum of losing %; time_exits = sold at (open+close)/2 after 3 months.")
    L += ["", "Simplifications and data notes:"] + [f"  - {n}" for n in notes]
    (out_dir / "report.txt").write_text("\n".join(L), encoding="utf-8")


# =====================================================================================================
# COMMANDS
# =====================================================================================================
def _data_paths(args):
    v = "fresh" if args.source == "fresh" else "master"
    d = Path(args.data_dir) if getattr(args, "data_dir", None) else None   # any folder holding the two files
    return C.file_path("raw_ohlcv", v, folder=d), C.file_path("market", v, folder=d)


def _results(args, start, end, log):
    cache = C.location("events")
    df = BS.fetch_results(cache, start, end, NC.HEADERS, log, offline=getattr(args, "offline", False))
    if df is not None:
        df.to_csv(cache / "results_calendar_nse.csv", index=False)
    return df


def _fno_mask(args, P, start, log):
    """dates x stocks boolean frame: True where the stock had stock futures on NSE that day.
    A trading day missing from the F&O file (download failed) takes the previous day's list (up to 5 days)."""
    path = Path(args.fno_file) if args.fno_file else C.file_path("fno_universe", "ref")
    if not path.exists():
        sys.exit(f"{path} not found - build it first: python nse_fno_universe.py build")
    fu = pl.read_parquet(path).select("date", "company_id").unique().to_pandas()
    fu["date"] = pd.to_datetime(fu["date"])
    m = pd.crosstab(fu["date"], fu["company_id"]).gt(0).sort_index()
    unmatched = sorted(set(m.columns) - set(P.C.columns))
    m = m.reindex(columns=P.C.columns, fill_value=False)
    m = m.reindex(P.dates, method="ffill", limit=5).fillna(False).astype(bool)
    first = fu["date"].min()
    log(f"exact F&O universe: {path} ({first:%Y-%m-%d} .. {fu['date'].max():%Y-%m-%d}), "
        f"{int(m.loc[m.index >= start].sum(axis=1).median())} stocks on a median day")
    if first > start:
        log(f"WARNING: the F&O file starts after the test window ({start:%Y-%m-%d}); F&O strategies get no "
            f"signals before {first:%Y-%m-%d} - run: python nse_fno_universe.py build --start <earlier>")
    if unmatched:
        log(f"  {len(unmatched)} F&O symbols not among the loaded stocks (not in the price data, or never in the "
            f"top 500 by turnover): {', '.join(unmatched[:15])}{' ...' if len(unmatched) > 15 else ''}")
    return m


def _run_dir(name):
    p = Path(name)
    return p if p.is_dir() else C.location("backtest_results") / name


def _regime(args, E, full_mk, days, workers, out_dir, log):
    """v3.11's regime for every test day. Computing it (HMM fits) is the slowest part of a run, and it depends
    only on Nifty / VIX, so --regime-from <earlier run on the same data> reuses that run's regime and computes
    only the days it does not have. Every run saves its own regime.parquet for later reuse."""
    known = {}
    if getattr(args, "regime_from", None):
        rd = _run_dir(args.regime_from)
        if (rd / "regime.parquet").exists():
            df = pd.read_parquet(rd / "regime.parquet")
            known = dict(zip(pd.to_datetime(df["date"]), df["regime"]))
        elif (rd / "signals.parquet").exists():                 # older runs: the regime stored with each signal
            df = pd.read_parquet(rd / "signals.parquet", columns=["signal_date", "regime"]).drop_duplicates()
            df = df[df["regime"] != "unknown"]
            known = dict(zip(pd.to_datetime(df["signal_date"]), df["regime"]))
        else:
            sys.exit(f"--regime-from: no regime.parquet or signals.parquet in {rd}")
        log(f"regime: {len(known)} sessions reused from {rd.name}")
    need = pd.DatetimeIndex([d for d in days if d not in known])
    if len(need):
        new = BS.regime_states(E, full_mk["nifty_close"], full_mk["vix"], need, workers, log)
        known.update({pd.Timestamp(k): v for k, v in new.items()})
    reg = pd.Series({d: known[d] for d in days if d in known})
    pd.DataFrame({"date": reg.index, "regime": reg.values}).to_parquet(out_dir / "regime.parquet", index=False)
    return reg


EXIT_WORDS = {"rule_exit": "exit rule", "stop": "stop", "target": "target", "time_exit_3m": "3 months",
              "stopped_trading": "stopped trading"}


def build_matrix(run_dirs, variant, out_dir, log=print):
    """Signal matrix: one row per (date, stock) on which any strategy said something, one column per strategy.

    BUY cell  = the strategy's buy signal at that day's close: "BUY <buy price> SL <stop>", plus what happened
                to it when it does not become a position: (not filled) the next day's order did not fill;
                (already held) the strategy already owned the stock; (regime blocked) v3.11's regime filter
                stopped it.
    SELL cell = the day the position is sold (the execution day), with price, result and why. Exits follow
                the chosen backtest variant (default rules_stop = v3.11's own exit rules + its stop, i.e. how
                v3.11 trades). Positions still open at the end of the data have no SELL.
    A blank cell = that strategy said nothing about that stock that day.
    Several runs can be combined (e.g. the 10 originals from one run and new strategies from another); if a
    strategy is in more than one, the first run listed wins."""
    sig_l, tr_l, seen = [], [], set()
    for rd in run_dirs:
        rd = Path(rd)
        s = pl.read_parquet(rd / "signals.parquet").select("strategy", "company_id", "signal_date", "buy_price", "stop",
                                                           "regime", "regime_allowed")
        names = [x for x in s["strategy"].unique().to_list() if x not in seen]
        seen.update(names)
        sig_l.append(s.filter(pl.col("strategy").is_in(names)))
        tr_l.append(pl.read_parquet(rd / "trades.parquet").filter(pl.col("variant") == variant, pl.col("strategy").is_in(names))
                      .select("strategy", "company_id", "signal_date", "filled", "fresh", "exit_date", "exit_price",
                              "exit_reason", "return_pct"))
    sig = pl.concat(sig_l, how="vertical_relaxed").with_columns(pl.col("signal_date").cast(pl.Datetime("ns")))
    tr = pl.concat(tr_l, how="vertical_relaxed").with_columns(pl.col("signal_date").cast(pl.Datetime("ns")),
                                                             pl.col("exit_date").cast(pl.Datetime("ns")))
    j = sig.join(tr, on=["strategy", "company_id", "signal_date"], how="left")
    note = (pl.when(~pl.col("regime_allowed")).then(pl.lit(" (regime blocked)"))
              .when(pl.col("filled") & ~pl.col("fresh")).then(pl.lit(" (already held)"))
              .when(~pl.col("filled").fill_null(False)).then(pl.lit(" (not filled)"))
              .otherwise(pl.lit("")))
    buys = j.select(pl.col("signal_date").alias("date"), "company_id", "strategy",
                    pl.format("BUY {} SL {}{}", pl.col("buy_price").round(2), pl.col("stop").round(2), note).alias("cell"))
    pos = j.filter(pl.col("filled").fill_null(False), pl.col("fresh").fill_null(False), pl.col("regime_allowed"),
                   pl.col("exit_reason").is_in(list(EXIT_WORDS)))
    sells = pos.select(pl.col("exit_date").alias("date"), "company_id", "strategy",
                       pl.format("SELL {} ({}%, {})", pl.col("exit_price").round(2),
                                 pl.col("return_pct").round(1), pl.col("exit_reason").replace(EXIT_WORDS)).alias("cell"))
    ev = (pl.concat([sells, buys]).group_by("date", "company_id", "strategy")
            .agg(pl.col("cell").str.join("; ")))                 # a sell and a new buy on the same day
    order = list(BS.STRATEGIES)
    cols = [c for c in order if c in seen] + sorted(c for c in seen if c not in order)
    m = ev.pivot(on="strategy", index=["date", "company_id"], values="cell")
    m = m.with_columns([pl.lit(None, pl.String).alias(c) for c in cols if c not in m.columns])
    m = (m.select(pl.col("date").dt.date(), pl.col("company_id").alias("script"), *cols)
          .with_columns(n_buy=pl.sum_horizontal([pl.col(c).str.contains("BUY").fill_null(False).cast(pl.Int32) for c in cols]),
                        n_sell=pl.sum_horizontal([pl.col(c).str.contains("SELL").fill_null(False).cast(pl.Int32) for c in cols]))
          .sort("date", "script"))
    reg = sig.select(pl.col("signal_date").dt.date().alias("date"), "regime").unique("date")
    m = m.join(reg, on="date", how="left").select("date", "script", "regime", "n_buy", "n_sell", *cols)
    out_dir = Path(out_dir)
    m.write_parquet(out_dir / "signal_matrix.parquet")
    m.write_csv(out_dir / "signal_matrix.csv")
    try:                                         # Excel copy: frozen header, filters, BUY green / SELL red
        import xlsxwriter  # noqa: F401
        last = chr(ord("A") + m.width - 1)
        fmt = {"type": "text", "criteria": "begins with"}
        m.write_excel(out_dir / "signal_matrix.xlsx", worksheet="signals", autofilter=True, freeze_panes=(1, 2),
                      column_widths={c: 30 for c in cols} | {"date": 11, "script": 14, "regime": 10, "n_buy": 6, "n_sell": 6},
                      conditional_formats={tuple(cols): [{**fmt, "value": "BUY", "format": {"font_color": "#006100", "bg_color": "#E2F0D9"}},
                                                         {**fmt, "value": "SELL", "format": {"font_color": "#9C0006", "bg_color": "#FBE2E2"}}]})
    except ImportError:
        pass
    except Exception as e:
        log(f"signal_matrix.xlsx not written: {e!r}")
    log(f"signal matrix: {m.height:,} rows (date x script), {len(cols)} strategy columns, exits = {variant} "
        f"-> {out_dir / 'signal_matrix.csv'}")
    return m


def cmd_matrix(args):
    runs = [_run_dir(x.strip()) for x in args.run.split(",") if x.strip()]
    for r in runs:
        if not (r / "signals.parquet").exists():
            sys.exit(f"{r} has no signals.parquet")
    out = Path(args.out_dir) if args.out_dir else runs[-1]
    out.mkdir(parents=True, exist_ok=True)
    build_matrix(runs, args.variant, out)


def cmd_results(args):
    end = dt.date.today()
    start = _add_months(end, -12 * args.years - 18)
    _results(args, start, end, print)


def cmd_list(args):
    for k, (fn, style) in BS.STRATEGIES.items():
        print(f"  {k:16s} {style:20s} {(fn.__doc__ or '').strip().splitlines()[0]}")
    for k, why in BS.EXCLUDED.items():
        print(f"  {k:16s} NOT BACKTESTED: {why}")


def cmd_run(args):
    t0 = time.time()
    E = BS.load_engine(args.engine_dir)
    raw_p, mk_p = _data_paths(args)
    names = list(BS.STRATEGIES) if args.strategies in (None, "all") else [x.strip() for x in args.strategies.split(",")]
    bad = [x for x in names if x not in BS.STRATEGIES]
    if bad:
        sys.exit(f"unknown / unsupported strategies: {bad}  (run: python nse_backtest.py list)")
    raw = pl.scan_parquet(raw_p)
    last = raw.select(pl.col("datetime").max()).collect().item()
    end = dt.datetime.fromisoformat(args.end) if args.end else last
    start = dt.datetime.fromisoformat(args.start) if args.start else dt.datetime.combine(_add_months(end.date(), -12 * args.years), dt.time())
    run_name = args.name or f"v311_{dt.datetime.now():%Y%m%d_%H%M}"
    out_dir = Path(args.out_dir) if args.out_dir else C.location("backtest_results") / run_name
    out_dir.mkdir(parents=True, exist_ok=True)
    workers = args.workers or os.cpu_count() or 1

    def log(m):
        line = f"{dt.datetime.now():%H:%M:%S}  {m}"
        print(line, flush=True)
        with open(out_dir / "log.txt", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    log(f"v3.11 source: {E.src}")
    log(f"data: {raw_p}; test window {start:%Y-%m-%d} .. {end:%Y-%m-%d}; strategies: {', '.join(names)}")
    notes = [
        "Each signal is simulated on its own: no position limits, no capital or sizing, no portfolio interaction.",
        "Universes approximated daily by 20-day turnover rank: nifty500 = top 500, nifty100 = top 100, nifty50 = top 50, "
        "fno = top 200 (historical index membership is not available).",
        "Regime: v3.11's own HMM + 200-day engine, run on Nifty 50 and India VIX (v3.11 live uses Nifty 500).",
        "Indicators come from v3.11 code but run on full history instead of its tail windows (EWM values differ in far decimals).",
        "Buy price = v3.11's entry reference (signal-day close; PEAD: its buy-stop trigger). v3.11 live buys at 15:10 on the "
        "signal day; here the order is placed the next morning as specified for this backtest.",
        "Targets (target_* variants) are not part of v3.11: target = buy + payoff x (buy - stop), payoff = the strategy's prior.",
        "forced_flow not backtested: " + BS.EXCLUDED["forced_flow"] + ".",
        "pullback's self-retirement rule and momentum's crowding / vol-target sizing are portfolio-level and not modelled.",
    ]

    # ---- load prices ---------------------------------------------------------------------------
    load_from = start - dt.timedelta(days=int(BS.LOOKBACK_SESSIONS * 1.5) + 30)
    df = (raw.filter(pl.col("datetime") >= load_from)
             .select("company_id", "datetime", "open", "high", "low", "close", "volume").collect())
    mk = pl.read_parquet(mk_p).select("datetime", "nifty_close", "vix").to_pandas().set_index("datetime")
    pdf = df.to_pandas()
    piv = {f: pdf.pivot(index="datetime", columns="company_id", values=f).sort_index() for f in
           ("open", "high", "low", "close", "volume")}
    # keep stocks that were ever liquid enough for the widest universe (top 500 by turnover)
    C, V = piv["close"], piv["volume"]
    rk = (C * V).rolling(20, min_periods=20).mean().where(C.notna()).rank(axis=1, ascending=False)
    keep = rk.le(500).any()
    for f in piv:
        piv[f] = piv[f].loc[:, keep]
    log(f"loaded {len(df):,} rows from {load_from:%Y-%m-%d}; {int(keep.sum())} stocks ever in the top 500 by turnover")

    # ---- results calendar -----------------------------------------------------------------------
    res = _results(args, load_from.date(), end.date(), log)
    if res is None:
        notes.append("NSE results calendar unavailable: PEAD produced no signals and results blackouts were not applied.")
    else:
        notes.append("Results dates from NSE board-meeting announcements; their time of day is unknown, so the meeting day "
                     "and the next session both count as possible day-0 (PEAD) and for blackouts.")
    P = BS.Panels(piv["open"], piv["high"], piv["low"], piv["close"], piv["volume"], mk["nifty_close"], mk["vix"],
                  BS.day0_map(res, piv["close"].index))

    # ---- exact F&O universe (optional) ------------------------------------------------------------
    if args.fno_universe == "exact":
        P.fno_exact = _fno_mask(args, P, start, log)
        notes[1] = ("Universes: fno = NSE's actual F&O stock list of each day (fno_universe.parquet); nifty500 / "
                    "nifty100 / nifty50 approximated by 20-day turnover rank (top 500 / 100 / 50).")

    # ---- regime ---------------------------------------------------------------------------------
    days = P.dates[(P.dates >= start) & (P.dates <= end)]
    full_mk = mk[mk.index <= end]
    reg = _regime(args, E, full_mk, days, workers, out_dir, log)

    # ---- signals --------------------------------------------------------------------------------
    all_sig, flags = [], {}
    for name in names:
        t1 = time.time()
        fn, style = BS.STRATEGIES[name]
        sig, flag = fn(E, P)
        sig = sig[(sig["signal_date"] >= start) & (sig["signal_date"] <= end)].copy()
        sig["regime"] = sig["signal_date"].map(reg).fillna("unknown")
        sig["regime_allowed"] = np.array([BS.regime_allowed(E, s, style) if s != "unknown" else False
                                          for s in sig["regime"]], dtype=bool)
        log(f"  {name:16s} {len(sig):7,} signals ({int(sig['regime_allowed'].sum()):,} regime-allowed)  {time.time() - t1:.0f}s")
        all_sig.append(sig)
        flags[name] = flag
    all_sig = [x for x in all_sig if len(x)]
    if not all_sig:
        sys.exit("no signals in the test window")
    signals = pd.concat(all_sig, ignore_index=True)
    signals.to_parquet(out_dir / "signals.parquet", index=False)

    # ---- simulate -------------------------------------------------------------------------------
    t1 = time.time()
    data_end = np.datetime64(P.dates.max(), "ns")
    tasks = []
    for cid, g in signals.groupby("company_id"):
        cl = P.C[cid]
        ok = cl.notna().to_numpy()
        dts = P.dates.to_numpy()[ok]
        fl = {s: flags[s][cid].to_numpy()[ok].astype(bool) for s in g["strategy"].unique() if cid in flags[s].columns}
        recs = g.to_dict("records")
        tasks.append((cid, dts, P.O[cid].to_numpy()[ok], P.H[cid].to_numpy()[ok], P.L[cid].to_numpy()[ok],
                      cl.to_numpy()[ok], recs, fl, args.hold_months, args.cost_bps / 100, data_end))
    rows = []
    if workers <= 1 or len(tasks) < 2:
        for tk in tasks:
            rows += simulate_company(tk)
    else:
        with ProcessPoolExecutor(max_workers=min(workers, 61)) as ex:
            for r in ex.map(simulate_company, tasks, chunksize=max(1, len(tasks) // (workers * 6) or 1)):
                rows += r
    trades = pl.from_pandas(pd.DataFrame(rows))          # pandas turns numpy dates / None into proper types
    log(f"simulated {len(signals):,} signals x {len(VARIANTS)} variants in {time.time() - t1:.0f}s")
    trades = mark_fresh(trades.with_columns(year=pl.col("signal_date").dt.year(),
                                            regime_allowed=pl.col("regime_allowed").cast(pl.Boolean)))
    trades.write_parquet(out_dir / "trades.parquet")
    trades.write_csv(out_dir / "trades.csv")
    tf = _with_filter(trades)
    summ = summarise(tf, ["signal_set", "strategy", "variant"])
    by_year = summarise(tf, ["signal_set", "strategy", "variant", "year"])
    summ.write_csv(out_dir / "summary.csv")
    by_year.write_csv(out_dir / "summary_by_year.csv")
    cfg = dict(run_name=run_name, data=str(raw_p), fno_universe=args.fno_universe, engine=E.src, start=f"{start:%Y-%m-%d}", end=f"{end:%Y-%m-%d}",
               strategies=names, hold_months=args.hold_months, cost_bps=args.cost_bps,
               created=dt.datetime.now().isoformat(timespec="seconds"))
    (out_dir / "run_config.json").write_text(json.dumps(cfg, indent=2))
    write_report(out_dir, cfg, summ, notes)
    try:
        build_matrix([out_dir], "rules_stop", out_dir, log)
    except Exception as e:                      # the matrix is a convenience - never fail the run for it
        log(f"signal matrix not written: {e!r}")
    log(f"done in {time.time() - t0:.0f}s -> {out_dir}")
    print((out_dir / "report.txt").read_text(encoding="utf-8"))


def main():
    ap = HelpfulParser(description=__doc__, epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    fmt = dict(epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    p = sub.add_parser("run", help="run the backtest", **fmt)
    p.add_argument("--source", choices=["updater", "fresh"], default="updater",
                   help="updater = %%NSEDATAENGINE_HOME%%\\data (history from 1991); fresh = data_fresh (5-year NSE build)")
    p.add_argument("--data-dir", help="data folder with raw_ohlcv.parquet + market.parquet (overrides --source), "
                                      "e.g. ${NSE_ROOT}/code/NSEDATAENGINE\\data_fresh_2019")
    p.add_argument("--fno-universe", choices=["proxy", "exact"], default="proxy",
                   help="proxy = top 200 by turnover (default, as before); exact = NSE's daily F&O list")
    p.add_argument("--fno-file", help="F&O list (default: %%NSEDATAENGINE_HOME%%\\data_ref\\fno_universe.parquet)")
    p.add_argument("--strategies", default="all", help="comma-separated names, or 'all'")
    p.add_argument("--years", type=int, default=5, help="test window length ending at --end (default 5)")
    p.add_argument("--start", help="YYYY-MM-DD, overrides --years")
    p.add_argument("--end", help="YYYY-MM-DD (default: last date in the data)")
    p.add_argument("--hold-months", type=int, default=3, help="max holding period (default 3)")
    p.add_argument("--cost-bps", type=float, default=0.0, help="round-trip costs in basis points (default 0)")
    p.add_argument("--workers", type=int, default=0, help="processes (default: all cores)")
    p.add_argument("--engine-dir", help="v3.11 project folder (default: %%NSEALGO_SRC%% or ${NSE_ROOT}/code/NSEALGOENGINE)")
    p.add_argument("--offline", action="store_true", help="use only cached NSE results files")
    p.add_argument("--name", help="run name (default: v311_<date_time>)")
    p.add_argument("--out-dir", help="output folder (default: %%NSEDATAENGINE_HOME%%\\backtest_results\\<name>)")
    p.add_argument("--regime-from", help="reuse the regime of an earlier run on the same data (saves the slowest step)")
    p = sub.add_parser("matrix", help="signal matrix (date x script, one column per strategy) of finished run(s)", **fmt)
    p.add_argument("--run", required=True, help="run name(s) in backtest_results, comma-separated (combined)")
    p.add_argument("--variant", default="rules_stop", choices=VARIANTS,
                   help="which exits give the SELL dates (default rules_stop = how v3.11 trades)")
    p.add_argument("--out-dir", help="output folder (default: the last run's folder)")
    p = sub.add_parser("results", help="download / refresh NSE's results calendar only", **fmt)
    p.add_argument("--years", type=int, default=5)
    sub.add_parser("list", help="list strategies")
    args = parse_args_or_help(ap)
    {"run": cmd_run, "results": cmd_results, "list": cmd_list, "matrix": cmd_matrix}[args.cmd](args)


if __name__ == "__main__":
    main()
