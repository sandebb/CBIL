"""Order-book signals: multi-level flow z-score, iceberg (absorption) detection, freshness, ORB's deep-book
check, and the depth study on a session where only the deeper levels carry information."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
from datetime import datetime, timedelta

import numpy as np

from nsealgo.compute import Compute
from nsealgo.config import load_config
from nsealgo.core.book import BookSignals, BookState
from nsealgo.core.models import Quote, Side
from nsealgo.research import book_study
from nsealgo.strategies import build_strategies

T0 = datetime(2026, 9, 30, 10, 0, 0)


def snap(t, bid=100.0, ask=100.05, bq=None, aq=None, L=10, vol=0.0, ltp=None, key="NSE:X"):
    bq, aq = bq or [200.0] * L, aq or [200.0] * L
    bids = tuple((round(bid - 0.05 * k, 2), float(bq[k])) for k in range(L))
    asks = tuple((round(ask + 0.05 * k, 2), float(aq[k])) for k in range(L))
    return Quote(key, T0 + timedelta(seconds=t), ltp if ltp is not None else round((bid + ask) / 2, 3), vol, bid, ask,
                 float(bq[0]), float(aq[0]), bids=bids, asks=asks)


def signals():
    return BookSignals({"window_seconds": 10}, compute=Compute(device="cpu"))


def test_flow_z_follows_bids_building_across_levels():
    s, rng = signals(), np.random.default_rng(0)
    for t in range(60):                                              # noise: sizes jitter, prices still
        s.update([snap(t, bq=list(200 + rng.normal(0, 20, 10)), aq=list(200 + rng.normal(0, 20, 10)))])
    for k in range(8):                                               # bids build at every level, asks thin out
        s.update([snap(60 + k, bq=[200.0 + 60 * (k + 1)] * 10, aq=[max(200.0 - 20 * (k + 1), 10.0)] * 10)])
    st = s.state("NSE:X", T0 + timedelta(seconds=67))
    assert st.ofi_z > 2 and st.imbalance > 0.3 and st.levels == 10


def test_absorption_flags_an_iceberg_but_not_a_clean_breakout():
    s, vol = signals(), 0.0
    for t in range(20):                     # buyers lift 500 a snapshot at 100.05; the ask keeps showing ~200
        vol += 500
        s.update([snap(t, vol=vol, ltp=100.05)])
    st = s.state("NSE:X", T0 + timedelta(seconds=19))
    assert st.absorb_ask > 2 and st.absorb_bid == 0
    s, vol = signals(), 0.0
    for t in range(20):                     # each ask is taken out and the price walks up: nothing hidden
        vol += 500
        bid = round(100.0 + 0.05 * t, 2)
        s.update([snap(t, bid=bid, ask=round(bid + 0.05, 2), vol=vol, ltp=bid)])
    assert s.state("NSE:X", T0 + timedelta(seconds=19)).absorb_ask == 0


def test_states_expire_and_depthless_quotes_are_ignored():
    s = signals()
    s.update([snap(0)])
    assert s.state("NSE:X", T0) is not None and s.state("NSE:X", T0 + timedelta(seconds=30)) is None
    s.update([Quote("NSE:Y", T0, 100.0, 0, 99.95, 100.05, 10, 10)])
    assert s.state("NSE:Y", T0) is None


def test_orb_breakout_check_uses_the_deep_book_when_present():
    orb = build_strategies(load_config(None), ["orb"])[0]

    class Ctx:
        def __init__(self, b):
            self.b = b

        def book(self, key):
            return self.b

    def state(z, absorb_ask=0.0, absorb_bid=0.0):
        return BookState(T0, 20, 100.0, 5.0, 0.0, 0.0, 0.0, z, absorb_ask, absorb_bid)

    assert orb._book_ok(Ctx(None), "NSE:X", Side.BUY) is None                 # no depth: top-of-book check
    assert orb._book_ok(Ctx(state(1.5, 0.5)), "NSE:X", Side.BUY) is True
    assert orb._book_ok(Ctx(state(1.5, 4.0)), "NSE:X", Side.BUY) is False     # iceberg sitting on the ask
    assert orb._book_ok(Ctx(state(-1.0)), "NSE:X", Side.BUY) is False         # flow against the breakout
    assert orb._book_ok(Ctx(state(-1.0, 0.0, 0.5)), "NSE:X", Side.SELL) is True


def test_book_study_finds_information_only_where_it_is():
    rng, quotes = np.random.default_rng(4), []
    for key in ("NSE:A", "NSE:B"):
        mid = 100.0
        for t in range(600):
            d = rng.uniform(-1, 1)                   # levels 2-10 lean with the next move; level 1 is noise
            bq = [rng.uniform(50, 350)] + [200 * (1 + d)] * 9
            aq = [rng.uniform(50, 350)] + [200 * (1 - d)] * 9
            bid, ask = round(mid - 0.025, 3), round(mid + 0.025, 3)
            quotes.append(Quote(key, T0 + timedelta(seconds=t), mid, 0, bid, ask, bq[0], aq[0],
                                bids=tuple((bid - 0.05 * k, bq[k]) for k in range(10)),
                                asks=tuple((ask + 0.05 * k, aq[k]) for k in range(10))))
            mid += 0.05 * np.sign(d) * (1 if rng.random() < 0.8 else -1)
    df = book_study(quotes, horizons=(1,), levels=(1, 10), compute=Compute(device="cpu"))
    ic = {(r.signal, r.levels): r.ic for r in df.itertuples()}
    assert ic[("imbalance", 10)] > 0.3 and abs(ic[("imbalance", 1)]) < 0.15
