"""Order-rate governor, self-trade guard and the market maker's quoting maths."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
from datetime import datetime

import pytest

from nsealgo.config import DEFAULTS
from nsealgo.core.clock import IST, SimClock
from nsealgo.core.costs import CostModel
from nsealgo.core.models import OrderRequest, Side, default_instrument
from nsealgo.core.oms import OrderManager
from nsealgo.core.portfolio import Portfolio
from nsealgo.core.risk import RiskManager
from nsealgo.core.storage import Storage
from nsealgo.strategies.microstructure import InventoryMarketMaker


class Fake:
    def __init__(self):
        self.n = 0

    def place_order(self, req, cid):
        self.n += 1
        return f"B{self.n}"

    def get_orders(self):
        return []


def oms(broker=None, **over):
    pf, st = Portfolio(5_000_000), Storage()
    rm = RiskManager(dict(DEFAULTS["risk"]), pf, st, priors={"mm": (0.5, 1.0), "sc": (0.5, 1.0)},
                     horizons={"mm": "intraday", "sc": "intraday"})
    return OrderManager(broker or Fake(), rm, pf, st, SimClock(datetime(2026, 9, 28, 10, 0, tzinfo=IST)),
                        {**DEFAULTS["oms"], **over}, CostModel(DEFAULTS["costs"]), default_instrument, lambda k: 100.0)


class Multi(Fake):
    def max_ops(self):
        return 24.0


def test_order_rate_cap_per_broker_and_summed_across_accounts():
    assert oms(orders_per_second=20).bucket.rate == 10                  # one broker, unregistered: capped
    assert oms(orders_per_second=20, registered_algo=True).bucket.rate == 20
    assert oms().bucket.rate == 8
    assert oms(Multi()).bucket.rate == 24                               # several accounts: budgets add up


def test_self_trade_guard_blocks_orders_that_would_hit_our_own_quote():
    o = oms()
    assert o.submit(OrderRequest(key="NSE:A", side=Side.SELL, qty=10, price=100.0, strategy="mm")) is not None
    assert o.submit(OrderRequest(key="NSE:A", side=Side.BUY, qty=10, price=100.05, strategy="sc")) is None
    assert o.submit(OrderRequest(key="NSE:A", side=Side.BUY, qty=10, price=99.90, strategy="sc")) is not None


def test_market_maker_quotes_skew_with_inventory_and_pay_costs():
    mm = InventoryMarketMaker({})
    b, a = mm.quotes(100.0, 0.01, 0, 0.05, 0.02)
    assert (b, a) == pytest.approx((99.95, 100.05))                  # one tick each side: symmetric
    b2, a2 = mm.quotes(100.0, 0.01, 2, 0.05, 0.02)
    assert b2 < b and a2 < a                                         # long inventory: quote lower to sell it
    b3, a3 = mm.quotes(100.0, 0.01, 0, 0.05, 1.0)
    assert a3 - b3 >= 1.2 * 1.0 - 1e-9                               # spread always covers round-trip costs
