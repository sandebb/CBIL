"""Separate swing / intraday books (risk.books), per-book daily loss limits and kill switches, sizing caps from
free margin and depth at the best price, and per-account capital budgets in the multi-broker (16-17 Oct 2026)."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
from datetime import datetime
from types import SimpleNamespace

import pytest

from nsealgo.brokers.base import BrokerError
from nsealgo.brokers.multi import MultiBroker
from nsealgo.config import DEFAULTS
from nsealgo.core.clock import IST
from nsealgo.core.models import BrokerPosition, OrderRequest, OrderType, Product, Side
from nsealgo.core.portfolio import Portfolio
from nsealgo.core.risk import RiskManager
from nsealgo.core.storage import Storage

NOW = datetime(2026, 10, 19, 10, tzinfo=IST)


def _rm(tmp_path=None, **over):
    cfg = {**DEFAULTS["risk"], **over}
    pf = Portfolio(1_000_000)
    kill = (tmp_path / "KILL") if tmp_path else None
    return RiskManager(cfg, pf, Storage(), priors={"momentum": (0.40, 2.5), "orb": (0.35, 2.5)},
                       horizons={"momentum": "swing", "orb": "intraday"}, kill_file=kill), pf


def _req(product, price=100.0, qty=10, strategy="orb"):
    return OrderRequest("NSE:X", Side.BUY, qty, OrderType.LIMIT, product, price=price, strategy=strategy)


def test_each_book_sizes_off_its_own_capital():
    rm, _ = _rm(books={"intraday": 0.3, "swing": 0.7})
    assert rm.book_equity("intraday") == pytest.approx(300_000) and rm.book_equity("swing") == pytest.approx(700_000)
    a = rm.risk_amount("orb")
    rm2, _ = _rm(books={"intraday": 0.6, "swing": 0.4})
    assert rm2.risk_amount("orb") == pytest.approx(a * 2)


def test_margin_and_depth_cap_intraday_size():
    rm, _ = _rm()
    free = rm.size(strategy="orb", horizon="intraday", entry=100, stop=99)
    assert free > 300
    # Rs 5,000 free margin at 20% margin per rupee -> Rs 25,000 of stock = 250 shares
    assert rm.size(strategy="orb", horizon="intraday", entry=100, stop=99, avail_cash=5_000) == 250
    # 25% of 400 shares resting at the best offer = 100
    assert rm.size(strategy="orb", horizon="intraday", entry=100, stop=99, touch_qty=400) == 100
    # swing: cash at 100% of the price; depth does not apply
    assert rm.size(strategy="momentum", horizon="swing", entry=100, stop=95, avail_cash=2_000, touch_qty=1) == 20


def test_swing_book_budget_blocks_new_swing_entries():
    rm, pf = _rm(books={"intraday": 0.5, "swing": 0.01})       # swing book Rs 10,000
    assert rm.check(_req(Product.CNC, 100, 50, "momentum"), NOW, 100)[0]
    ok, why = rm.check(_req(Product.CNC, 100, 200, "momentum"), NOW, 100)
    assert not ok and "swing book" in why


def test_per_book_kill_switches(tmp_path):
    rm, _ = _rm(tmp_path)
    (tmp_path / "KILL_SWING").write_text("x")
    assert not rm.check(_req(Product.CNC, strategy="momentum"), NOW, 100)[0]
    assert rm.check(_req(Product.MIS), NOW, 100)[0]               # intraday unaffected
    assert rm.monitor() is None
    (tmp_path / "KILL_INTRADAY").write_text("x")
    assert rm.monitor() == "flatten_intraday"
    assert not rm.check(_req(Product.MIS), NOW, 100)[0]
    assert not rm.halted                                           # whole-system halt only with KILL


def test_swing_daily_loss_limit_halts_swing_entries_only():
    rm, pf = _rm()
    rm.day_start()
    rm.on_trade(SimpleNamespace(strategy="momentum", r_multiple=-1.0, product="CNC", pnl_net=-11_000))
    rm.monitor()                                                    # 2% of the 5 lakh swing book = 10,000
    assert rm.swing_halted
    assert not rm.check(_req(Product.CNC, strategy="momentum"), NOW, 100)[0]
    assert rm.check(_req(Product.MIS), NOW, 100)[0]


def test_intraday_loss_limit_is_a_share_of_the_intraday_book():
    rm, _ = _rm()
    rm.day_start()
    rm.day_intraday_pnl = -14_000                                  # 3% of 5 lakh = 15,000: not yet
    assert rm.monitor() is None
    rm.day_intraday_pnl = -15_500
    assert rm.monitor() == "flatten_intraday"


class _Acct:
    name = "a"

    def __init__(self, pos):
        self.pos, self.placed = pos, []

    def get_positions(self):
        return self.pos

    def place_order(self, req, cid):
        self.placed.append(cid)
        return "1"


def test_multi_account_capital_budget(tmp_path):
    a = _Acct([BrokerPosition("NSE:Y", 100, 450.0, "CNC")])         # Rs 45,000 in use
    mb = MultiBroker({"main": a}, state_file=tmp_path / "alloc.json", capital={"main": 50_000})
    mb.place_order(OrderRequest("NSE:X", Side.BUY, 10, OrderType.LIMIT, Product.CNC, price=400.0, strategy="s"), "c1")
    with pytest.raises(BrokerError, match="capital budget"):
        mb.place_order(OrderRequest("NSE:Z", Side.BUY, 20, OrderType.LIMIT, Product.CNC, price=400.0, strategy="s"),
                       "c2")
    exit_req = OrderRequest("NSE:Y", Side.SELL, 100, OrderType.LIMIT, Product.CNC, price=440.0, strategy="s",
                            intent="exit")
    mb.place_order(exit_req, "c3")                                  # exits are never blocked by the budget
    assert a.placed == ["c1", "c3"]
