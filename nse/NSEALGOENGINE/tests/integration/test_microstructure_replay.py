"""Scalper and market maker on a recorded (synthetic) day of quotes through the real engine and OMS."""
import copy
from pathlib import Path

from nsealgo.core.storage import Storage
from nsealgo.data.synthetic import write_recording_from_bars
from nsealgo.forwardtest.runner import run_replay


def _run(synth, tmp_path, name, params, depth=0):
    c = copy.deepcopy(synth["cfg"])
    c.setdefault("strategies", {})[name] = params
    day = synth["info"]["days"][-1]
    rec = write_recording_from_bars(synth["info"]["history"], list(params["symbols"]), day,
                                    tmp_path / f"{name}_{day}.jsonl.gz", depth_levels=depth)
    s = run_replay(c, rec, names=[name], master=synth["master"], history=synth["info"]["history"],
                   run_dir=tmp_path / name)
    st = Storage(Path(s["run_dir"]) / "state.db")
    out = (s, st.kv_get("strategy", name) or {}, st.orders_df(), st.kv_get("engine", "portfolio"))
    st.close()
    return out


def test_scalper_cost_gate_and_trades(synth, tmp_path):
    # Synthetic quotes carry no order-flow information (|OFI z| never reaches 2), so the signal thresholds are
    # lowered: this test exercises the machinery (cost gate, orders, exits), not the edge.
    syms = ["NSE:SYN00", "NSE:SYN01", "NSE:SYN02", "NSE:SYN03"]
    loose = {"symbols": syms, "ofi_z": 0.3, "imbalance": 0.0, "max_spread_ticks": 40}
    # SYN00 trades near Rs 4,000 with a Rs 0.10 tick: 8 ticks (Rs 0.80) can't pay ~Rs 1.50/share of costs.
    # (The gate compares rupees, not ticks: on a Rs 0.50-tick name, 8 ticks can pay, and it lets trades through.)
    s, st, _, _ = _run(synth, tmp_path, "scalper", {**loose, "symbols": ["NSE:SYN00"]})
    assert s["errors"] == 0 and st["trades"] == 0 and st["cost_blocked"] > 0
    s, st, _, _ = _run(synth, tmp_path / "wide", "scalper", {**loose, "target_ticks": 200, "stop_ticks": 60,
                                                              "cost_multiple": 1.0})
    assert s["errors"] == 0 and st["trades"] > 0 and s["trades"] > 0


def test_market_maker_quotes_within_limits_and_ends_flat(synth, tmp_path):
    p = {"symbols": ["NSE:SYN00"], "quote_value": 400000.0, "max_inventory_value": 1600000.0, "cost_multiple": 1.0,
         "min_quote_life": 2.0}
    s, st, orders, pf = _run(synth, tmp_path, "market_maker", p)
    mm = orders[orders["strategy"] == "market_maker"] if len(orders) else orders
    assert s["errors"] == 0 and len(mm) > 0
    assert st.get("max_inventory", 0) <= 1600000.0 / 3000 + 150        # capped (price ~4,000; unit ~100 shares)
    assert not [p for p in (pf or {}).get("positions", []) if p.get("strategy") == "market_maker"]


def test_scalper_reads_deep_book_signals_through_the_engine(synth, tmp_path):
    p = {"symbols": ["NSE:SYN02", "NSE:SYN03"], "ofi_z": 0.3, "imbalance": 0.0, "max_spread_ticks": 40}
    s, st, _, _ = _run(synth, tmp_path, "scalper", p, depth=10)
    assert s["errors"] == 0 and st.get("book_seen", 0) > 100      # recording -> engine -> signals -> strategy
    assert s["signals"] > 0                                      # and each order's signal reached the desk's journal
    from nsealgo.desk.client import ReviewClient                 # saved to disk, and chartable later without a session
    saved = ReviewClient(tmp_path / "scalper")
    assert len(saved.strategy("scalper")["signals"]) == s["signals"] and saved.series(saved.j.recs[0].key)["bars"]
