"""
bt_strategies.py - Nsealgo v3.11 swing strategies, ported for a whole-universe daily backtest.

Fidelity
  The indicator, statistics and regime code is IMPORTED from the v3.11 source (${NSE_ROOT}/code/NSEALGOENGINE\\src, or
  --engine-dir / %NSEALGO_SRC%): atr / atr_panel / ema / rsi / supertrend / momentum maths, the Marchenko-Pastur
  eigen-factor residuals and the AR(1)/OU fit of resid_reversion, the HMM + 200-day regime engine, the
  round-number stop adjustment and the strategy parameters (default_params / prior). Only the strategies'
  decision rules (which in v3.11 live inside on_timer callbacks that need the live engine) are re-expressed
  here as vectorised panel operations, line by line from the source. Each function below names the v3.11
  file it follows.

What a strategy produces
  signals     one row per (stock, day) where v3.11 would place a BUY at 15:10 (or, for PEAD, arm a buy-stop):
                buy_price  v3.11's entry reference: the signal-day close (PEAD: the buy-stop trigger)
                stop       v3.11's protective stop
                target     buy + payoff x (buy - stop); payoff = the strategy's own `prior` win/loss ratio
                max_hold   v3.11's time stop in sessions (the backtest also caps every trade at 3 months)
  exit flags  a dates x stocks boolean panel: True where v3.11's exit rule fires at that day's close
              (close below the 20-EMA, Supertrend flipped down, rank fell out at a rebalance, ...)

Deliberate differences from live v3.11 (all reported in the backtest's report.txt)
  * Every signal is tested independently: no max_positions / max_new_per_day / capital limits, so the
    test answers "is each signal good", not "how would the full portfolio have done".
  * Universes (Nifty 500 / 100 / 50 / F&O) are approximated each day by 20-day turnover rank (top 500 /
    100 / 50 / 200), because historical index membership files are not available.
  * The regime model runs on Nifty 50 (v3.11 uses Nifty 500) - it is the index the dataset carries.
  * Indicators are computed on the full history rather than on v3.11's tail windows (EWM-based values
    such as ATR differ in the far decimals only).
  * forced_flow is not backtested: there is no history of index-change announcements or closing-auction
    imbalance data.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import datetime as dt
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

TICK = 0.05                   # NSE equity tick size used for v3.11's tick-based offsets


# =====================================================================================================
# LOADING THE v3.11 ENGINE CODE
# =====================================================================================================
def load_engine(engine_dir: str | None):
    """Put v3.11's src folder on the import path and import the pieces we reuse. Returns a namespace."""
    # %NSEALGO_SRC% if set, else `locations: algo_engine` in contract\\schema.yaml (default <root>\\..\\NSEALGOENGINE)
    try:
        import nse_contract as C
        default = str(C.location("algo_engine"))
    except Exception:
        default = None
    cands = [engine_dir, os.environ.get("NSEALGO_SRC"), default]
    for c in cands:
        if not c:
            continue
        p = Path(c)
        src = p / "src" if (p / "src" / "nsealgo").is_dir() else p
        if (src / "nsealgo").is_dir():
            sys.path.insert(0, str(src))
            break
    else:
        sys.exit("Nsealgo v3.11 source not found. Pass --engine-dir ${NSE_ROOT}/code/NSEALGOENGINE or set NSEALGO_SRC.")
    import importlib
    ns = type("V311", (), {})()
    ns.ind = importlib.import_module("nsealgo.core.indicators")
    ns.stats = importlib.import_module("nsealgo.core.stats")
    ns.regime = importlib.import_module("nsealgo.core.regime")
    ns.crowding = importlib.import_module("nsealgo.core.crowding")
    ns.cfg_defaults = importlib.import_module("nsealgo.config").DEFAULTS
    st = importlib.import_module("nsealgo.strategies")
    ns.classes = st.REGISTRY
    ns.src = str(src)
    return ns


def params(E, name):
    """v3.11 default parameters merged with the overrides in its config/system.yaml (strategies: section)."""
    cls = E.classes[name]
    p = dict(cls.default_params)
    try:
        import yaml
        cfg = yaml.safe_load(open(Path(E.src).parent / "config" / "system.yaml", encoding="utf-8")) or {}
        over = dict(((cfg.get("strategies") or {}).get(name)) or {})
        over.pop("enabled", None)
        p.update(over)
    except Exception:
        pass
    return p, cls


# =====================================================================================================
# SHARED PANEL HELPERS
# =====================================================================================================
class Panels:
    """Wide (dates x stocks) price panels plus the market series, and the derived things every strategy
    needs: turnover ranks (universe proxies), trading-session positions, results-day masks."""

    def __init__(self, O, H, L, C, V, nifty, vix, results_day0=None):
        self.O, self.H, self.L, self.C, self.V = O, H, L, C, V
        self.dates = C.index
        self.nifty, self.vix = nifty.reindex(self.dates), (vix.reindex(self.dates) if vix is not None else None)
        self.turnover20 = (C * V).rolling(20, min_periods=20).mean()
        # rank 1 = most traded; only stocks trading that day are ranked
        self.rank = self.turnover20.where(C.notna()).rank(axis=1, ascending=False, method="first")
        self.rows = C.notna().cumsum()                 # rows of history each stock has up to each day
        D0 = pd.DataFrame(False, index=self.dates, columns=C.columns)
        if results_day0 is not None:
            for d, syms in results_day0.items():
                if d in D0.index:
                    cols = [s for s in syms if s in D0.columns]
                    if cols:
                        D0.loc[d, cols] = True
        self.day0 = D0
        r = D0.astype(float)
        self._had3 = r.rolling(4, min_periods=1).max() > 0              # day0 within the last 0..3 sessions
        self._next3 = r[::-1].rolling(3, min_periods=1).max()[::-1].shift(-1).fillna(0) > 0   # within next 1..3

    fno_exact = None     # dates x stocks bool frame from fno_universe.parquet (nse_backtest --fno-universe exact)

    def universe(self, name):
        if name == "fno" and self.fno_exact is not None:
            return self.fno_exact                      # NSE's real F&O list of each day
        n = {"nifty500": 500, "nifty100": 100, "nifty50": 50, "fno": 200}[name]
        return self.rank <= n

    def blackout(self, had=True, nxt=True):
        out = pd.DataFrame(False, index=self.dates, columns=self.C.columns)
        if had:
            out |= self._had3
        if nxt:
            out |= self._next3
        return out

    def first_session_of_month(self):
        d = pd.Series(self.dates, index=self.dates)
        return (d.dt.month != d.shift(1).dt.month).astype(bool)

    def first_session_of_week(self):
        d = pd.Series(self.dates, index=self.dates)
        wk = d.dt.isocalendar()
        key = (wk["year"].astype("int64") * 100 + wk["week"].astype("int64"))
        return (key != key.shift(1)).fillna(True).astype(bool)


def _melt(mask: pd.DataFrame, **cols) -> pd.DataFrame:
    """Rows of a boolean dates x stocks mask -> long frame with the requested panels' values attached."""
    idx = np.argwhere(mask.to_numpy(dtype=bool))
    if len(idx) == 0:
        return pd.DataFrame(columns=["company_id", "signal_date", *cols])
    out = pd.DataFrame({"company_id": mask.columns[idx[:, 1]], "signal_date": mask.index[idx[:, 0]]})
    for k, v in cols.items():
        out[k] = v.to_numpy()[idx[:, 0], idx[:, 1]] if isinstance(v, pd.DataFrame) else v
    return out


def _round_level(E, price, direction):
    """v3.11 crowding.avoid_round_level with its default levels (stops/entries moved off round numbers)."""
    return E.crowding.avoid_round_level(float(price), TICK, direction, (100, 50, 10), 3)


def _finish(E, name, sig, payoff, max_hold, extra=None):
    """Common columns for a strategy's signals."""
    if sig is None or len(sig) == 0:
        return pd.DataFrame(columns=["strategy", "company_id", "signal_date", "order_type", "buy_price", "limit_price",
                                     "arm_days", "stop", "target", "max_hold", "rule", "hold_param", "score"])
    sig = sig.copy()
    sig["strategy"] = name
    if "order_type" not in sig:
        sig["order_type"] = "limit"
    if "limit_price" not in sig:
        sig["limit_price"] = sig["buy_price"]
    if "arm_days" not in sig:
        sig["arm_days"] = 1
    sig["target"] = sig["buy_price"] + payoff * (sig["buy_price"] - sig["stop"])
    sig["max_hold"] = max_hold
    if "rule" not in sig:
        sig["rule"] = "flag"
    if "hold_param" not in sig:
        sig["hold_param"] = np.nan
    if "score" not in sig:
        sig["score"] = np.nan
    sig = sig[(sig["stop"] < sig["buy_price"]) & (sig["stop"] > 0) & np.isfinite(sig["buy_price"])]
    return sig


# =====================================================================================================
# THE STRATEGIES  (each returns (signals DataFrame, exit-flag panel))
# =====================================================================================================
def momentum(E, P: Panels):
    """strategies/momentum.py  MomentumLeaders._entries / _exits"""
    p, cls = params(E, "momentum")
    C, H, L, V = P.C, P.H, P.L, P.V
    U = P.universe(p["universe"]) & (C.rolling(253, min_periods=253).count() == 253)   # closes.notna().all() over 253
    # NSE-momentum-index composite, exactly indicators.momentum_scores but for every day at once
    r6, r12 = C / C.shift(126) - 1, C / C.shift(252) - 1
    vol = np.log(C).diff().rolling(252, min_periods=252).std() * math.sqrt(252)

    def z(x):
        x = x.where(U).replace([np.inf, -np.inf], np.nan)
        sd = x.std(axis=1, ddof=0).replace(0, 1.0)
        return x.sub(x.mean(axis=1), axis=0).div(sd, axis=0)
    score = (z(r6 / vol) + z(r12 / vol)) / 2
    n_ok = score.notna().sum(axis=1)
    top_n = np.ceil(n_ok * float(p["top_frac"])).clip(lower=1)
    top = score.rank(axis=1, ascending=False, method="first").le(top_n, axis=0) & score.notna()
    hist = lambda f: f.shift(1)
    atr_f = hist(E.ind.atr_panel(H, L, C, int(p["atr_fast"])))
    atr_s = hist(E.ind.atr_panel(H, L, C, int(p["atr_slow"])))
    n = int(p["breakout_days"])
    cond = (top & (P.rows >= 260)
            & (C >= (1 - p["near_high"]) * H.rolling(252, min_periods=1).max())
            & (C > C.rolling(50).mean()) & (C.rolling(50).mean() > C.rolling(200).mean())
            & (atr_f <= p["contraction"] * atr_s)
            & (C > H.shift(1).rolling(n, min_periods=n).max())
            & (V >= p["vol_mult"] * V.shift(1).rolling(50, min_periods=50).mean()))      # volume_fraction = 1 at the close
    sig = _melt(cond, buy_price=C, low10=L.rolling(10, min_periods=1).min(), score=score)
    sig["stop"] = [_round_level(E, x, -1) for x in sig["low10"]]
    sig = sig[(sig["stop"] > 0) & ((sig["buy_price"] - sig["stop"]) / sig["buy_price"] <= p["max_stop_pct"])]
    sig["rule"], sig["hold_param"] = "momentum", float(p["time_stop"])   # +1R-progress rule needs the fill price
    exit_flag = C < E.ind.ema(C, int(p["exit_ema"]))
    return _finish(E, "momentum", sig, cls.prior[1], 10 ** 6), exit_flag


def pead(E, P: Panels):
    """strategies/pead.py  EarningsGapDrift._scan / _place / exits"""
    p, cls = params(E, "pead")
    C, H, L, V = P.C, P.H, P.L, P.V
    if not P.day0.to_numpy().any():
        return _finish(E, "pead", None, cls.prior[1], 0), pd.DataFrame(False, index=C.index, columns=C.columns)
    prev = C.shift(1)
    a = E.ind.atr_panel(H, L, C, 14).shift(1)                       # atr(d.iloc[:-1], 14)
    rng = H - L
    cond = (P.day0 & P.universe(p["universe"]) & (P.rows >= 60)
            & ((C / prev - 1) >= p["atr_mult"] * a / prev)
            & (V >= p["vol_mult"] * V.shift(1).rolling(50, min_periods=1).mean())
            & (rng > 0) & ((C - L) / rng >= 1 - p["close_top"]))
    sig = _melt(cond, day_high=H, day_low=L)
    sig["buy_price"] = [_round_level(E, x + TICK, +1) for x in sig["day_high"]]               # buy-stop trigger
    sig["stop"] = [_round_level(E, x - int(p["stop_ticks"]) * TICK, -1) for x in sig["day_low"]]
    sig["order_type"] = "stop"
    sig["limit_price"] = sig["buy_price"] * (1 + p["entry_slip"])                             # stop-LIMIT
    sig["arm_days"] = int(p["trigger_sessions"])
    exit_flag = C < E.ind.ema(C, int(p["exit_ema"]))
    return _finish(E, "pead", sig, cls.prior[1], int(p["max_hold"])), exit_flag


def pullback(E, P: Panels):
    """strategies/pullback.py  PanicPullback.on_timer (vectorised screen is already in the source)"""
    p, cls = params(E, "pullback")
    C, H, L, V = P.C, P.H, P.L, P.V
    nd = int(p["drop_days"])
    sma200 = C.shift(1).rolling(200, min_periods=200).mean()          # C.iloc[-201:-1].mean(skipna=False)
    rsi2 = E.ind.rsi(C, 2)
    a = E.ind.atr_panel(H, L, C, 14).shift(1)                          # atr on H/L/C.iloc[:-1]
    drop = C.shift(nd) - C
    v20 = V.shift(1).rolling(20, min_periods=1).mean()
    spike = V.rolling(3, min_periods=1).max() / v20.where(v20 > 0)
    cond = (P.universe(p["universe"]) & (C.rolling(261, min_periods=1).count() >= 210) & (C > sma200)
            & (rsi2 < p["rsi_max"]) & (drop >= p["drop_atr"] * a) & ~(spike > p["news_rvol"])
            & ~P.blackout(had=True, nxt=False))
    sig = _melt(cond, buy_price=C, atr=a)
    sig["stop"] = sig["buy_price"] - p["disaster_atr"] * sig["atr"]
    exit_flag = C > C.rolling(int(p["exit_sma"]), min_periods=1).mean()
    return _finish(E, "pullback", sig, cls.prior[1], int(p["max_hold"])), exit_flag


def turtle(E, P: Panels):
    """strategies/timing.py  TurtleBreakout (entry / exit_reason) via templates.DailySignalSleeve"""
    p, cls = params(E, "turtle")
    C, H, L = P.C, P.H, P.L
    n, m = int(p["entry_n"]), int(p["exit_n"])
    N = E.ind.atr_panel(H, L, C, int(p["atr_n"])).shift(1)           # atr(d.iloc[:-1], atr_n)
    blk = P.blackout(had=False, nxt=True) if int(p["results_window"]) else False
    cond = (P.universe(p["universe"]) & (P.rows >= 60) & (C > H.shift(1).rolling(n, min_periods=n).max())
            & (N > 0) & ~blk)
    sig = _melt(cond, buy_price=C, N=N)
    sig["stop"] = sig["buy_price"] - float(p["stop_n"]) * sig["N"]
    if p.get("system1_filter"):
        sig["note"] = "system1_filter not applied"
    exit_flag = C < L.shift(1).rolling(m, min_periods=m).min()
    return _finish(E, "turtle", sig, cls.prior[1], int(p["max_hold"])), exit_flag


def supertrend(E, P: Panels):
    """strategies/timing.py  SupertrendTrend; the Supertrend line itself is indicators.supertrend"""
    p, cls = params(E, "supertrend")
    C = P.C
    line = pd.DataFrame(np.nan, index=C.index, columns=C.columns)
    dirn = line.copy()
    U = P.universe(p["universe"])
    for col in C.columns[U.any().to_numpy()]:                       # only stocks that were ever in the universe
        d = pd.DataFrame({"high": P.H[col], "low": P.L[col], "close": C[col]}).dropna()
        if len(d) < 30:
            continue
        ln, dr = E.ind.supertrend(d, int(p["period"]), float(p["mult"]))
        line.loc[d.index, col], dirn.loc[d.index, col] = ln.to_numpy(), dr.to_numpy()
    blk = P.blackout(had=False, nxt=True) if int(p["results_window"]) else False
    prev_dir = dirn.ffill().shift(1)
    cond = U & (P.rows >= 60) & (dirn > 0) & (prev_dir < 0) & ~blk
    sig = _melt(cond, buy_price=C, stop=line)
    exit_flag = dirn < 0
    return _finish(E, "supertrend", sig, cls.prior[1], int(p["max_hold"])), exit_flag


# ---- rebalance sleeves (strategies/templates.py RebalanceSleeve + factors.py) ---------------------------
def _sleeve(E, P: Panels, name, score_fn, lookback):
    p, cls = params(E, name)
    C, H, L, V = P.C, P.H, P.L, P.V
    reb = P.first_session_of_month() if p.get("rebalance", "monthly") == "monthly" else P.first_session_of_week()
    liquid = (P.universe(p["universe"]) & (C.rolling(lookback + 1, min_periods=lookback + 1).count() == lookback + 1)
              & (C >= float(p["min_price"])))
    if float(p["min_adv"]) > 0:
        liquid &= (C * V).rolling(20, min_periods=20).mean() >= float(p["min_adv"])
    a14 = E.ind.atr_panel(H, L, C, 14)                               # atr(d, 14) with today
    blk = P.blackout(had=True, nxt=True) if int(p["results_window"]) else pd.DataFrame(False, index=C.index, columns=C.columns)
    ranks = pd.DataFrame(np.nan, index=C.index, columns=C.columns)
    rows = []
    for t in np.flatnonzero(reb.to_numpy()):
        ok = liquid.iloc[t]
        if ok.sum() < 20:
            continue
        sc = score_fn(t, list(ok.index[ok.to_numpy()]))
        if sc is None or len(sc) == 0:
            continue
        rk = sc.rank(ascending=False)
        ranks.iloc[t] = rk.reindex(C.columns).to_numpy()
        for key in sc.sort_values(ascending=False).index[: int(p["top_n"])]:
            if blk.iloc[t][key] or P.rows.iloc[t][key] < 30:
                continue
            px, a = C.iloc[t][key], a14.iloc[t][key]
            if not (np.isfinite(a) and a > 0):
                continue
            rows.append({"company_id": key, "signal_date": C.index[t], "buy_price": px,
                         "stop": px - float(p["disaster_atr"]) * a, "score": float(sc[key])})
    sig = pd.DataFrame(rows)
    # exit flags: on a rebalance day a holding whose rank is beyond exit_rank (or no longer scored) is sold;
    # any day: results within the next results_window sessions
    rb = pd.DataFrame(np.repeat(reb.to_numpy()[:, None], C.shape[1], axis=1), index=C.index, columns=C.columns)
    scored_day = ranks.notna().any(axis=1)
    exit_flag = rb & scored_day.to_numpy()[:, None] & ~(ranks <= int(p["exit_rank"]))
    if int(p["results_window"]):
        exit_flag |= P.blackout(had=False, nxt=True)
    return _finish(E, name, sig, cls.prior[1], int(p["max_hold"])), exit_flag


def high52(E, P):
    """factors.py High52Momentum.scores: close / 52-week high"""
    lb = 252

    def sc(t, keys):
        C = P.C[keys].iloc[t - lb:t + 1]
        Hh = P.H[keys].iloc[t - lb:t + 1]
        return (C.iloc[-1] / Hh.max()).dropna()
    return _sleeve(E, P, "high52", sc, lb)


def low_vol(E, P):
    """factors.py LowVolatility.scores: minus the std of one year of daily log returns"""
    lb = 252

    def sc(t, keys):
        C = P.C[keys].iloc[t - lb:t + 1]
        return -np.log(C).diff().iloc[1:].std()
    return _sleeve(E, P, "low_vol", sc, lb)


def resid_momentum(E, P):
    """factors.py ResidualMomentum.scores: market-model residual 12-1 return / its volatility"""
    p, _ = params(E, "resid_momentum")
    lb = 378
    mk = np.log(P.nifty).diff()

    def sc(t, keys):
        C = P.C[keys].iloc[t - lb:t + 1]
        R = np.log(C).diff().iloc[1:]
        m = mk.reindex(R.index)
        if m.isna().any():
            return None
        X = np.column_stack([np.ones(len(m)), m.to_numpy()])
        B = np.linalg.lstsq(X, R.to_numpy(), rcond=None)[0]
        Ed = pd.DataFrame(R.to_numpy() - X @ B, index=R.index, columns=R.columns)
        Fm = Ed.iloc[-int(p["formation"]):-int(p["skip"])]
        sd = Fm.std().replace(0, np.nan)
        return (Fm.sum() / sd).dropna()
    return _sleeve(E, P, "resid_momentum", sc, lb)


def skew_premium(E, P: Panels):
    """skew_premium.py LotterySkewPremium: weekly, long the least lottery-like decile"""
    p, cls = params(E, "skew_premium")
    C, H, L, V = P.C, P.H, P.L, P.V
    lb = int(p["lookback"])
    reb = P.first_session_of_week()
    liquid = (P.universe(p["universe"]) & (C.rolling(lb + 1, min_periods=lb + 1).count() == lb + 1)
              & (C >= float(p["min_price"])) & ((C * V).rolling(20, min_periods=20).mean() >= float(p["min_adv"])))
    mk = np.log(P.nifty).diff()
    a14 = E.ind.atr_panel(H, L, C, 14)
    blk = P.blackout(had=True, nxt=True)
    scores = pd.DataFrame(np.nan, index=C.index, columns=C.columns)
    rows = []
    for t in np.flatnonzero(reb.to_numpy()):
        ok = liquid.iloc[t]
        keys = list(ok.index[ok.to_numpy()])
        if len(keys) < 20:
            continue
        Cw = C[keys].iloc[t - lb:t + 1]
        R = np.log(Cw).diff().iloc[1:]
        m = mk.reindex(R.index)
        Rc = R - R.mean()
        if m.notna().all() and float(m.var()) > 0:
            mc = m - m.mean()
            beta = Rc.mul(mc, axis=0).sum() / float((mc * mc).sum())
            resid = Rc - np.outer(mc.to_numpy(), beta.to_numpy())
        else:
            resid = Rc
        skew = resid.skew()
        mx = pd.Series(np.sort(Cw.pct_change().iloc[-int(p["max_window"]):].to_numpy(), axis=0)
                       [-int(p["max_days"]):].mean(axis=0), index=Cw.columns)
        score = ((skew.rank(pct=True) + mx.rank(pct=True)) / 2).rank(pct=True)
        scores.iloc[t] = score.reindex(C.columns).to_numpy()
        for key in score[score <= float(p["quantile"])].sort_values().index:
            if blk.iloc[t][key] or P.rows.iloc[t][key] < 30:
                continue
            px, a = C.iloc[t][key], a14.iloc[t][key]
            if np.isfinite(a) and a > 0:
                rows.append({"company_id": key, "signal_date": C.index[t], "buy_price": px,
                             "stop": px - float(p["disaster_atr"]) * a, "score": float(score[key])})
    sig = pd.DataFrame(rows)
    rb = pd.DataFrame(np.repeat(reb.to_numpy()[:, None], C.shape[1], axis=1), index=C.index, columns=C.columns)
    exit_flag = (rb & (scores > float(p["exit_quantile"]))) | P.blackout(had=False, nxt=True)
    return _finish(E, "skew_premium", sig, cls.prior[1], int(p["max_hold"])), exit_flag


def resid_reversion(E, P: Panels, workers=1):
    """resid_reversion.py ResidualReversion.scores / on_timer / _manage (Avellaneda-Lee s-score)"""
    p, cls = params(E, "resid_reversion")
    C, H, L, V = P.C, P.H, P.L, P.V
    fw, ow = int(p["factor_window"]), int(p["ou_window"])
    U = P.universe(p["universe"])
    a14 = E.ind.atr_panel(H, L, C, 14)
    blk = P.blackout(had=True, nxt=True)
    S = pd.DataFrame(np.nan, index=C.index, columns=C.columns)
    rows = []
    adv = (C * V).rolling(20, min_periods=1).mean()
    for t in range(fw - 1, len(C)):
        u = U.iloc[t].to_numpy()
        if not u.any():
            continue
        Cw = C.iloc[t - fw + 1:t + 1, u]                              # tail=fw: fw rows incl. today
        Cw = Cw.loc[:, Cw.notna().all() & (Cw.iloc[-1] >= float(p["min_price"]))]
        if Cw.shape[1] < 20:
            continue
        R = np.diff(np.log(Cw.to_numpy(dtype=float)), axis=0)
        mp = E.stats.mp_clean(R)
        k = int(min(max(mp["n_signal"], 1), int(p["max_factors"])))
        Ed, _ = E.stats.factor_residuals(R[-ow:], E.stats.eigen_factor_returns(R, R[-ow:], k))
        fit = E.stats.ar1_fit(np.cumsum(Ed, axis=0))
        with np.errstate(invalid="ignore", divide="ignore"):
            s = (0.0 - fit["mu"]) / fit["sigma_eq"]
        cols = list(Cw.columns)
        S.iloc[t, [C.columns.get_loc(c) for c in cols]] = s
        hl = fit["half_life"]
        okm = (hl >= float(p["min_half_life"])) & (hl <= float(p["max_half_life"])) & np.isfinite(s)
        if p["require_df"]:
            okm &= fit["stationary"]
        okm &= adv.iloc[t][cols].to_numpy() >= float(p["min_adv"])
        for j in np.flatnonzero(okm & (s <= -float(p["s_entry"]))):
            key = cols[j]
            if blk.iloc[t][key] or P.rows.iloc[t][key] < 30:
                continue
            px, a = C.iloc[t][key], a14.iloc[t][key]
            if np.isfinite(a) and a > 0:
                rows.append({"company_id": key, "signal_date": C.index[t], "buy_price": px,
                             "stop": px - float(p["disaster_atr"]) * a, "score": float(s[j]),
                             "rule": "resid", "hold_param": float(math.ceil(float(p["hold_half_lives"]) * hl[j]))})
    sig = pd.DataFrame(rows)
    # exit: residual reverted (s > -s_exit), model unavailable (no s that day), results ahead
    exit_flag = (S > -float(p["s_exit_long"])) | S.isna() | P.blackout(had=False, nxt=True)
    return _finish(E, "resid_reversion", sig, cls.prior[1], 10 ** 6), exit_flag


# -----------------------------------------------------------------------------------------------------
# VARIANTS (not in v3.11) - the tweaks that held up in the 2021-26 analysis, kept next to the originals
# -----------------------------------------------------------------------------------------------------
def _above_sma(P: Panels, sig, n=200):
    """Keep only signals whose stock closed above its n-day simple average on the signal day."""
    if sig is None or len(sig) == 0:
        return sig
    sma = P.C.rolling(n, min_periods=n).mean()
    above = (P.C > sma)
    ok = np.array([bool(above.at[d, k]) if k in above.columns else False
                   for d, k in zip(sig["signal_date"], sig["company_id"])], dtype=bool)
    return sig[ok]


def pead_200dma(E, P: Panels):
    """VARIANT: pead, but only when the stock is above its 200-day average on the signal day.
    In 2021-26 the PEAD signals below the 200-day average lost ~1.6% per trade against the market."""
    sig, flag = pead(E, P)
    sig = _above_sma(P, sig).copy()
    sig["strategy"] = "pead_200dma"
    return sig, flag


def resid_momentum_hold63(E, P: Panels):
    """VARIANT: resid_momentum entries, held a fixed 63 sessions (about 3 months) instead of being sold when the
    stock drops out of the top ranks at a monthly rebalance. Its signals kept earning for ~3 months."""
    sig, flag = resid_momentum(E, P)
    sig = sig.copy()
    sig["strategy"] = "resid_momentum_hold63"
    sig["max_hold"] = 63
    sig["rule"] = "flag"
    return sig, pd.DataFrame(False, index=P.C.index, columns=P.C.columns)


def resid_momentum_200dma_hold63(E, P: Panels):
    """VARIANT: resid_momentum_hold63 plus the 200-day-average filter."""
    sig, flag = resid_momentum_hold63(E, P)
    sig = _above_sma(P, sig).copy()
    sig["strategy"] = "resid_momentum_200dma_hold63"
    return sig, flag


STRATEGIES = {
    "momentum": (momentum, "liquidity_taking"),
    "pead": (pead, "liquidity_taking"),
    "pullback": (pullback, "liquidity_providing"),
    "turtle": (turtle, "liquidity_taking"),
    "supertrend": (supertrend, "liquidity_taking"),
    "high52": (high52, "liquidity_taking"),
    "low_vol": (low_vol, "liquidity_taking"),
    "resid_momentum": (resid_momentum, "liquidity_taking"),
    "resid_reversion": (resid_reversion, "liquidity_providing"),
    "skew_premium": (skew_premium, "liquidity_taking"),
    # variants (not part of v3.11)
    "pead_200dma": (pead_200dma, "liquidity_taking"),
    "resid_momentum_hold63": (resid_momentum_hold63, "liquidity_taking"),
    "resid_momentum_200dma_hold63": (resid_momentum_200dma_hold63, "liquidity_taking"),
}
EXCLUDED = {"forced_flow": "no history of index-change announcements or closing-auction imbalance data"}
LOOKBACK_SESSIONS = 420          # momentum needs ~420 sessions of history before its first signal; resid_momentum 379


# =====================================================================================================
# REGIME (v3.11 core/regime.py)
# =====================================================================================================
def regime_states(E, nifty: pd.Series, vix: pd.Series | None, days, workers: int, log) -> pd.Series:
    """v3.11's regime for each session (HMM on index return + VIX level, and the 200-day average, the more
    cautious of the two), computed from data up to the previous session, exactly as build_timeline does."""
    cfg = {**E.cfg_defaults["regime"]}
    t0 = time.time()
    tl = E.regime.build_timeline(cfg, nifty.dropna(), vix.dropna() if vix is not None else None,
                                 [d.date() for d in days], workers=workers)
    log(f"regime timeline: {len(tl)} sessions in {time.time() - t0:.0f}s")
    return pd.Series({pd.Timestamp(k): v["state"] for k, v in tl.items()})


def regime_allowed(E, state: str, style: str) -> bool:
    eng = E.regime.RegimeEngine({})
    eng.state = state
    return bool(eng.posture("swing", style)[0])


# =====================================================================================================
# RESULTS CALENDAR (for PEAD day-0 and the results blackouts)
# =====================================================================================================
URL_BM = ("https://www.nseindia.com/api/corporate-board-meetings?index=equities"
          "&from_date={a:%d-%m-%Y}&to_date={b:%d-%m-%Y}")


def fetch_results(cache_dir: Path, start: dt.date, end: dt.date, headers: dict, log, offline=False):
    """Board meetings whose purpose is financial results, from NSE, one quarter per request (cached).
    Returns a DataFrame [symbol, date] or None if nothing could be obtained."""
    import requests
    cache_dir.mkdir(parents=True, exist_ok=True)
    s = None
    rows, failed = [], 0
    a = dt.date(start.year, ((start.month - 1) // 3) * 3 + 1, 1)
    while a <= end:
        nxt = dt.date(a.year + (1 if a.month == 10 else 0), 1 if a.month == 10 else a.month + 3, 1)
        b = nxt - dt.timedelta(days=1)
        f = cache_dir / f"board_meetings_{a:%Y%m%d}.json"
        part = cache_dir / f"board_meetings_{a:%Y%m%d}_partial.json"
        data = f.read_bytes() if f.exists() else None
        if data is None and not offline:
            for attempt in range(4):
                try:
                    if s is None:
                        s = requests.Session()
                        s.headers.update(headers)
                        s.get("https://www.nseindia.com/", timeout=20)
                    r = s.get(URL_BM.format(a=a, b=b), timeout=60, headers={"Accept": "application/json"})
                    if r.status_code == 200 and r.content.lstrip()[:1] in (b"[", b"{"):
                        data = r.content
                        if b < dt.date.today() - dt.timedelta(days=7):
                            f.write_bytes(data)
                        else:                  # quarter still in progress: keep a dated copy for offline runs
                            part.write_bytes(data)
                        break
                except Exception as ex:
                    log(f"  board meetings {a}..{b}: {ex!r}")
                s = None
                time.sleep(3 * (attempt + 1))
            time.sleep(0.5)
        if data is None and part.exists():
            # offline, or NSE unreachable: the in-progress quarter as last downloaded (may miss the newest days)
            data = part.read_bytes()
            log(f"  board meetings {a}..{b}: using the copy saved "
                f"{dt.datetime.fromtimestamp(part.stat().st_mtime):%Y-%m-%d %H:%M}")
        items = None
        if data:
            try:
                j = json.loads(data)
                items = j.get("data", j) if isinstance(j, dict) else j
            except Exception:
                items = None
        if not isinstance(items, list):
            failed += 1
        else:
            for it in items:
                sym = str(it.get("bm_symbol") or it.get("symbol") or "").strip().upper()
                purpose = " ".join(str(it.get(k) or "") for k in ("bm_purpose", "purpose", "bm_desc", "desc")).lower()
                ds = str(it.get("bm_date") or it.get("date") or "").strip()
                d = None
                for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y"):
                    try:
                        d = dt.datetime.strptime(ds[:11].strip(), fmt).date()
                        break
                    except ValueError:
                        pass
                if sym and d and "result" in purpose:
                    rows.append((sym, d))
        a = nxt
    log(f"results calendar: {len(rows):,} results board meetings"
        + (f"; {failed} quarter(s) could not be downloaded" if failed else ""))
    if not rows:
        return None
    return pd.DataFrame(sorted(set(rows)), columns=["symbol", "date"])


def day0_map(results: pd.DataFrame | None, dates: pd.DatetimeIndex) -> dict:
    """v3.11 day-0 = first session able to react. NSE's board-meeting list gives the date but not the time
    of the announcement, so BOTH the meeting day (if it is a session) and the next session are treated as
    possible day-0s; PEAD's own gap/volume/close test then decides. Blackouts count both days."""
    out = {}
    if results is None:
        return out
    idx = dates
    for sym, d in results.itertuples(index=False):
        ts = pd.Timestamp(d)
        i = idx.searchsorted(ts)
        cands = []
        if i < len(idx) and idx[i] == ts:
            cands.append(idx[i])
            if i + 1 < len(idx):
                cands.append(idx[i + 1])
        elif i < len(idx):
            cands.append(idx[i])
        for c in cands:
            out.setdefault(c, set()).add(sym)
    return out
