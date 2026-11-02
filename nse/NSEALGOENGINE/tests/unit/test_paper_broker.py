from datetime import datetime

from nsealgo.brokers.paper import PaperBroker
from nsealgo.core.clock import IST, SimClock
from nsealgo.core.models import Bar, OrderRequest, OrderType, Product, Side, Status

T0 = datetime(2026, 9, 25, 10, 0, tzinfo=IST)


def _pb(mode="minute"):
    clk = SimClock(T0)
    return PaperBroker({"slippage_bps": 0, "participation": 1.0}, "/tmp/.nsealgo-test", clock=clk, mode=mode), clk


def _bar(o, h, l, c, v=10000, minute=1):
    return Bar("NSE:X", T0.replace(minute=minute), o, h, l, c, v)


def _status(pb, bid):
    return next(o for o in pb.get_orders() if o.broker_id == bid)


def test_resting_limit_needs_trade_through():
    pb, _ = _pb()
    pb.on_market([], [_bar(100, 100.5, 99.8, 100.2, minute=0)], T0)
    bid = pb.place_order(OrderRequest("NSE:X", Side.BUY, 10, OrderType.LIMIT, Product.MIS, price=99.5), "T1")
    pb.on_market([], [_bar(100, 100.2, 99.5, 99.9)], T0)          # touches 99.50 only: no fill (queue)
    assert _status(pb, bid).filled_qty == 0
    pb.on_market([], [_bar(99.9, 99.9, 99.4, 99.6, minute=2)], T0)  # trades through
    o = _status(pb, bid)
    assert o.status == Status.FILLED and o.avg_price == 99.5


def test_marketable_limit_fills_at_next_open():
    pb, _ = _pb()
    bid = pb.place_order(OrderRequest("NSE:X", Side.BUY, 10, OrderType.LIMIT, Product.MIS, price=101), "T2")
    pb.on_market([], [_bar(100.4, 100.9, 100.1, 100.5)], T0)
    assert _status(pb, bid).avg_price == 100.4


def test_stop_limit_gap_leaves_order_resting():
    pb, _ = _pb()
    bid = pb.place_order(OrderRequest("NSE:X", Side.BUY, 10, OrderType.SL, Product.MIS, price=101, trigger=100.5), "T3")
    pb.on_market([], [_bar(102, 102.5, 101.5, 102)], T0)           # gaps above the limit
    assert _status(pb, bid).filled_qty == 0 and _status(pb, bid).status == Status.OPEN
    pb.on_market([], [_bar(101.8, 101.9, 100.8, 101.2, minute=2)], T0)
    assert _status(pb, bid).avg_price == 101


def test_no_delivery_short_selling_and_at_close():
    pb, _ = _pb()
    bid = pb.place_order(OrderRequest("NSE:X", Side.SELL, 5, OrderType.LIMIT, Product.CNC, price=100), "T4")
    assert _status(pb, bid).status == Status.REJECTED
    b2 = pb.place_order(OrderRequest("NSE:X", Side.BUY, 5, OrderType.LIMIT, Product.CNC, price=100, at_close=True), "T5")
    pb.on_market([], [_bar(99, 99.5, 98.5, 99)], T0)               # at-close orders ignore intraday bars
    assert _status(pb, b2).filled_qty == 0
    pb.on_close({"NSE:X": 99.2}, T0)
    assert _status(pb, b2).avg_price == 99.2


def test_convert_stop_to_limit_and_cas_sweep():
    pb, clk = _pb()
    pb.cas_keys = {"NSE:X"}
    s1 = pb.place_order(OrderRequest("NSE:X", Side.SELL, 5, OrderType.SL, Product.MIS, price=98, trigger=98.5), "T6")
    pb.modify_order(s1, order_type=OrderType.LIMIT, price=99.0)
    pb.on_market([], [_bar(99.5, 99.8, 99.2, 99.4)], T0)
    assert _status(pb, s1).status == Status.FILLED
    s2 = pb.place_order(OrderRequest("NSE:X", Side.SELL, 5, OrderType.SL, Product.MIS, price=90, trigger=91), "T7")
    clk.set(T0.replace(hour=15, minute=16))
    pb.on_market([], [], clk.now())
    assert _status(pb, s2).status == Status.CANCELLED               # NSE cancels SL orders when CAS starts


def test_daily_mode_unsubscribed_orders_fill_at_close():
    """Regression: a strategy that screens a universe without subscribing symbols still gets closing fills.
    Before the fix, no day bar arrived for unsubscribed keys, so its 15:10 orders waited and expired."""
    pb, clk = _pb("daily")
    t = T0.replace(hour=15, minute=10)
    clk.set(t)
    pb.on_market([], [], t)                         # the 15:10 reveal step, with no subscribed symbols
    bid = pb.place_order(OrderRequest("NSE:X", Side.BUY, 5, OrderType.LIMIT, Product.CNC, price=101), "T9")
    pb.on_close({"NSE:X": 100.4}, t.replace(minute=31))
    o = _status(pb, bid)
    assert o.status == Status.FILLED and o.avg_price == 100.4
