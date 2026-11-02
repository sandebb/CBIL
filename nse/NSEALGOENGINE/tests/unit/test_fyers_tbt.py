"""Fyers TBT 50-level depth against frames built with Fyers' own protobuf schema; subscriptions, the symbol cap
and reconnects; tiered depth sources; Fyers 5-level DepthUpdate; the universe watch setting."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import json
from datetime import datetime

import pytest

pb = pytest.importorskip("fyers_apiv3.FyersWebsocket.msg_pb2")

from nsealgo.brokers.fyers import FyersAdapter  # noqa: E402
from nsealgo.core.models import Quote  # noqa: E402
from nsealgo.data.depth import TieredDepth  # noqa: E402
from nsealgo.data.fyers_tbt import FyersTbtFeed  # noqa: E402
from nsealgo.data.universe import watch_keys  # noqa: E402


def frame(ticker=None, snapshot=False, bids=(), asks=(), error=None):
    m = pb.SocketMessage()
    if error:
        m.error, m.msg = True, error
        return m.SerializeToString()
    m.snapshot = snapshot
    f = m.feeds[ticker]
    f.ticker, f.sequence_no, f.snapshot = ticker, 1, snapshot
    for side, levels in ((f.depth.bids, bids), (f.depth.asks, asks)):
        for lv in levels:                                    # only the fields given are present (a diff)
            x = side.add()
            if "p" in lv:
                x.price.value = lv["p"]
            if "q" in lv:
                x.qty.value = lv["q"]
            if "o" in lv:
                x.nord.value = lv["o"]
    return m.SerializeToString()


class WS:
    def __init__(self, url, header, on_open, on_message, on_close):
        self.url, self.header, self.sent = url, header, []
        self.on_open, self.on_message, self.on_close = on_open, on_message, on_close

    def send(self, text):
        self.sent.append(text if text == "ping" else json.loads(text))

    def close(self):
        pass


def tbt(clock):
    made = []

    def factory(url, header, on_open, on_message, on_close):
        made.append(WS(url, header, on_open, on_message, on_close))
        return made[-1]

    f = FyersTbtFeed("APP-100", "TOKEN", lambda k: f"NSE:{k.split(':')[1]}-EQ", ws_factory=factory, threaded=False,
                     lookup=False, now=lambda: clock[0], schema=pb)
    return f, made


SNAP_B = [{"p": 80100 - 5 * i, "q": 100 + i, "o": 1 + i} for i in range(50)]
SNAP_A = [{"p": 80105 + 5 * i, "q": 200 + i, "o": 2 + i} for i in range(50)]


def test_snapshot_then_diffs_rebuild_the_book_like_the_sdk():
    f, made = tbt([0.0])
    f.set_keys(["NSE:SBIN"])
    made[0].on_open()
    made[0].on_message(frame("NSE:SBIN-EQ", True, SNAP_B, SNAP_A))
    bids, asks = f.book("NSE:SBIN", 1.0)
    assert len(bids) == len(asks) == 50 and bids[0] == (801.0, 100.0, 1) and asks[49] == (803.5, 249.0, 51)
    made[0].on_message(frame("NSE:SBIN-EQ", False, [{"q": 999}, {}, {"p": 80080, "q": 7}], [{}, {"o": 42}]))
    bids, asks = f.book("NSE:SBIN", 1.0)
    assert bids[0] == (801.0, 999.0, 1)                       # only the quantity changed at level 1
    assert bids[1] == (800.95, 101.0, 2)                      # an empty level in a diff changes nothing
    assert bids[2] == (800.8, 7.0, 3)
    assert asks[1] == (801.1, 201.0, 42)


def test_diffs_before_any_snapshot_are_not_used():
    f, made = tbt([0.0])
    f.set_keys(["NSE:SBIN"])
    made[0].on_open()
    made[0].on_message(frame("NSE:SBIN-EQ", False, [{"p": 80100, "q": 5}], [{"p": 80105, "q": 5}]))
    assert f.book("NSE:SBIN", 1.0) is None
    made[0].on_message(frame("NSE:SBIN-EQ", True, SNAP_B, SNAP_A))
    assert f.book("NSE:SBIN", 1.0) is not None


def test_subscriptions_follow_the_documented_limits_and_survive_reconnects():
    f, made = tbt([0.0])
    f.set_keys([f"NSE:S{i}" for i in range(20)])        # docs: 3 connections per app per user x 5 symbols = 15
    assert len(made) == 3 and all(ws.header == {"authorization": "APP-100:TOKEN"} for ws in made)
    assert made[0].url.endswith("rtsocket-api.fyers.in/versova")
    for ws in made:
        ws.on_open()
        sub = [m for m in ws.sent if m["type"] == 1]
        assert len(sub) == 1 and len(sub[0]["data"]["symbols"]) == 5
        assert sub[0]["data"]["channel"] == "1" and sub[0]["data"]["subs"] == 1 and sub[0]["data"]["mode"] == "depth"
        assert ws.sent[-1] == {"type": 2, "data": {"resumeChannels": ["1"], "pauseChannels": []}}
        ws.sent.clear()
    f.set_keys(["NSE:S0", "NSE:S1", "NSE:S99"])               # rotate: 13 out, 1 in, no 4th connection
    out = [m for ws in made for m in ws.sent if m["type"] == 1 and m["data"]["subs"] == -1]
    assert sum(len(m["data"]["symbols"]) for m in out) == 13 and len(made) == 3
    add = [m for ws in made for m in ws.sent if m["type"] == 1 and m["data"]["subs"] == 1]
    assert add == [{"type": 1, "data": {"subs": 1, "symbols": ["NSE:S99-EQ"], "mode": "depth", "channel": "1"}}]
    assert [c.tickers for c in f.conns] == [{"NSE:S0-EQ", "NSE:S1-EQ", "NSE:S99-EQ"}, set(), set()]
    made[0].on_close()
    made[0].sent.clear()
    made[0].on_open()                                          # reconnect: resubscribe its symbols, then resume
    assert made[0].sent[0]["data"]["symbols"] == ["NSE:S0-EQ", "NSE:S1-EQ", "NSE:S99-EQ"]
    assert made[0].sent[-1]["type"] == 2


def test_level_index_comes_from_num_when_present():
    f, made = tbt([0.0])
    f.set_keys(["NSE:SBIN"])
    made[0].on_open()
    made[0].on_message(frame("NSE:SBIN-EQ", True, SNAP_B, SNAP_A))
    m = pb.SocketMessage()
    fd = m.feeds["NSE:SBIN-EQ"]
    fd.ticker = "NSE:SBIN-EQ"
    lv = fd.depth.bids.add()                                   # only level 8 changed, sent on its own
    lv.num.value, lv.qty.value = 7, 4242
    made[0].on_message(m.SerializeToString())
    bids, _ = f.book("NSE:SBIN", 1.0)
    assert bids[7][1] == 4242.0 and bids[0][1] == 100.0         # docs: num is the 0-based level


def test_fyers_capacity_counts_subscriptions_not_stocks(tmp_path):
    assert FyersAdapter.stream_capacity == 5000                # docs: 5,000 data subscriptions, one socket
    assert FyersAdapter({"depth": "depth5"}, tmp_path).stream_capacity == 2500


def test_fyers_errors_are_kept_and_stale_books_dropped():
    clock = [0.0]
    f, made = tbt(clock)
    f.set_keys(["NSE:SBIN"])
    made[0].on_open()
    made[0].on_message(frame(error="Symbol limit exceeded for TBT"))
    assert f.errors == ["Symbol limit exceeded for TBT"]
    made[0].on_message(frame("NSE:SBIN-EQ", True, SNAP_B, SNAP_A))
    clock[0] += 5.0
    assert f.book("NSE:SBIN", 2.0) is None


class Src:
    def __init__(self, cap, books):
        self.capacity, self.books, self.keys = cap, books, None

    def set_keys(self, keys):
        self.keys = list(keys)

    def book(self, key, max_age):
        return self.books.get(key)

    def close(self):
        pass


def test_tiered_depth_slices_the_focus_list_and_prefers_deeper_books():
    deep = (tuple((100 - i * 0.05, 10, 1) for i in range(50)), tuple((100.05 + i * 0.05, 10, 1) for i in range(50)))
    mid = (deep[0][:20], deep[1][:20])
    a, b = Src(2, {"A": deep}), Src(3, {"A": mid, "C": mid})
    t = TieredDepth([a, b])
    t.set_depth_focus(list("ABCDEFG"))
    assert a.keys == ["A", "B"] and b.keys == ["C", "D", "E"] and t.capacity == 5
    assert len(t.book("A", 2.0)[0]) == 50 and len(t.book("C", 2.0)[0]) == 20 and t.book("Z", 2.0) is None


def test_fyers_five_level_depth_merges_onto_quotes():
    msg = {"symbol": "NSE:SBIN-EQ"}
    for i in range(1, 6):
        msg.update({f"bid_price{i}": 801.05 - 0.05 * i, f"ask_price{i}": 801.05 + 0.05 * (i - 1),
                    f"bid_size{i}": 100 * i, f"ask_size{i}": 50 * i, f"bid_order{i}": i, f"ask_order{i}": 2 * i})
    bids, asks = FyersAdapter.parse_depth(msg)
    assert len(bids) == len(asks) == 5 and bids[0] == (pytest.approx(801.0), 100.0, 1) and asks[4][2] == 10
    q = Quote("NSE:SBIN", datetime(2026, 10, 1, 10, 0), 801.0, 1e5, 800.95, 801.1, 5.0, 5.0)
    m = FyersAdapter.with_depth(q, (bids, asks))
    assert m.bid == pytest.approx(801.0) and m.ask == pytest.approx(801.05) and m.bid_qty == 100.0 and len(m.asks) == 5


def test_watch_streams_the_universe_only_live():
    class Master:
        def __len__(self):
            return 3

        def keys(self, kind=None):
            return ["NSE:A", "NSE:B", "BSE:C"]

    class Uni:
        def load(self, name):
            return [f"NSE:{name.upper()}1"]

    assert watch_keys({"feeds": {"watch": "all"}}, "forward", Master(), Uni()) == ["NSE:A", "NSE:B"]
    assert watch_keys({"feeds": {"watch": "nifty500"}}, "live", Master(), Uni()) == ["NSE:NIFTY5001"]
    assert watch_keys({"feeds": {"watch": "all"}}, "backtest", Master(), Uni()) == []
    assert watch_keys({}, "live", Master(), Uni()) == []
