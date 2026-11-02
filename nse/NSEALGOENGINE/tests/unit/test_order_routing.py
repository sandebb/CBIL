"""Live order routing (broker.orders), dormant brokers, the product router, the Fyers SDK session going through the
static-IP transport, code -99 as an uncertain outcome, and the shipped config itself."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
from pathlib import Path
from types import SimpleNamespace

import pytest

from nsealgo.brokers.base import BrokerError, OrderUncertain
from nsealgo.brokers.routing import ProductRouter, build_execution, describe, order_brokers
from nsealgo.config import load_config
from nsealgo.core.models import BrokerOrder, BrokerPosition, OrderRequest, Product, Side, Status


def test_orders_resolve_from_config_with_delivery_defaulting_to_intraday():
    c = {"broker": {"execution": "paper", "orders": {"intraday": "fyers"}}}
    assert order_brokers(c) == {"MIS": "fyers", "CNC": "fyers"} and describe(c) == "all live orders -> fyers"
    c["broker"]["orders"]["delivery"] = "zerodha"
    assert order_brokers(c) == {"MIS": "fyers", "CNC": "zerodha"} and "delivery (CNC) orders -> zerodha" in describe(c)
    assert order_brokers(c, override="upstox") == {"MIS": "upstox", "CNC": "upstox"}
    assert order_brokers({"broker": {"execution": "zerodha"}}) == {"MIS": "zerodha", "CNC": "zerodha"}  # older configs
    with pytest.raises(BrokerError):
        order_brokers({"broker": {"execution": "paper"}})                    # nothing live configured


def test_dormant_brokers_never_receive_orders():
    c = {"broker": {"execution": "paper", "orders": {"intraday": "shoonya"}, "brokers": {"shoonya": {"orders": False}}}}
    with pytest.raises(BrokerError, match="dormant"):
        order_brokers(c)
    with pytest.raises(BrokerError, match="dormant"):
        order_brokers(c, override="shoonya")
    c["broker"]["orders"]["intraday"] = "fyers"
    assert order_brokers(c)["MIS"] == "fyers"
    m = {"broker": {"execution": "multi", "brokers": {"shoonya": {"orders": False}}},
         "accounts": {"a": {"broker": "fyers"}, "b": {"broker": "shoonya"}}}
    with pytest.raises(BrokerError, match="dormant"):
        order_brokers(m)


class Fake:
    def __init__(self, name):
        self.name, self.placed, self.calls = name, [], []

    def place_order(self, req, cid):
        self.placed.append(cid)
        return f"{self.name}-{len(self.placed)}"

    def cancel_order(self, bid):
        self.calls.append(("cancel", bid))

    def modify_order(self, bid, **kw):
        self.calls.append(("modify", bid, kw))

    def get_orders(self):
        return [BrokerOrder(f"{self.name}-1", Status.OPEN, 0, 0.0, key="NSE:A", tag="T1")]

    def get_positions(self):
        return [BrokerPosition("NSE:A", 10, 100.0, "MIS" if self.name == "fyers" else "CNC")]

    def login(self, interactive=True):
        pass

    def instrument(self, key):
        return SimpleNamespace(key=key, tick_size=0.05)


def req(product):
    return OrderRequest(key="NSE:A", side=Side.BUY, qty=1, price=100.0, strategy="orb", intent="entry", product=product)


def test_product_router_sends_each_product_to_its_broker():
    f, z = Fake("fyers"), Fake("zerodha")
    r = ProductRouter({"MIS": f, "CNC": z})
    assert r.place_order(req(Product.MIS), "C1") == "fyers|fyers-1"
    assert r.place_order(req(Product.CNC), "C2") == "zerodha|zerodha-1"
    r.cancel_order("zerodha|zerodha-1")
    r.modify_order("fyers|fyers-1", price=101.0)
    assert z.calls == [("cancel", "zerodha-1")] and f.calls == [("modify", "fyers-1", {"price": 101.0})]
    assert sorted(o.broker_id for o in r.get_orders()) == ["fyers|fyers-1", "zerodha|zerodha-1"]
    assert {(p.key, p.product, p.qty) for p in r.get_positions()} == {("NSE:A", "MIS", 10), ("NSE:A", "CNC", 10)}
    assert r.instrument("NSE:A").key == "NSE:A"                               # anything else: the intraday broker
    with pytest.raises(BrokerError):
        r.cancel_order("unknown|1")


def test_build_execution_reuses_brokers_already_logged_in(monkeypatch):
    import nsealgo.brokers.registry as reg
    made, logged = [], []
    monkeypatch.setattr(reg, "make_broker", lambda name, cfg, cache, **kw: made.append(name) or Fake(name))
    data_fyers = Fake("fyers")
    c = {"broker": {"execution": "paper", "orders": {"intraday": "fyers"}}}
    assert build_execution(c, [data_fyers], ".", login=logged.append) is data_fyers and made == [] and logged == []
    c["broker"]["orders"]["delivery"] = "zerodha"
    ex = build_execution(c, [data_fyers], ".", login=logged.append)
    assert isinstance(ex, ProductRouter) and made == ["zerodha"] and [b.name for b in logged] == ["zerodha"]


def test_fyers_sdk_session_goes_through_the_static_ip_transport(tmp_path, monkeypatch):
    from nsealgo.brokers.fyers import API_BASE, FyersAdapter
    from nsealgo.brokers.transport import BrokerTransport, SessionShim
    a = FyersAdapter({"proxy": "http://u:p@proxy.example:3128"}, tmp_path)
    a.transport = BrokerTransport.from_config("fyers", API_BASE, a.cfg)
    calls = []
    monkeypatch.setattr(a.transport, "request", lambda method, url, **kw: calls.append((method, url)) or "resp")
    client = SimpleNamespace(service=SimpleNamespace(session=object()))
    a._wire(client)
    assert isinstance(client.service.session, SessionShim)
    assert client.service.session.post("https://api-t1.fyers.in/api/v3/orders/sync", data="{}") == "resp"
    assert calls == [("POST", "https://api-t1.fyers.in/api/v3/orders/sync")]
    assert a.transport.host == "api-t1.fyers.in" and a.transport.session.proxies["https"] == "http://u:p@proxy.example:3128"


def test_fyers_no_response_is_uncertain_not_rejected(tmp_path):
    from nsealgo.brokers.fyers import FyersAdapter
    a = FyersAdapter({}, tmp_path)
    a.min_intervals = {}
    a.instrument = lambda k: SimpleNamespace(broker_symbol="NSE:SBIN-EQ")
    a.key_for = lambda token=None, symbol=None: "NSE:SBIN"
    swallowed = {"s": "error", "code": -99, "message": "Bad request"}       # the SDK hides timeouts this way
    a._client = SimpleNamespace(place_order=lambda data: swallowed, orderbook=lambda: {"orderBook": []})
    with pytest.raises(OrderUncertain):
        a.place_order(req(Product.MIS), "ORB1")
    book = [{"id": "77", "status": 6, "filledQty": 0, "tradedPrice": 0, "symbol": "NSE:SBIN-EQ", "side": 1, "qty": 1,
             "orderTag": "1:ORB2", "message": ""}]
    a._client = SimpleNamespace(place_order=lambda data: swallowed, orderbook=lambda: {"orderBook": book})
    assert a.place_order(req(Product.MIS), "ORB2") == "77"                  # it did reach Fyers


def test_shipped_config_routes_live_orders_to_shoonya_and_fyers_is_data_only():
    """16 Oct 2026: Shoonya places all orders, Fyers is the data feed. Until setup_shoonya.ps1 -TestOrder -Enable
    has passed, shoonya.orders is false and resolving the order broker refuses (dormant) - nothing can trade."""
    c = load_config(str(Path(__file__).resolve().parents[2] / "config" / "system.yaml"))
    assert c["broker"]["orders"] == {"intraday": "shoonya", "delivery": "shoonya"}
    assert c["broker"]["data"] == "fyers"
    s = c["broker"]["brokers"]["shoonya"]
    assert isinstance(s, dict) and {"user_id", "totp_secret", "vendor_code", "api_secret", "proxy", "static_ip"} <= set(s)
    if s.get("orders") is False:
        with pytest.raises(BrokerError, match="dormant"):
            order_brokers(c)
    else:
        assert order_brokers(c) == {"MIS": "shoonya", "CNC": "shoonya"}
