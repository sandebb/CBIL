"""Order-safety rules (exchange/SEBI) and statutory costs, asserted to the paisa."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from nsealgo.config import DEFAULTS
from nsealgo.core.clock import IST, SimClock
from nsealgo.core.costs import CostModel
from nsealgo.core.models import Fill, OrderRequest, OrderType, Product, Side, default_instrument
from nsealgo.core.oms import OrderManager
from nsealgo.core.portfolio import Portfolio
from nsealgo.core.risk import RiskManager
from nsealgo.core.storage import Storage


class FakeBroker:
    def __init__(self):
        self.placed, self.modified = [], []

    def place_order(self, req, cid):
        self.placed.append((req.order_type, req.price, req.trigger))
        return f"B{len(self.placed)}"

    def modify_order(self, broker_id, **kw):
        self.modified.append((broker_id, kw))

    def cancel_order(self, broker_id):
        pass

    def get_orders(self):
        return []


def _oms(quote=None, last=100.0):
    clock = SimClock(datetime(2026, 9, 28, 10, 0, tzinfo=IST))
    pf, st = Portfolio(1_000_000), Storage()
    rm = RiskManager(dict(DEFAULTS["risk"]), pf, st, priors={"t": (0.5, 2.0)}, horizons={"t": "intraday"})
    b = FakeBroker()
    oms = OrderManager(b, rm, pf, st, clock, dict(DEFAULTS["oms"]), CostModel(DEFAULTS["costs"]), default_instrument,
                       lambda k: last, quote=quote)
    return oms, b, clock, pf, rm


def test_market_order_is_sent_as_protected_limit():
    oms, b, *_ = _oms(quote=lambda k: SimpleNamespace(bid=99.95, ask=100.05))
    oms.submit(OrderRequest(key="NSE:ABC", side=Side.BUY, qty=10, order_type=OrderType.MARKET, strategy="t"))
    ot, px, _ = b.placed[-1]
    assert ot == OrderType.LIMIT and 100.05 * 1.005 <= px <= 100.05 * 1.005 + 0.05


def test_stop_market_is_sent_as_stop_limit():
    oms, b, *_ = _oms()
    oms.submit(OrderRequest(key="NSE:ABC", side=Side.SELL, qty=10, order_type=OrderType.SLM, trigger=98.0,
                            strategy="t", intent="stop"))
    ot, px, trig = b.placed[-1]
    assert ot == OrderType.SL and trig == 98.0 and px == pytest.approx(97.0)


def test_cash_short_cannot_be_carried_overnight():
    oms, b, clock, pf, rm = _oms()
    now = clock.now()
    short = dict(key="NSE:ABC", side=Side.SELL, qty=5, product=Product.CNC, price=100.0, strategy="t")
    for intent in ("entry", "exit", "stop"):            # blocked however it is labelled
        ok, why = rm.check(OrderRequest(**short, intent=intent), now, 100.0)
        assert not ok and "cash short" in why
    assert rm.check(OrderRequest(**{**short, "product": Product.MIS}), now, 100.0)[0]   # intraday short is fine
    pf.on_fill(Fill("c1", "NSE:ABC", Side.BUY, 5, 100.0, now, "t", Product.CNC, 0.0, "entry"))
    assert rm.check(OrderRequest(**short, intent="exit"), now, 100.0)[0]                # selling holdings is fine
    assert not rm.check(OrderRequest(**{**short, "qty": 6}, intent="exit"), now, 100.0)[0]


def test_unfilled_exit_is_repriced_then_left_alone_when_marketable():
    px = {"last": 100.0}
    oms, b, clock, *_ = _oms()
    oms.last_price = lambda k: px["last"]
    oms.submit(OrderRequest(key="NSE:ABC", side=Side.SELL, qty=10, price=99.8, strategy="t", intent="exit"))
    clock.set(clock.now() + timedelta(seconds=2))
    assert oms.chase_exits(clock.now()) == 0                       # too soon
    px["last"] = 99.0
    clock.set(clock.now() + timedelta(seconds=10))
    assert oms.chase_exits(clock.now()) == 1
    new = b.modified[-1][1]["price"]
    assert new < 99.0 and new >= 99.0 * (1 - 0.02)                  # more aggressive, within the cap
    px["last"] = 101.0
    clock.set(clock.now() + timedelta(seconds=10))
    assert oms.chase_exits(clock.now()) == 0                       # market came back: no churn


def test_statutory_charges_itemised_exactly():
    c = CostModel(DEFAULTS["costs"])
    b = c.breakdown(product="MIS", side="BUY", price=1000, qty=100)             # Rs 1 lakh intraday buy
    assert (b["brokerage"], b["stt"]) == (20.0, 0.0)
    assert b["exchange"] == pytest.approx(3.07) and b["stamp"] == pytest.approx(3.0)       # 0.00307%, 0.003% buy
    assert b["sebi"] == pytest.approx(0.10) and b["gst"] == pytest.approx(0.18 * (20 + 3.07 + 0.10))
    s = c.breakdown(product="MIS", side="SELL", price=1000, qty=100)
    assert s["stt"] == pytest.approx(25.0) and s["stamp"] == 0.0                          # 0.025% on the sell
    assert b["total"] + s["total"] == pytest.approx(82.6812)
    db = c.breakdown(product="CNC", side="BUY", price=1000, qty=100)
    ds = c.breakdown(product="CNC", side="SELL", price=1000, qty=100)
    assert db["stt"] == ds["stt"] == pytest.approx(100.0)                                 # 0.1% both sides
    assert db["stamp"] == pytest.approx(15.0) and ds["dp"] == pytest.approx(15.93) and db["brokerage"] == 0.0
