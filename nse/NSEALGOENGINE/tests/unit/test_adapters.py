"""Contract tests: each adapter translates canonical orders into its SDK's call and normalises replies.
Fake SDK clients record calls, so these run without credentials or network."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import types

import pandas as pd

from nsealgo.brokers.angelone import AngelOneAdapter
from nsealgo.brokers.dhan import DhanAdapter
from nsealgo.brokers.fyers import FyersAdapter
from nsealgo.brokers.noren import NorenAdapter
from nsealgo.brokers.symbols import SymbolMaster
from nsealgo.brokers.upstox import UpstoxAdapter
from nsealgo.brokers.zerodha import ZerodhaAdapter
from nsealgo.core.models import OrderRequest, OrderType, Product, Side, Status


class Recorder:
    def __init__(self, replies=None):
        self.calls, self.replies = [], replies or {}

    def __getattr__(self, name):
        def f(*a, **k):
            self.calls.append((name, a, k))
            r = self.replies.get(name)
            return r(*a, **k) if callable(r) else r
        return f


def _master(bsym="SBIN", token="3045"):
    return SymbolMaster(pd.DataFrame([{"key": "NSE:SBIN", "exchange": "NSE", "kind": "EQ", "symbol": "SBIN",
                                       "broker_symbol": bsym, "token": token, "lot_size": 1, "tick_size": 0.05}]))


REQ = OrderRequest("NSE:SBIN", Side.BUY, 10, OrderType.SL, Product.MIS, price=801.0, trigger=800.0, strategy="orb")


def _adapter(cls, client, bsym="SBIN", token="3045", cfg=None, tmp="/tmp/.nsealgo-test"):
    a = cls(cfg or {}, tmp)
    a._client, a.master = client, _master(bsym, token)
    return a


def test_zerodha():
    c = Recorder({"place_order": "123", "orders": [
        {"order_id": "123", "status": "COMPLETE", "filled_quantity": 10, "average_price": 800.4, "tradingsymbol": "SBIN",
         "transaction_type": "BUY", "quantity": 10, "tag": "ORB1"}],
        "positions": {"net": [{"tradingsymbol": "SBIN", "quantity": 10, "average_price": 800.4, "product": "MIS"}]},
        "holdings": [{"tradingsymbol": "SBIN", "quantity": 5, "t1_quantity": 2, "average_price": 700}]})
    a = _adapter(ZerodhaAdapter, c, cfg={"algo_id": "ALG1"})
    assert a.place_order(REQ, "ORB1") == "123"
    k = c.calls[0][2]
    assert (k["exchange"], k["tradingsymbol"], k["order_type"], k["trigger_price"], k["tag"], k["algo_id"]) == \
        ("NSE", "SBIN", "SL", 800.0, "ORB1", "ALG1")
    o = a.get_orders()[0]
    assert o.status == Status.FILLED and o.filled_qty == 10 and o.key == "NSE:SBIN"
    pos = {p.product: p.qty for p in a.get_positions()}
    assert pos == {"MIS": 10, "CNC": 7}


def test_fyers():
    c = Recorder({"place_order": {"s": "ok", "id": "F1"}, "orderbook": {"s": "ok", "orderBook": [
        {"id": "F1", "status": 2, "filledQty": 10, "tradedPrice": 800.2, "symbol": "NSE:SBIN-EQ", "side": 1, "qty": 10,
         "orderTag": "ORB1"}]}})
    a = _adapter(FyersAdapter, c, bsym="NSE:SBIN-EQ")
    assert a.place_order(REQ, "ORB1") == "F1"
    d = c.calls[0][2]["data"]
    assert (d["type"], d["side"], d["productType"], d["stopPrice"]) == (4, 1, "INTRADAY", 800.0)
    assert a.get_orders()[0].status == Status.FILLED


def test_noren_modify_uses_cached_order():
    c = Recorder({"place_order": {"stat": "Ok", "norenordno": "N1"}, "modify_order": {"stat": "Ok"},
                  "get_order_book": None})
    a = _adapter(NorenAdapter, c, bsym="SBIN-EQ", cfg={"flavor": "shoonya"})
    assert a.place_order(REQ, "ORB1") == "N1"
    assert c.calls[0][2]["price_type"] == "SL-LMT" and c.calls[0][2]["product_type"] == "I"
    a.modify_order("N1", order_type=OrderType.LIMIT, price=805.0)
    m = c.calls[1][2]
    assert (m["newprice_type"], m["newprice"], m["tradingsymbol"]) == ("LMT", 805.0, "SBIN-EQ")
    assert a.get_orders() == []


def test_angel_stop_orders_use_stoploss_variety():
    c = Recorder({"placeOrderFullResponse": {"status": True, "data": {"orderid": "A1"}},
                  "cancelOrder": {"status": True}})
    a = _adapter(AngelOneAdapter, c, bsym="SBIN-EQ")
    a.place_order(REQ, "ORB1")
    p = c.calls[0][1][0]
    assert (p["variety"], p["ordertype"], p["producttype"]) == ("STOPLOSS", "STOPLOSS_LIMIT", "INTRADAY")
    a.cancel_order("A1")
    assert c.calls[1][1] == ("A1", "STOPLOSS")


def test_dhan_partial_status():
    c = Recorder({"place_order": {"status": "success", "data": {"orderId": "D1"}},
                  "get_order_list": {"status": "success", "data": [
                      {"orderId": "D1", "orderStatus": "PART_TRADED", "filledQty": 4, "averageTradedPrice": 800.1,
                       "securityId": "3045", "transactionType": "BUY", "quantity": 10, "correlationId": "ORB1"}]}})
    a = _adapter(DhanAdapter, c)
    assert a.place_order(REQ, "ORB1") == "D1"
    assert c.calls[0][2]["order_type"] == "STOP_LOSS" and c.calls[0][2]["exchange_segment"] == "NSE_EQ"
    o = a.get_orders()[0]
    assert o.status == Status.PARTIAL and o.filled_qty == 4 and o.key == "NSE:SBIN"


def test_upstox_v3_order_body():
    body = {}

    class Req:
        def __init__(self, **k):
            body.update(k)

    class OrderApiV3:
        def __init__(self, client):
            pass

        def place_order(self, b):
            return {"data": {"order_ids": ["U1"]}}

    a = _adapter(UpstoxAdapter, object(), token="NSE_EQ|INE062A01020")
    a._up = types.SimpleNamespace(PlaceOrderV3Request=Req, OrderApiV3=OrderApiV3)
    assert a.place_order(REQ, "ORB1") == "U1"
    assert (body["instrument_token"], body["order_type"], body["product"], body["tag"]) == \
        ("NSE_EQ|INE062A01020", "SL", "I", "ORB1")
