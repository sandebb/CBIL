"""Upstox depth: 30-level parsing, the 50-stock full_d30 limit, and depth in recordings."""
import json
from types import SimpleNamespace

from nsealgo.brokers.upstox import D30_LIMIT, UpstoxAdapter
from nsealgo.core.models import quote_from_dict, quote_to_dict


def _feed(levels=30):
    book = [{"bidQ": str(100 + i), "bidP": round(100.40 - 0.05 * i, 2), "askQ": str(120 + i),
             "askP": round(100.50 + 0.05 * i, 2)} for i in range(levels)]
    return {"fullFeed": {"marketFF": {"ltpc": {"ltp": 100.45, "ltt": "1790000000000", "cp": 99.0},
                                      "marketLevel": {"bidAskQuote": book}, "vtt": "12345"}}}


def test_all_depth_levels_are_parsed():
    q = UpstoxAdapter.parse_feed(_feed(30), "NSE:RELIANCE")
    assert len(q.bids) == len(q.asks) == 30
    assert q.bids[0] == (100.40, 100.0) and q.bid == 100.40 and q.ask == 100.50
    assert q.asks[-1][0] == round(100.50 + 0.05 * 29, 2)
    assert len(UpstoxAdapter.parse_feed(_feed(5), "NSE:RELIANCE").bids) == 5


class FakeStreamer:
    def __init__(self, client, keys, mode):
        self.first, self.subs, self.handlers = (list(keys), mode), [], {}

    def on(self, event, fn):
        self.handlers[event] = fn

    def subscribe(self, keys, mode):
        self.subs.append((list(keys), mode))

    def auto_reconnect(self, *a):
        pass

    def connect(self):
        self.handlers.get("open", lambda *a: None)()


def test_d30_goes_to_chosen_stocks_within_the_limit(tmp_path):
    syms = ["S0", "S1", "NSE:S2"] + [f"X{i}" for i in range(60)]
    a = UpstoxAdapter({"depth": {"mode": "full_d30", "symbols": syms}}, tmp_path)
    a._up, a._client = SimpleNamespace(MarketDataStreamerV3=FakeStreamer), object()
    a.instrument = lambda k: SimpleNamespace(token="T_" + k)
    assert len(a.depth_keys()) == D30_LIMIT == 50
    a.stream(["NSE:S0", "NSE:S1", "NSE:S2", "NSE:A", "NSE:B"], lambda q: None)
    assert a._ws.first == (["T_NSE:A", "T_NSE:B"], "full")
    assert a._ws.subs == [(["T_NSE:S0", "T_NSE:S1", "T_NSE:S2"], "full_d30")]
    a.stream(["NSE:C", "NSE:X0"], lambda q: None)                  # incremental subscriptions keep the split
    assert a._ws.subs[-2:] == [(["T_NSE:C"], "full"), (["T_NSE:X0"], "full_d30")]
    b = UpstoxAdapter({}, tmp_path)                                # default: everything 5-level `full`
    assert b.depth_keys() == []


def test_recordings_keep_the_configured_depth():
    q = UpstoxAdapter.parse_feed(_feed(30), "NSE:RELIANCE")
    d = json.loads(json.dumps(quote_to_dict(q, 5)))
    assert len(d["bids"]) == 5 and len(d["asks"]) == 5
    back = quote_from_dict(d)
    assert back.bids == q.bids[:5] and back.asks == q.asks[:5] and back.ltp == q.ltp
    assert quote_to_dict(q, 0)["bids"] == [] and len(quote_to_dict(q)["bids"]) == 30
