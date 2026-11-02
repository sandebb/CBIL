"""Research sleeves (resid_reversion, skew_premium), the fragility overlay, the variance-ratio gates and the
research tools on a synthetic market; and parallel == continuous with the new sleeves enabled."""
from pathlib import Path

from nsealgo.backtest.runner import run_backtest
from nsealgo.core.storage import Storage
from nsealgo.research import fractal, lead_lag

COLS = ["strategy", "key", "entry_ts", "exit_ts", "qty", "entry_price", "exit_price", "pnl_net"]
SLEEVES = ["resid_reversion", "skew_premium"]


def _cfg(synth, **fragility):
    c = dict(synth["cfg"])
    # the synthetic master lists futures on 2 names only, so point the long-only residual model at all stocks
    c["strategies"] = {**(c.get("strategies") or {}), "resid_reversion": {"universe": "nifty500"}}
    c["fragility"] = {**(c.get("fragility") or {}), **fragility}
    return c


def _db(summary):
    st = Storage(Path(summary["run_dir"]) / "state.db")
    try:
        return st.trades_df(), st.orders_df()
    finally:
        st.close()


def test_research_sleeves_trade_cleanly_under_the_fragility_gate(synth, tmp_path):
    d = synth["info"]["days"]
    s = run_backtest(_cfg(synth, action="halve"), start=d[-140], end=d[-1], granularity="daily",
                     history=synth["info"]["history"], master=synth["master"], names=SLEEVES, mode="faithful",
                     run_dir=tmp_path / "faithful")
    assert s["errors"] == 0
    trades, orders = _db(s)
    assert (trades["strategy"] == "skew_premium").sum() > 0
    assert set(orders["strategy"]) <= set(SLEEVES)
    entries = orders[orders["intent"] == "entry"]
    assert (entries["side"] == "BUY").all() and (entries["product"] == "CNC").all()      # long-only by default


def test_parallel_equals_continuous_with_research_sleeves(synth, tmp_path):
    d = synth["info"]["days"]
    cfg = _cfg(synth)
    cfg["parallel"] = {**(cfg.get("parallel") or {}), "min_block_daily": 40, "warmup_sessions": 80, "runoff_sessions": 60}
    kw = dict(start=d[-140], end=d[-1], granularity="daily", history=synth["info"]["history"], master=synth["master"],
              names=SLEEVES, mode="research")
    a = run_backtest(cfg, workers=1, run_dir=tmp_path / "seq", **kw)
    b = run_backtest(cfg, workers=3, run_dir=tmp_path / "par", **kw)
    ta, tb = (_db(x)[0] for x in (a, b))
    ta, tb = (t[COLS].sort_values(COLS[:4]).reset_index(drop=True).round(6) for t in (ta, tb))
    assert b["perf"]["jobs"] > len(SLEEVES) and len(ta) > 0 and ta.equals(tb)


def test_variance_ratio_gates_run_in_the_intraday_engine(synth, tmp_path):
    d = synth["info"]["days"]
    c = dict(synth["cfg"])
    st = dict(c.get("strategies") or {})
    st["orb"] = {**st.get("orb", {}), "trend_filter": True}
    st["vwap_fade"] = {**st.get("vwap_fade", {}), "reversion_filter": True}
    c["strategies"] = st
    s = run_backtest(c, start=d[-2], end=d[-1], granularity="minute", history=synth["info"]["history"],
                     master=synth["master"], names=["orb", "vwap_fade"], mode="faithful", run_dir=tmp_path / "vr")
    assert s["errors"] == 0


def test_leadlag_and_fractal_tools_on_minute_bars(synth):
    h = synth["info"]["history"]
    keys = sorted(h.keys("minute"))[:2]
    dates = sorted(h.minute_dates(keys[0]))
    x, y = (h.minute(k, dates[0], dates[-1]) for k in keys)
    r = lead_lag(x, y, lags=(1, 2), n_shuffle=50)
    assert len(r["rows"]) == 2 and all(0 < row["p_xy"] <= 1 and 0 < row["p_yx"] <= 1 for row in r["rows"])
    f = fractal(x)
    assert set(f["variance_ratio"]) == {10, 30, 60} and f["sessions"] >= 10
