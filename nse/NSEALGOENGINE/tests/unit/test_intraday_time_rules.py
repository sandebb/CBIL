"""Intraday time rules (14 Oct 2026): no MIS entries before 09:30 or from 14:30; CNC (swing) entries, exits and
stops are never blocked; the shipped config exits every intraday position by 15:00."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from nsealgo.config import load_config
from nsealgo.core.clock import IST, hm
from nsealgo.core.context import Context
from nsealgo.core.models import OrderRequest, OrderType, Product, Side


def _ctx(h, m):
    class At(Context):                                  # a subclass: Context.now itself stays untouched
        now = property(lambda self: datetime(2026, 10, 19, h, m, tzinfo=IST))
    c = At.__new__(At)
    c.e = SimpleNamespace(cfg={"session": {"intraday_entry_start": "09:30", "no_new_intraday": "14:30"}})
    return c


def _req(product=Product.MIS, intent="entry"):
    return OrderRequest("NSE:SBIN", Side.BUY, 10, OrderType.LIMIT, product, price=800.0, strategy="orb", intent=intent)


def test_mis_entries_only_between_0930_and_1430():
    assert _ctx(9, 20).intraday_entry_block(_req()) == "before 09:30"
    assert _ctx(9, 30).intraday_entry_block(_req()) is None
    assert _ctx(14, 29).intraday_entry_block(_req()) is None
    assert _ctx(14, 30).intraday_entry_block(_req()) == "after 14:30"


def test_swing_entries_exits_and_stops_are_never_blocked():
    c = _ctx(9, 16)
    assert c.intraday_entry_block(_req(Product.CNC)) is None
    assert c.intraday_entry_block(_req(intent="exit")) is None
    assert c.intraday_entry_block(_req(intent="stop")) is None


def test_shipped_config_is_flat_by_1500():
    s = load_config(str(Path(__file__).resolve().parents[2] / "config" / "system.yaml"))["session"]
    assert hm(s["intraday_entry_start"]) == hm("09:30")
    assert max(hm(s[k]) for k in ("intraday_exit_cas", "safety_flatten", "intraday_exit_noncas")) <= hm("14:55")
