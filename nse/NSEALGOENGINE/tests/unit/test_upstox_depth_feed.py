"""Upstox 30-level overlay source: up to 5 connections x 50 in full_d30, rotation, freshness, and detecting a
per-user full_d30 limit (connections Upstox doesn't serve are retired and capacity shrinks)."""
from types import SimpleNamespace

from nsealgo.data.upstox_depth import UpstoxDepthFeed


def feed30(mid=100.0, levels=30):
    book = [{"bidQ": "100", "bidP": round(mid - 0.05 * (i + 1), 2), "askQ": "120", "askP": round(mid + 0.05 * (i + 1), 2)}
            for i in range(levels)]
    return {"fullFeed": {"marketFF": {"ltpc": {"ltp": mid, "ltt": "1790000000000", "cp": mid},
                                      "marketLevel": {"bidAskQuote": book}, "vtt": "100"}}}


class Streamer:
    def __init__(self, client, keys, mode):
        self.keys, self.mode, self.calls, self.handlers, self.off = list(keys), mode, [], {}, False

    def on(self, event, fn):
        self.handlers[event] = fn

    def subscribe(self, keys, mode):
        self.calls.append(("sub", list(keys), mode))

    def unsubscribe(self, keys):
        self.calls.append(("unsub", list(keys)))

    def auto_reconnect(self, *a):
        pass

    def connect(self):
        pass

    def disconnect(self):
        self.off = True

    def push(self, tokens):
        self.handlers["message"]({"feeds": {t: feed30() for t in tokens}})


def make(clock, **kw):
    made = []

    def ctor(client, keys, mode):
        made.append(Streamer(client, keys, mode))
        return made[-1]

    a = SimpleNamespace(_up=SimpleNamespace(MarketDataStreamerV3=ctor), _client=object(),
                        instrument=lambda k: SimpleNamespace(token="T_" + k))
    return UpstoxDepthFeed(a, now=lambda: clock[0], **kw), made


def test_five_connections_of_fifty_in_full_d30():
    f, made = make([0.0])
    f.set_keys([f"NSE:S{i}" for i in range(300)])
    assert f.capacity == 250 and len(made) == 5
    assert all(len(s.keys) == 50 and s.mode == "full_d30" for s in made)


def test_rotation_reuses_connections():
    f, made = make([0.0])
    keys = [f"NSE:S{i}" for i in range(60)]
    f.set_keys(keys)                                            # 50 + 10
    f.set_keys(keys[:40] + ["NSE:X1", "NSE:X2"])
    unsub = [c for s in made for c in s.calls if c[0] == "unsub"]
    sub = [c for s in made for c in s.calls if c[0] == "sub"]
    assert len(made) == 2 and sum(len(c[1]) for c in unsub) == 20
    assert all(c[2] == "full_d30" for c in sub) and sorted(t for c in sub for t in c[1]) == ["T_NSE:X1", "T_NSE:X2"]


def test_books_are_thirty_levels_and_expire():
    clock = [0.0]
    f, made = make(clock)
    f.set_keys(["NSE:S0"])
    made[0].push(["T_NSE:S0"])
    bids, asks = f.book("NSE:S0", 2.0)
    assert len(bids) == len(asks) == 30
    clock[0] += 5.0
    assert f.book("NSE:S0", 2.0) is None


def test_a_per_user_limit_is_detected_and_capacity_shrinks():
    clock = [0.0]
    f, made = make(clock)
    keys = [f"NSE:S{i}" for i in range(100)]
    f.set_keys(keys)                                            # 2 connections
    made[0].push([f"T_NSE:S{i}" for i in range(50)])            # Upstox serves only the first one
    clock[0] = 61.0
    f.set_keys(keys)
    assert made[1].off and f.capacity == 50 and "per-user" in f.notes[0]
    f.set_keys(keys)
    assert len(made) == 2                                       # no new connections once the limit is known


def test_nothing_is_retired_while_the_market_is_quiet():
    clock = [0.0]
    f, made = make(clock)
    keys = [f"NSE:S{i}" for i in range(100)]
    f.set_keys(keys)
    clock[0] = 600.0                                           # no data anywhere: market closed, not a cap
    f.set_keys(keys)
    assert not any(s.off for s in made) and f.capacity == 250
