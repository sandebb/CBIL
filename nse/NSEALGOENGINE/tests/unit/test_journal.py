"""Signal journal: signal -> order -> fill records, paired into trades with % P&L (long and short), open trades
marked to the latest price, blocked signals, partial fills, and the summary."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from nsealgo.core.journal import SignalJournal, summarize
from nsealgo.core.models import OrderRequest, Product, Side, Status

T0 = datetime(2026, 10, 1, 10, 0)


def req(side, intent, key="NSE:A", reason=""):
    return OrderRequest(key=key, side=side, qty=10, price=100.0, strategy="orb", intent=intent, product=Product.MIS,
                        meta={"stop": 99.0, "reason": reason} if intent == "entry" else {"reason": reason})


def order(cid, t):
    return SimpleNamespace(client_id=cid, created=t, status=Status.OPEN)


def test_long_and_short_round_trips_with_open_and_blocked_signals():
    j = SignalJournal()
    j.on_submit(T0, req(Side.BUY, "entry"), order("C1", T0 + timedelta(seconds=1)), ref_price=100.0)
    j.on_fill("C1", T0 + timedelta(seconds=5), 100.0, 6)
    j.on_fill("C1", T0 + timedelta(seconds=6), 100.5, 4)                       # partial fills: average 100.2
    j.on_submit(T0 + timedelta(minutes=20), req(Side.SELL, "exit", reason="target"), order("C2", T0 + timedelta(minutes=20)))
    j.on_fill("C2", T0 + timedelta(minutes=20, seconds=2), 102.204, 10)
    j.on_submit(T0, req(Side.SELL, "entry", key="NSE:B"), order("C3", T0), ref_price=200.0)
    j.on_fill("C3", T0 + timedelta(seconds=3), 200.0, 10)                      # short, still open
    blocked = j.on_submit(T0, req(Side.BUY, "entry", key="NSE:C"), None, ref_price=50.0)
    assert blocked.status == "BLOCKED" and blocked.client_id is None
    trades = j.trades("orb", marks={"NSE:B": 190.0})
    a = next(t for t in trades if t["key"] == "NSE:A")
    assert a["entry_price"] == 100.2 and a["pct"] == 2.0 and a["status"] == "closed" and a["exit_reason"] == "target"
    assert a["entry_signal_ts"] == "2026-10-01T10:00:00" and a["entry_order_ts"] == "2026-10-01T10:00:01"
    b = next(t for t in trades if t["key"] == "NSE:B")
    assert b["direction"] == "short" and b["status"] == "open" and b["pct"] == 5.0          # 200 -> 190 short = +5%
    assert len(trades) == 2                                                              # the blocked signal never traded
    j.sync({"C1": SimpleNamespace(status=Status.FILLED)})
    assert j.by_cid["C1"].status == "FILLED"
    s = summarize(trades)
    assert s["trades"] == 1 and s["open"] == 1 and s["win_rate"] == 100.0 and s["total_pct"] == 2.0


def test_a_saved_day_round_trips_and_survives_a_restart(tmp_path):
    import json
    from nsealgo.core.models import Bar
    j = SignalJournal()
    p = j.open_day(tmp_path / "journal", "2026-10-01")
    j.on_submit(T0, req(Side.BUY, "entry"), order("C1", T0), ref_price=100.0)
    j.on_fill("C1", T0 + timedelta(seconds=5), 100.0, 10)
    j.sync({"C1": SimpleNamespace(status=Status.FILLED)})
    j.save_bars({"NSE:A": [Bar("NSE:A", T0, 100.0, 101.0, 99.0, 100.5, 1000.0)]})
    again = SignalJournal()                                   # the program restarts the same day
    again.open_day(tmp_path / "journal", "2026-10-01")
    assert len(again.recs) == 1 and again.by_cid["C1"].filled_qty == 10 and again.by_cid["C1"].status == "FILLED"
    again.on_submit(T0 + timedelta(minutes=20), req(Side.SELL, "exit", reason="target"), order("C2", T0 + timedelta(minutes=20)))
    again.on_fill("C2", T0 + timedelta(minutes=20, seconds=1), 102.0, 10)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('{"e": "fill", "cid": "C2", "ts"')            # a half-written line after a crash
    saved = SignalJournal.read(p)
    assert [t["pct"] for t in saved.trades()] == [2.0] and saved.trades()[0]["exit_reason"] == "target"
    bars = json.loads(p.with_suffix(".bars.json").read_text(encoding="utf-8"))
    assert bars["NSE:A"][0][1:5] == [100.0, 101.0, 99.0, 100.5]
