"""Partial fills of intraday entries (16 Oct 2026): the rest of an intraday LIMIT entry is cancelled after
oms.entry_ttl_seconds, the shares already bought are kept; exit() first cancels a still-open entry."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from nsealgo.core.models import Order, OrderRequest, OrderType, Product, Side, Status
from nsealgo.core.oms import OrderManager as OMS


def _oms(ttl=90.0):
    o = OMS.__new__(OMS)
    o.cfg = {"entry_ttl_seconds": ttl}
    o.orders, o.notifier = {}, None
    o.cancelled = []
    o.cancel = lambda cid: (o.cancelled.append(cid), True)[1]
    return o


def _order(cid, filled, qty=100, product=Product.MIS, ot=OrderType.LIMIT, age=120, intent="entry"):
    t0 = datetime(2026, 10, 19, 10, 0)
    r = OrderRequest("NSE:SBIN", Side.BUY, qty, ot, product, price=800.0, strategy="orb", intent=intent)
    return Order(cid, r, broker_id="b" + cid, status=Status.PARTIAL if filled else Status.OPEN, filled_qty=filled,
                 created=t0 - timedelta(seconds=age))


def test_old_intraday_entries_are_cancelled_and_partials_reported():
    o = _oms()
    o.orders = {"a": _order("a", 40), "b": _order("b", 0), "c": _order("c", 10, age=30),
                "d": _order("d", 5, product=Product.CNC), "e": _order("e", 0, ot=OrderType.SL),
                "f": _order("f", 0, intent="exit")}
    part = o.expire_entries(datetime(2026, 10, 19, 10, 0))
    assert sorted(o.cancelled) == ["a", "b"]                 # young, CNC, stop-entry and exit orders untouched
    assert [x.client_id for x in part] == ["a"]
    assert o.expire_entries(datetime(2026, 10, 19, 10, 0)) == [] and sorted(o.cancelled) == ["a", "b"]  # once


def test_ttl_zero_switches_it_off():
    o = _oms(ttl=0)
    o.orders = {"a": _order("a", 40)}
    assert o.expire_entries(datetime(2026, 10, 19, 10, 0)) == [] and o.cancelled == []


def test_exit_cancels_an_open_entry_first():
    from nsealgo.strategies.base import Strategy
    calls = []
    entry = SimpleNamespace(client_id="e1", req=SimpleNamespace(intent="entry"))
    ctx = SimpleNamespace(active_orders=lambda name, key, intent=None: [entry] if intent == "entry" else [],
                          cancel=lambda cid: calls.append(cid), position=lambda name, key: None)
    st = Strategy.__new__(Strategy)
    st.name = "orb"
    assert Strategy.exit(st, ctx, "NSE:SBIN", "test") is None
    assert calls == ["e1"]
