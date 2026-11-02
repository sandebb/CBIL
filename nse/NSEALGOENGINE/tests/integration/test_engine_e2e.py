"""End-to-end: synthetic market -> engine -> strategies -> OMS -> paper exchange -> storage -> report."""
from pathlib import Path

from nsealgo.backtest.runner import run_backtest
from nsealgo.core.storage import Storage
from nsealgo.data.synthetic import write_recording_from_bars
from nsealgo.forwardtest.runner import run_replay


def test_daily_backtest_runs_clean(synth):
    d = synth["info"]["days"]
    s = run_backtest(synth["cfg"], start=d[-80], end=d[-1], granularity="daily", history=synth["info"]["history"], mode="faithful",
                     master=synth["master"], run_dir=Path(synth["root"]) / "runs" / "t_daily")
    assert s["errors"] == 0
    assert s["overall"]["sessions"] == 80
    st = Storage(Path(s["run_dir"]) / "state.db")
    orders = st.orders_df()
    st.close()
    assert len(orders) > 0
    assert set(orders["strategy"]) <= {"momentum", "pead", "pullback", "forced_flow"}
    assert (Path(s["run_dir"]) / "report.html").exists()


def test_minute_backtest_intraday_strategies(synth):
    d = synth["info"]["days"]
    s = run_backtest(synth["cfg"], start=d[-2], end=d[-1], granularity="minute", history=synth["info"]["history"], mode="faithful",
                     master=synth["master"], names=["orb", "vwap_fade"],
                     run_dir=Path(synth["root"]) / "runs" / "t_minute")
    assert s["errors"] == 0
    st = Storage(Path(s["run_dir"]) / "state.db")
    mis_left = st.kv_get("engine", "portfolio")["positions"]
    st.close()
    assert not [p for p in mis_left if p["product"] == "MIS"]      # intraday book is flat at the close


def test_quote_replay_forward_path(synth):
    d = synth["info"]["days"]
    keys = [f"NSE:SYN{i:02d}" for i in range(20)] + ["NSE:SYNETF"]
    rec = write_recording_from_bars(synth["info"]["history"], keys, d[-1], Path(synth["root"]) / "rec.jsonl.gz")
    s = run_replay(synth["cfg"], rec, ["orb", "vwap_fade"], synth["master"], synth["info"]["history"],
                   run_dir=Path(synth["root"]) / "runs" / "t_replay")
    assert s["errors"] == 0
