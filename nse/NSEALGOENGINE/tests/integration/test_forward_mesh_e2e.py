"""A full forward-test session through the live code path: two fake 'free' brokers stream the same
quotes (one faster; the other lagging and dropping prints), the tick mesh merges them, and the engine
trades the intraday strategies against the paper exchange on a fast-forwarded wall clock."""
from datetime import timedelta
from pathlib import Path

from nsealgo.brokers.base import BrokerAdapter
from nsealgo.brokers.paper import PaperBroker
from nsealgo.core.clock import at
from nsealgo.core.engine import Engine
from nsealgo.core.models import Quote
from nsealgo.core.notifier import Notifier
from nsealgo.core.storage import Storage
from nsealgo.data.mesh import MultiSourceData, TickMesh
from nsealgo.strategies import build_strategies


class LiveFake(BrokerAdapter):
    supports_streaming = True
    stream_capacity = 1000

    def __init__(self, name, master):
        self.name, self.cfg, self._order_cache, self.master = name, {}, {}, master
        import logging
        self.log = logging.getLogger("fake")
        self.cb, self.keys = None, set()

    def stream(self, keys, on_quote):
        self.keys |= set(keys)
        self.cb = on_quote

    def get_quotes(self, keys): return []
    def get_history(self, key, interval, start, end): raise KeyError("history comes from the store")
    def login(self, interactive=True): pass
    def _fetch_master(self): pass
    def place_order(self, req, cid): pass
    def modify_order(self, bid, **kw): pass
    def cancel_order(self, bid): pass
    def get_orders(self): return []
    def get_positions(self): return []


class FastClock:
    """Wall-clock stand-in: sleep() jumps ahead and lets the fake feeds publish what 'arrived' meanwhile."""
    is_sim = False

    def __init__(self, start, on_advance):
        self.t, self.on_advance = start, on_advance

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += timedelta(seconds=max(float(seconds), 0.5))
        self.on_advance(self.t)


def test_forward_session_over_two_feeds(synth):
    hist, day = synth["info"]["history"], synth["info"]["days"][-1]
    keys = [f"NSE:SYN{i:02d}" for i in range(12)] + ["NSE:SYNETF"]
    events = []
    for k in keys:
        m = hist.minute(k, day, day)
        cum = 0.0
        for ts, r in zip(m.index, m.itertuples(index=False)):
            for j, px in enumerate((r.open, r.high, r.low, r.close)):
                cum += r.volume / 4
                events.append((ts.to_pydatetime() + timedelta(seconds=5 + 15 * j), k, float(px), cum))
    events.sort()
    fast, slow = LiveFake("fastfeed", synth["master"]), LiveFake("slowfeed", synth["master"])
    state = {"i": 0}

    def publish(now):
        while state["i"] < len(events) and events[state["i"]][0] <= now:
            ts, k, px, vol = events[state["i"]]
            n = state["i"]
            state["i"] += 1
            q = dict(ltp=px, volume=vol, bid=round(px - 0.05, 2), ask=round(px + 0.05, 2), bid_qty=500.0, ask_qty=400.0)
            if fast.cb and k in fast.keys:
                fast.cb(Quote(k, ts, **q))
            if slow.cb and k in slow.keys and n % 7:
                slow.cb(Quote(k, ts + timedelta(milliseconds=300), **q))

    cfg = {**synth["cfg"], "session": {**synth["cfg"]["session"], "poll_seconds": 2.0}}
    clock = FastClock(at(day, "09:10"), publish)
    paper = PaperBroker(cfg["paper"], Path(synth["root"]) / ".cache", clock=clock, mode="quote", master=synth["master"])
    mesh = TickMesh([fast, slow], redundancy=2, stale_seconds=120)
    run_dir = Path(synth["root"]) / "runs" / "t_forward_mesh"
    focus_calls = []

    class DepthSpy:                                  # a depth source behind the mesh, as the Dhan overlay would be
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def set_depth_focus(self, keys):
            focus_calls.append(list(keys))

    eng = Engine(cfg, strategies=build_strategies(cfg, ["orb", "vwap_fade"]), execution=paper,
                 data=MultiSourceData([fast, slow]), feed=DepthSpy(mesh), clock=clock, history=hist,
                 storage=Storage(run_dir / "state.db"), mode="forward", granularity="minute",
                 notifier=Notifier({}), run_dir=run_dir)
    assert eng.run_day(day)
    rep = {s["source"]: s for s in mesh.report(clock.now())["sources"]}
    assert rep["fastfeed"]["first_share"] > 0.9 and rep["slowfeed"]["lag_p50_s"] == 0.3
    assert rep["slowfeed"]["duplicates"] > 1000                 # the mesh dropped the slow feed's repeats
    assert eng.storage.events_df("ERROR").empty
    assert (run_dir / "journal" / f"{day}.jsonl").exists()      # forward tests (paper orders) save their journal
    assert sum(len(v) for v in eng.today_bars.values()) > 3000   # minute bars were built from merged ticks
    assert not [p for p in eng.portfolio.positions() if p.product == "MIS"]
    assert len(focus_calls) > 100                                 # re-ranked every minute of the session
    assert all(isinstance(k, str) for c in focus_calls for k in c) and any(focus_calls)
