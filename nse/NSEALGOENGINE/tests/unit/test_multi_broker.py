"""Multi-broker execution: sticky per-stock allocation, per-account order budgets, duplicate-safe outages."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
from datetime import datetime, timedelta

import pytest

from nsealgo.brokers.base import BrokerError
from nsealgo.brokers.multi import AllocationTable, MultiBroker, account_capacity
from nsealgo.core.models import BrokerOrder, BrokerPosition, OrderRequest, Side, Status


class Acct:
    def __init__(self, name, fail=None):
        self.name, self.fail, self.orders, self.cancelled, self.positions = name, fail, [], [], []

    def login(self, interactive=True):
        pass

    def place_order(self, req, cid):
        if self.fail == "reject":
            raise BrokerError("RMS: insufficient margin")
        if self.fail == "network":
            raise BrokerError("send failed") from ConnectionError("connection refused")
        if self.fail == "landed":
            self.orders.append(BrokerOrder(f"{self.name}-1", Status.OPEN, 0, 0.0, key=req.key, tag=cid))
            raise TimeoutError("read timed out")
        if self.fail == "unknown":
            raise ConnectionError("reset")
        oid = f"{self.name}-{len(self.orders) + 1}"
        self.orders.append(BrokerOrder(oid, Status.OPEN, 0, 0.0, key=req.key, side=req.side, qty=req.qty, tag=cid))
        return oid

    def get_orders(self):
        if self.fail == "unknown":
            raise ConnectionError("reset")
        return list(self.orders)

    def cancel_order(self, bid):
        self.cancelled.append(bid)

    def get_positions(self):
        return list(self.positions)


class FakeClock:
    def __init__(self):
        self.t = datetime(2026, 9, 28, 10, 0)

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)


def req(key="NSE:AAA", strategy="x", intent="entry", side=Side.BUY):
    return OrderRequest(key=key, side=side, qty=10, price=100.0, strategy=strategy, intent=intent)


def test_each_stock_sticks_to_one_account_for_every_strategy(tmp_path):
    a, b = Acct("a"), Acct("b")
    m = MultiBroker({"a": a, "b": b}, state_file=tmp_path / "s.json")
    first = m.place_order(req("NSE:AAA", "orb"), "c1").split("|")[0]
    second = m.place_order(req("NSE:BBB", "orb"), "c2").split("|")[0]
    assert first != second                                             # new stocks are spread out
    assert m.place_order(req("NSE:AAA", "vwap_fade"), "c3").startswith(first + "|")
    m.cancel_order(f"{first}|{first}-1")
    assert (a if first == "a" else b).cancelled == [f"{first}-1"]
    again = MultiBroker({"a": Acct("a"), "b": Acct("b")}, state_file=tmp_path / "s.json")
    assert again.route(req("NSE:BBB", "momentum")) == second          # the table survives restarts


def test_new_stocks_spread_by_capacity():
    t = AllocationTable(["a", "b"], {"a": 10, "b": 5})
    for i in range(30):
        t.assign(f"S{i}", t.choose(f"S{i}"))
    s = t.summary()
    assert (len(s["a"]), len(s["b"])) == (20, 10)


def test_pins_and_rebalance_leave_open_positions_alone(tmp_path):
    t = AllocationTable(["a", "b"], None, {"PINNED": "b"}, tmp_path / "s.json")
    for s in ("X1", "X2", "X3", "X4", "HELD"):
        t.assign(s, "a")
    t.assign("PINNED", "b")
    t.owners["orb|NSE:HELD"] = "a"                                     # an open position at a
    moves = t.rebalance()
    s = t.summary()
    assert t.account("HELD") == "a" and t.account("PINNED") == "b" and moves
    assert abs(len(s["a"]) - len(s["b"])) <= 1
    with pytest.raises(BrokerError):
        t.pin("HELD", "b")                                             # can't move a stock with a position
    t.pin("X1", "b")
    assert t.account("X1") == "b" and t.pins["X1"] == "b"


def test_outages_never_duplicate_orders_or_move_positions():
    a, b = Acct("a", fail="network"), Acct("b")                        # never landed: idle stock moves
    m = MultiBroker({"a": a, "b": b})
    m.table.assign("AAA", "a")
    assert m.place_order(req("NSE:AAA"), "c1").startswith("b|") and m.table.account("AAA") == "b"
    a2, b2 = Acct("a", fail="landed"), Acct("b")                       # it did land: adopt, send nothing to b
    m2 = MultiBroker({"a": a2, "b": b2})
    m2.table.assign("AAA", "a")
    assert m2.place_order(req("NSE:AAA"), "c2") == "a|a-1" and not b2.orders
    for fail in ("reject", "unknown"):                                 # a broker's answer / unknown fate: stay
        a3, b3 = Acct("a", fail=fail), Acct("b")
        m3 = MultiBroker({"a": a3, "b": b3})
        m3.table.assign("AAA", "a")
        with pytest.raises(BrokerError):
            m3.place_order(req("NSE:AAA"), "c3")
        assert not b3.orders
    a4, b4 = Acct("a"), Acct("b")                                      # an open position never moves
    m4 = MultiBroker({"a": a4, "b": b4})
    m4.table.assign("AAA", "a")
    m4.place_order(req("NSE:AAA"), "c4")
    m4.mark_down("a", "test")
    with pytest.raises(BrokerError):
        m4.place_order(req("NSE:AAA", intent="exit", side=Side.SELL), "c5")
    with pytest.raises(BrokerError):
        m4.place_order(req("NSE:AAA", strategy="other"), "c6")
    assert not b4.orders


def test_each_account_has_its_own_order_budget():
    clk = FakeClock()
    m = MultiBroker({"a": Acct("a"), "b": Acct("b")}, capacity={"a": 2, "b": 2}, clock=clk)
    m.table.assign("AAA", "a")
    m.table.assign("BBB", "b")
    for i in range(6):
        m.place_order(req("NSE:AAA", strategy=f"s{i}"), f"a{i}")
    assert m.budgets["a"].waited == pytest.approx(2.0)                 # 6 orders at 2/s: 4 waited 0.5 s each
    t0 = clk.t
    m.place_order(req("NSE:BBB"), "b0")
    m.place_order(req("NSE:BBB", strategy="y"), "b1")
    assert m.budgets["b"].waited == 0 and clk.t == t0                  # the other broker is unaffected
    assert m.max_ops() == 4


def test_account_capacity_caps_unregistered_accounts_at_ten():
    cap = account_capacity({"a": {"broker": "zerodha", "orders_per_second": 25},
                            "b": {"broker": "fyers", "orders_per_second": 25, "registered_algo": True},
                            "c": {"broker": "dhan"}})
    assert cap == {"a": 10.0, "b": 25.0, "c": 8.0}


def test_positions_are_merged_per_instrument():
    a, b = Acct("a"), Acct("b")
    a.positions = [BrokerPosition("NSE:A", 10, 100.0, "MIS")]
    b.positions = [BrokerPosition("NSE:A", 5, 106.0, "MIS"), BrokerPosition("NSE:B", -3, 50.0, "MIS")]
    got = {p.key: p for p in MultiBroker({"a": a, "b": b}).get_positions()}
    assert got["NSE:A"].qty == 15 and got["NSE:A"].avg_price == pytest.approx(102.0) and got["NSE:B"].qty == -3
