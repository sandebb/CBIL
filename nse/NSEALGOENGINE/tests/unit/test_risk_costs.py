from datetime import datetime

from nsealgo.config import DEFAULTS
from nsealgo.core.clock import IST
from nsealgo.core.costs import CostModel
from nsealgo.core.models import OrderRequest, OrderType, Product, Side
from nsealgo.core.portfolio import Portfolio, Trade
from nsealgo.core.risk import RiskManager
from nsealgo.core.storage import Storage


def test_intraday_round_trip_costs():
    c = CostModel(DEFAULTS["costs"])
    tot = c.fill_costs(product="MIS", side="BUY", price=1000, qty=100) + \
        c.fill_costs(product="MIS", side="SELL", price=1000, qty=100)
    assert 70 < tot < 95            # Rs 1 lakh each way: brokerage 2x20, STT 25, exch, stamp, SEBI, GST


def test_delivery_costs_dominated_by_stt():
    c = CostModel(DEFAULTS["costs"])
    tot = c.round_trip_fraction("CNC", 1000, 100)
    assert 0.0022 < tot < 0.0045    # ~0.2% STT + stamp + DP charge on a Rs 1 lakh round trip


def _rm(**over):
    cfg = {**DEFAULTS["risk"], **over}
    pf = Portfolio(1_000_000)
    return RiskManager(cfg, pf, Storage(), priors={"momentum": (0.40, 2.5), "orb": (0.35, 2.5)},
                       horizons={"momentum": "swing", "orb": "intraday"}, sector_of=lambda k: "IT"), pf


def test_kelly_prior_is_capped():
    rm, _ = _rm()
    frac, d = rm.kelly("momentum")
    assert frac == DEFAULTS["risk"]["swing_cap"] and d["f_star"] > 0


def test_losing_strategy_is_switched_off():
    rm, _ = _rm()
    for _ in range(40):
        rm.stat("orb").add(-1.0)
    assert rm.kelly("orb")[0] == 0.0


def test_size_respects_caps_and_lots():
    rm, _ = _rm()
    q = rm.size(strategy="momentum", horizon="swing", entry=1000, stop=990, lot=1, gap_q=0.05)
    # swing book = 50% of 10 lakh: risk budget 5k at 10/share = 500 shares; position cap 20% of 5 lakh = 100 binds
    assert q == 100
    rm2, _ = _rm(max_position_frac=1.0)
    q = rm2.size(strategy="momentum", horizon="swing", entry=1000, stop=990, lot=1, gap_q=0.05)
    # now the gap cap binds: worst-1% gap may cost at most 2 x 5k -> 10k / (1000 x 5%) = 200 shares
    assert q == 200
    q2 = rm.size(strategy="orb", horizon="intraday", entry=1000, stop=999.99, lot=25)
    assert q2 % 25 == 0 and q2 * 1000 <= DEFAULTS["risk"]["max_order_value"]


def test_pre_trade_checks():
    rm, pf = _rm()
    now = datetime(2026, 9, 25, 10, tzinfo=IST)
    far = OrderRequest("NSE:X", Side.BUY, 10, OrderType.LIMIT, Product.MIS, price=110, strategy="orb")
    assert not rm.check(far, now, 100)[0]                       # outside the 1% price-protection band
    ok = OrderRequest("NSE:X", Side.BUY, 10, OrderType.LIMIT, Product.MIS, price=100.2, strategy="orb")
    assert rm.check(ok, now, 100)[0]
    rm.intraday_halted = True
    assert not rm.check(ok, now, 100)[0]
    exit_req = OrderRequest("NSE:X", Side.SELL, 10, OrderType.LIMIT, Product.MIS, price=99.8, strategy="orb", intent="exit")
    assert rm.check(exit_req, now, 100)[0]                      # exits are never blocked


def test_consecutive_losses_halt():
    rm, _ = _rm()
    for _ in range(3):
        rm.on_trade(Trade("orb", "NSE:X", 1, 10, "", "", 100, 99, -10, 1, -11, -1.0, "MIS"))
    assert rm.intraday_halted
