"""The depth overlay onto the main feed, focus ranking, and Upstox slot rotation on the main data feed."""
from datetime import datetime
from types import SimpleNamespace

from nsealgo.brokers.upstox import UpstoxAdapter
from nsealgo.core.models import Quote
from nsealgo.data.depth import DepthOverlayFeed, rank_depth_focus


def book(n=20, mid=100.0):
    return ([(round(mid - 0.05 * (i + 1), 2), 100 + i, 3) for i in range(n)],
            [(round(mid + 0.05 * (i + 1), 2), 200 + i, 4) for i in range(n)])


class Inner:
    live = True

    def __init__(self, quotes):
        self.quotes = quotes

    def subscribe(self, keys):
        pass

    def poll(self, now):
        return list(self.quotes), []


class Source:
    def __init__(self, books):
        self.books, self.focus = books, None

    def book(self, key, max_age):
        return self.books.get(key)

    def set_depth_focus(self, keys):
        self.focus = keys

    def close(self):
        pass


def test_overlay_uses_only_deeper_fresh_uncrossed_books():
    q = Quote("NSE:A", datetime(2026, 9, 30, 10, 0), 100.0, 0, 99.95, 100.05, 10, 10,
              bids=((99.95, 10.0),) * 5, asks=((100.05, 10.0),) * 5)
    bids, asks = (tuple(x) for x in book(20, 100.0))
    src = Source({"NSE:A": (bids, asks)})
    ov = DepthOverlayFeed(Inner([q]), src)
    out = ov.poll(None)[0][0]
    assert len(out.bids) == 20 and out.bid == bids[0][0] and out.ask == asks[0][0] and out.ltp == 100.0
    src.books["NSE:A"] = (asks, bids)                                # crossed book (stale mix): ignored
    assert ov.poll(None)[0][0] is q
    src.books["NSE:A"] = None                                        # no fresh book: untouched
    assert ov.poll(None)[0][0] is q
    src.books["NSE:A"] = (bids[:3], asks[:3])                        # shallower than the quote: untouched
    assert ov.poll(None)[0][0] is q
    ov.set_depth_focus(["NSE:A"])
    assert src.focus == ["NSE:A"] and ov.merged == 1


def test_focus_ranks_positions_then_candidates_round_robin():
    got = rank_depth_focus(["NSE:P"], [["NSE:A", "NSE:B", "NSE:C"], ["NSE:X", "NSE:A"], []])
    assert got == ["NSE:P", "NSE:A", "NSE:X", "NSE:B", "NSE:C"]
    assert rank_depth_focus([], [["a", "b"]], limit=1) == ["a"]


class Streamer:
    def __init__(self, client, keys, mode):
        self.first, self.calls, self.handlers = (list(keys), mode), [], {}

    def on(self, event, fn):
        self.handlers[event] = fn

    def subscribe(self, keys, mode):
        self.calls.append(("sub", list(keys), mode))

    def change_mode(self, keys, mode):
        self.calls.append(("mode", list(keys), mode))

    def auto_reconnect(self, *a):
        pass

    def connect(self):
        self.handlers.get("open", lambda *a: None)()


def test_upstox_rotates_its_fifty_deep_slots(tmp_path):
    a = UpstoxAdapter({"depth": {"mode": "full_d30", "symbols": ["S0", "S1"]}}, tmp_path)
    a._up, a._client = SimpleNamespace(MarketDataStreamerV3=Streamer), object()
    a.instrument = lambda k: SimpleNamespace(token="T_" + k)
    a.stream([f"NSE:S{i}" for i in range(80)], lambda q: None)
    assert a._d30_live == {"T_NSE:S0", "T_NSE:S1"}
    a._ws.calls.clear()
    a.set_depth_focus(["NSE:S5", "NSE:S1", "NSE:NOTSUBSCRIBED"] + [f"NSE:S{i}" for i in range(10, 70)])
    assert a._ws.calls[0] == ("mode", ["T_NSE:S0"], "full")         # S0 lost its slot
    assert a._ws.calls[1][2] == "full_d30" and len(a._d30_live) == 50
    assert {"T_NSE:S5", "T_NSE:S1"} <= a._d30_live
    a._ws.calls.clear()
    a.set_depth_focus([])                                            # nothing to focus on: keep the slots
    assert a._ws.calls == []
    UpstoxAdapter({}, tmp_path).set_depth_focus(["NSE:S1"])          # 5-level mode: nothing to rotate
