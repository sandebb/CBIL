"""Parallel backtests on a synthetic market: bulk/threaded reads, and parallel == continuous results."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nsealgo.core.storage import Storage
from nsealgo.parallel import thread_map


def test_bulk_and_threaded_history_reads(synth):
    h = synth["info"]["history"]
    keys = sorted(h.keys("daily"))[:6]
    many = h.daily_many(keys)
    for k in keys:
        pd.testing.assert_frame_equal(many[k], h.daily(k), check_freq=False)
    panel = h.daily_panel(keys, "close")
    assert np.allclose(panel[keys[0]].dropna().to_numpy(), h.daily(keys[0])["close"].to_numpy())
    mk = sorted(h.keys("minute"))[:4]
    d = max(h.minute_dates(mk[0]))
    mm = h.minute_many(mk, d, d)
    for k in mk:
        pd.testing.assert_frame_equal(mm[k], h.minute(k, d, d), check_freq=False)
    assert thread_map(lambda k: len(h.daily(k)), keys, 4) == [len(h.daily(k)) for k in keys]


def _trades(summary):
    st = Storage(Path(summary["run_dir"]) / "state.db")
    t = st.trades_df()
    st.close()
    cols = ["strategy", "key", "entry_ts", "exit_ts", "qty", "entry_price", "exit_price", "pnl_net"]
    return t[cols].sort_values(cols[:4]).reset_index(drop=True).round(6)


@pytest.mark.parametrize("granularity", ["minute", "daily"])
def test_parallel_research_backtest_equals_continuous_run(synth, tmp_path, granularity):
    from nsealgo.backtest.runner import run_backtest
    cfg = {**synth["cfg"], "parallel": {**synth["cfg"].get("parallel", {}), "min_block_daily": 40,
                                        "warmup_sessions": 80, "runoff_sessions": 60}}
    d = synth["info"]["days"]
    if granularity == "minute":
        kw = dict(start=d[-4], end=d[-1], names=["orb", "vwap_fade"])
    else:
        kw = dict(start=d[-140], end=d[-1], names=None)
    common = dict(granularity=granularity, history=synth["info"]["history"], master=synth["master"], mode="research")
    seq = run_backtest(cfg, workers=1, run_dir=tmp_path / "seq", **common, **kw)
    par = run_backtest(cfg, workers=3, run_dir=tmp_path / "par", **common, **kw)
    a, b = _trades(seq), _trades(par)
    assert par["perf"]["jobs"] > len(set(a["strategy"])) or granularity == "minute"
    assert len(a) > 0 and a.equals(b)
    assert seq["overall"]["final_equity"] == par["overall"]["final_equity"]
