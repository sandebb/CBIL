"""Tick mesh: merging several free broker feeds into one clean stream."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import logging
from datetime import datetime, timedelta

import pandas as pd

from nsealgo.brokers.base import BrokerAdapter
from nsealgo.brokers.symbols import SymbolMaster
from nsealgo.core.clock import IST
from nsealgo.core.models import Quote
from nsealgo.data.mesh import MultiSourceData, TickMesh

T0 = datetime(2026, 9, 29, 10, 0, tzinfo=IST)
KEYS = [f"NSE:S{i}" for i in range(8)]


class FakeFeed(BrokerAdapter):
    supports_streaming = True

    def __init__(self, name, keys=KEYS, cap=100, history=True):
        self.name, self.cfg, self._order_cache = name, {}, {}
        self.stream_capacity, self.history = cap, history
        self.log = logging.getLogger("fake")
        self.master = SymbolMaster(pd.DataFrame([{"key": k, "exchange": "NSE", "kind": "EQ", "symbol": k[4:],
                                                  "broker_symbol": k, "token": k} for k in keys]))
        self.cb, self.streamed = None, []

    def stream(self, keys, on_quote):
        self.streamed += list(keys)
        self.cb = on_quote

    def push(self, key, ltp, vol, ts, bid=None, ask=None, bq=None, aq=None, exch=None):
        self.cb(Quote(key, ts, ltp, vol, bid, ask, bq, aq, exch_ts=exch))

    def get_quotes(self, keys):
        return [Quote(k, T0, 100.0, 5.0) for k in keys]

    def get_history(self, key, interval, start, end):
        if not self.history:
            from nsealgo.brokers.base import NotSupported
            raise NotSupported("no history")
        return pd.DataFrame({"close": [1.0]})

    def login(self, interactive=True): pass
    def _fetch_master(self): pass
    def place_order(self, req, cid): pass
    def modify_order(self, bid, **kw): pass
    def cancel_order(self, bid): pass
    def get_orders(self): return []
    def get_positions(self): return []


def test_duplicates_dropped_and_stale_prints_rejected():
    a, b = FakeFeed("a"), FakeFeed("b")
    m = TickMesh([a, b], redundancy=2)
    m.subscribe(["NSE:S0"])
    assert set(a.streamed) == set(b.streamed) == {"NSE:S0"}
    a.push("NSE:S0", 10.0, 100, T0)
    b.push("NSE:S0", 10.0, 100, T0 + timedelta(milliseconds=80))       # same print, later: duplicate
    b.push("NSE:S0", 10.1, 110, T0 + timedelta(milliseconds=200))      # newer print from b
    a.push("NSE:S0", 10.05, 105, T0 + timedelta(milliseconds=250))     # a is lagging: volume went backwards
    out, _ = m.poll(T0 + timedelta(seconds=1))
    assert [(q.ltp, q.volume) for q in out] == [(10.0, 100), (10.1, 110)]
    r = {s["source"]: s for s in m.report(T0 + timedelta(seconds=2))["sources"]}
    assert r["b"]["duplicates"] == 1 and r["a"]["stale_rejected"] == 1
    assert r["a"]["first_share"] == 0.5 and r["b"]["lag_p50_s"] == 0.08


def test_sharding_respects_caps_and_falls_back_to_rest():
    feeds = [FakeFeed(n, cap=2) for n in ("a", "b", "c")]
    m = TickMesh(feeds, redundancy=1)
    m.subscribe(KEYS[:7])
    loads = [len(f.streamed) for f in feeds]
    assert sum(loads) == 6 and max(loads) <= 2
    assert len(m.poll_keys) == 1
    out, _ = m.poll(T0)
    assert len(out) == 1 and out[0].source.endswith(":rest")


def test_failover_moves_instruments_to_a_healthy_feed():
    a, b = FakeFeed("a"), FakeFeed("b")
    m = TickMesh([a, b], redundancy=1, stale_seconds=20)
    m.subscribe(KEYS[:4])
    a_keys = set(a.streamed)
    assert a_keys and set(b.streamed) and not (a_keys & set(b.streamed))
    for s in m.srcs:
        s.started_at = T0
    b.push(sorted(b.streamed)[0], 50.0, 10, T0 + timedelta(seconds=25))
    m.poll(T0 + timedelta(seconds=25))                                  # a silent for 25 s in market hours
    assert not m.by_name["a"].healthy and a_keys <= set(b.streamed)
    assert any("failing over" in e for e in m.events)


def test_depth_comes_from_one_leader_and_ltp_only_feeds_keep_volume():
    a, b = FakeFeed("a"), FakeFeed("b")
    m = TickMesh([a, b], redundancy=2)
    m.subscribe(["NSE:S1"])
    a.push("NSE:S1", 100.0, 1000, T0, 99.95, 100.05, 500, 700)
    b.push("NSE:S1", 100.05, 1010, T0 + timedelta(milliseconds=50), 99.9, 100.1, 90, 80)   # different book
    b.push("NSE:S1", 100.10, 0, T0 + timedelta(milliseconds=90))                          # LTP-only print
    out, _ = m.poll(T0 + timedelta(seconds=1))
    assert [q.bid for q in out] == [99.95, 99.95, 99.95]          # a's book is carried; no cross-feed OFI noise
    assert out[-1].volume == 1010 and out[-1].ltp == 100.10


def test_multisource_data_falls_through_for_history():
    no_hist, hist = FakeFeed("kotak", history=False), FakeFeed("shoonya")
    d = MultiSourceData([no_hist, hist])
    assert len(d.get_history("NSE:S0", "1d", None, None)) == 1
    assert d.instrument("NSE:S0").symbol == "S0"
