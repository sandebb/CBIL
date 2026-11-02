"""The documented stock strategies run cleanly through the live engine on the synthetic market, and research
backtests split across processes reproduce continuous runs exactly."""
import copy
from pathlib import Path

from nsealgo.backtest.runner import run_backtest
from nsealgo.core.storage import Storage

DAILY = ["turtle", "supertrend", "high52", "low_vol", "resid_momentum"]
SYM = ["NSE:SYN02", "NSE:SYN05", "NSE:SYN07"]


def _cfg(synth):
    c = copy.deepcopy(synth["cfg"])
    st = c.setdefault("strategies", {})
    st["turtle"] = {"universe": "fno", "entry_n": 20, "exit_n": 10}
    st["supertrend"] = {"symbols": SYM, "universe": None}
    for n in ("high52", "low_vol", "resid_momentum"):
        st[n] = {"universe": "nifty500", "min_adv": 0, "top_n": 5, "exit_rank": 10}
    st["compression_breakout"] = {"setup": "narrow_cpr", "cpr_pct": 1.0, "min_adv": 0}
    st["gap_fade"] = {"symbols": SYM, "min_gap": 0.0, "max_gap": 0.05}
    c["parallel"] = {**c.get("parallel", {}), "min_block_daily": 40}
    return c


def _trades(s):
    st = Storage(Path(s["run_dir"]) / "state.db")
    t = st.trades_df()
    st.close()
    cols = ["strategy", "key", "entry_ts", "exit_ts", "qty", "entry_price", "exit_price", "pnl_net"]
    return t[cols].sort_values(cols[:4]).reset_index(drop=True).round(6) if len(t) else t


def test_daily_documented_strategies_trade_cleanly(synth, tmp_path):
    d = synth["info"]["days"]
    s = run_backtest(_cfg(synth), start=d[-150], end=d[-1], granularity="daily", names=DAILY, mode="faithful",
                     history=synth["info"]["history"], master=synth["master"], run_dir=tmp_path / "f")
    assert s["errors"] == 0
    traded = {n for n, m in s["strategies"].items() if m.get("trades")}
    assert len(traded & set(DAILY)) >= 4, traded


def test_parallel_equals_continuous_for_documented_sleeves(synth, tmp_path):
    d = synth["info"]["days"]
    kw = dict(start=d[-150], end=d[-1], granularity="daily", names=DAILY, mode="research",
              history=synth["info"]["history"], master=synth["master"])
    a = run_backtest(_cfg(synth), workers=1, run_dir=tmp_path / "seq", **kw)
    b = run_backtest(_cfg(synth), workers=3, run_dir=tmp_path / "par", **kw)
    A, B = _trades(a), _trades(b)
    assert len(A) > 0 and A.equals(B)
    assert a["overall"]["final_equity"] == b["overall"]["final_equity"]


def test_intraday_patterns_trade_cleanly(synth, tmp_path):
    d = synth["info"]["days"]
    s = run_backtest(_cfg(synth), start=d[-4], end=d[-1], granularity="minute", mode="faithful",
                     names=["compression_breakout", "gap_fade"], history=synth["info"]["history"],
                     master=synth["master"], run_dir=tmp_path / "m")
    assert s["errors"] == 0 and s["trades"] > 0
