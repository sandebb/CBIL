"""The Fyers adapter against Fyers' API v3 documentation: order-tag prefixes, code 201 (placed but not
acknowledged), T1 holdings, still-forming candles, the account-wide call budget, and data-socket settings."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import sys
import time
from datetime import datetime
from types import ModuleType, SimpleNamespace

import pytest

from nsealgo.brokers.base import BrokerError, OrderUncertain
from nsealgo.brokers.fyers import FyersAdapter
from nsealgo.core.clock import IST
from nsealgo.core.models import OrderRequest, Side


class Client:
    def __init__(self, place=None, book=()):
        self.place, self.book, self.sent = place, list(book), []

    def place_order(self, data):
        self.sent.append(data)
        return self.place

    def orderbook(self):
        return {"s": "ok", "orderBook": self.book}


def adapter(tmp_path, client, cfg=None):
    a = FyersAdapter(cfg or {}, tmp_path)
    a._client = client
    a.instrument = lambda k: SimpleNamespace(broker_symbol="NSE:SBIN-EQ", token="1", tick_size=0.05)
    a.key_for = lambda token=None, symbol=None: "NSE:SBIN"
    a.min_intervals = {}
    return a


def row(oid, tag, status=6):
    return {"id": oid, "status": status, "filledQty": 0, "tradedPrice": 0, "symbol": "NSE:SBIN-EQ", "side": 1,
            "qty": 10, "orderTag": tag, "message": ""}


def req():
    return OrderRequest(key="NSE:SBIN", side=Side.BUY, qty=10, price=800.0, strategy="orb", intent="entry")


def test_order_tags_lose_the_fyers_prefix(tmp_path):
    assert FyersAdapter.user_tag("1:ORB1001093015001") == "ORB1001093015001"
    assert FyersAdapter.user_tag("2:Untagged") == "" and FyersAdapter.user_tag(None) == ""
    a = adapter(tmp_path, Client(book=[row("101", "1:ORB1001093015001"), row("102", "2:Untagged")]))
    assert [o.tag for o in a.get_orders()] == ["ORB1001093015001", ""]   # so the OMS can match its own tag


def test_code_201_is_confirmed_from_the_book_or_left_uncertain(tmp_path):
    ack_missing = {"s": "ok", "code": 201, "message": "order request made, no acknowledgement"}
    a = adapter(tmp_path, Client(place=ack_missing, book=[row("555", "1:ORB1001093015001")]))
    assert a.place_order(req(), "ORB1001093015001") == "555"
    assert a._client.sent[0]["orderTag"] == "ORB1001093015001"
    b = adapter(tmp_path, Client(place=ack_missing, book=[]))
    with pytest.raises(OrderUncertain):
        b.place_order(req(), "ORB1001093015002")
    assert not issubclass(OrderUncertain, BrokerError)               # the OMS must not call it rejected
    c = adapter(tmp_path, Client(place={"s": "error", "code": -50, "message": "Invalid price"}))
    with pytest.raises(BrokerError):
        c.place_order(req(), "ORB1001093015003")                     # a real rejection stays a rejection


def test_t1_holdings_are_counted_once():
    assert FyersAdapter.holding_qty({"holdingType": "HLD", "quantity": 10, "qty_t1": 3}) == 13
    assert FyersAdapter.holding_qty({"holdingType": "T1", "quantity": 5, "qty_t1": 5}) == 5
    assert FyersAdapter.holding_qty({"holdingType": "T1", "quantity": 0, "qty_t1": 4}) == 4
    assert FyersAdapter.holding_qty({"holdingType": "T1", "quantity": 6, "qty_t1": 0}) == 6


def test_candles_still_forming_are_dropped():
    now = datetime(2026, 10, 1, 11, 0, 30, tzinfo=IST)
    t = now.timestamp()
    rows = [(t - 120, 1, 1, 1, 1, 1), (t - 60, 1, 1, 1, 1, 1), (t - 30, 1, 1, 1, 1, 1)]
    assert len(FyersAdapter.completed(rows, "1m", now)) == 2
    today = datetime(2026, 10, 1, 0, 0, tzinfo=IST).timestamp()
    daily = [(today - 86400, 1, 1, 1, 1, 1), (today, 1, 1, 1, 1, 1)]
    assert len(FyersAdapter.completed(daily, "1d", now)) == 1
    assert len(FyersAdapter.completed(daily, "1d", now.replace(hour=16))) == 2


def test_the_call_budget_spans_all_endpoints(tmp_path):
    a = FyersAdapter({}, tmp_path)
    assert a.api_limits == (9, 180)                                  # docs: 10/s, 200/min; kept at 90%
    assert FyersAdapter({"rate_per_minute": 600}, tmp_path).api_limits == (9, 540)   # Prime
    a.api_limits, a._window, a.min_intervals = (100, 3), 0.3, {}
    t0 = time.monotonic()
    for kind in ("quote", "orders", "order", "history"):             # different endpoints share one budget
        a.throttle(kind)
    assert time.monotonic() - t0 >= 0.28


def test_data_socket_settings(tmp_path, monkeypatch):
    seen = {}

    class Sock:
        def __init__(self, **kw):
            seen.update(kw)

        def connect(self):
            pass

        def subscribe(self, **kw):
            pass

    import fyers_apiv3.FyersWebsocket as pkg
    fake = ModuleType("fyers_apiv3.FyersWebsocket.data_ws")
    fake.FyersDataSocket = Sock
    monkeypatch.setitem(sys.modules, "fyers_apiv3.FyersWebsocket.data_ws", fake)
    monkeypatch.setattr(pkg, "data_ws", fake, raising=False)
    a = adapter(tmp_path, Client(), {"client_id": "APP-100"})
    a._token = "TOKEN"
    a.stream(["NSE:SBIN"], lambda q: None)
    assert seen["access_token"] == "APP-100:TOKEN" and seen["reconnect_retry"] == 50
    assert seen["log_path"] == str(a.cache_dir) and seen["reconnect"] is True
