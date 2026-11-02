"""Monitoring service for the trading desk: token, every view, localhost-only, kill switch; the engine snapshot."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import json
import urllib.error
from datetime import datetime
from types import SimpleNamespace

import pytest

from nsealgo.compute import Compute
from nsealgo.core.book import BookSignals
import pandas as pd

from nsealgo.core.journal import SignalJournal
from nsealgo.core.models import Bar, Order, OrderRequest, Product, Quote, Side, Status
from nsealgo.core.portfolio import Portfolio
from nsealgo.data.depth import DepthOverlayFeed, TieredDepth
from nsealgo.desk.client import MonitorClient
from nsealgo.monitor import EngineSnapshot, MonitorServer, start_monitor

T0 = datetime(2026, 10, 1, 10, 0)


class Snap:
    def __init__(self):
        self.killed = False

    def status(self):
        return {"mode": "forward", "positions": []}

    def tiers(self):
        return [{"name": "Fyers TBT", "levels": 50, "groups": [["NSE:A"]]}]

    def books(self, keys):
        return {k: {"bids": [[100.0, 5, 1]], "asks": [[100.05, 3, 1]]} for k in keys}

    def watch(self):
        return [{"key": "NSE:A"}]

    def kill(self):
        self.killed = True
        return {"kill": True}


def test_monitor_needs_its_token_and_serves_every_view():
    snap = Snap()
    srv = MonitorServer(snap).start()
    try:
        assert srv.httpd.server_address[0] == "127.0.0.1"               # never another interface
        c = MonitorClient(srv.url, srv.token)
        assert c.status()["mode"] == "forward" and c.tiers()[0]["levels"] == 50
        assert c.books(["NSE:A", "NSE:B"])["NSE:B"]["asks"][0][0] == 100.05 and c.watch()[0]["key"] == "NSE:A"
        with pytest.raises(urllib.error.HTTPError) as e:
            MonitorClient(srv.url, "wrong").status()
        assert e.value.code == 403 and not snap.killed
        assert c.kill() == {"kill": True} and snap.killed
    finally:
        srv.stop()


class Src:
    capacity = 5

    def tier(self):
        return {"name": "Fyers TBT", "levels": 50, "groups": [["NSE:A"]]}

    def totals(self, key):
        return (1200.0, 900.0)

    def book(self, key, max_age):
        return None

    def set_keys(self, keys):
        pass

    def close(self):
        pass


def fake_engine(tmp_path):
    lv = 50
    q = Quote("NSE:A", T0, 101.0, 5000, 100.95, 101.05, 10.0, 10.0, prev_close=100.0,
              bids=tuple((round(100.95 - 0.05 * i, 2), 10.0, 2) for i in range(lv)),
              asks=tuple((round(101.05 + 0.05 * i, 2), 10.0, 2) for i in range(lv)))
    book = BookSignals({}, compute=Compute(device="cpu"))
    book.update([q])
    pf = Portfolio(1_000_000)
    pf.positions = lambda strategy=None, product=None: [SimpleNamespace(strategy="orb", key="NSE:A", product="MIS",
                                                                        qty=10, avg=100.0, entry_ts="09:31:00")]
    req = OrderRequest(key="NSE:A", side=Side.BUY, qty=10, price=101.0, strategy="orb", intent="entry", product=Product.MIS)
    oms = SimpleNamespace(orders={"C1": Order(client_id="C1", req=req, status=Status.OPEN, created=T0)},
                          active=lambda strategy=None: [])
    inner = SimpleNamespace(live=True, subscribe=lambda k: None, poll=lambda n: ([], []), close=lambda: None)
    j = SignalJournal()
    j.on_submit(T0, req, Order(client_id="C1", req=req, status=Status.FILLED, created=T0), ref_price=100.0)
    j.on_fill("C1", T0, 100.0, 10)
    j.on_submit(T0, OrderRequest(key="NSE:A", side=Side.BUY, qty=5, price=100.0, strategy="orb", intent="entry",
                                 product=Product.MIS), None, ref_price=100.0)          # blocked by risk
    daily = pd.DataFrame({"open": [99.0, 100.0], "high": [100.0, 101.0], "low": [98.0, 99.0], "close": [99.5, 100.5],
                          "volume": [1e5, 2e5]}, index=pd.to_datetime(["2026-09-29", "2026-09-30"]))
    return SimpleNamespace(journal=j, today_bars={"NSE:A": [Bar("NSE:A", T0, 100.0, 101.0, 99.5, 100.5, 1000.0)]},
                           history=SimpleNamespace(daily=lambda k: daily), mode="forward", clock=SimpleNamespace(now=lambda: T0), portfolio=pf, quotes={"NSE:A": q},
                           book=book, oms=oms, strategies=[SimpleNamespace(name="orb", horizon="intraday")],
                           active=lambda: [SimpleNamespace(name="orb")], regime=SimpleNamespace(state="neutral"),
                           feed=DepthOverlayFeed(inner, TieredDepth([Src()])), run_dir=tmp_path)


def test_engine_snapshot_views(tmp_path):
    s = EngineSnapshot(fake_engine(tmp_path))
    st = s.status()
    assert st["mode"] == "forward" and st["positions"][0]["unrealized"] == 10.0 and st["orders"][0]["status"] == "OPEN"
    assert st["strategies"][0]["active"] and st["kill"] is False and st["regime"] == "neutral"
    assert s.tiers() == [{"name": "Fyers TBT", "levels": 50, "groups": [["NSE:A"]]}]
    b = s.books(["NSE:A", "NSE:MISSING"])
    assert list(b) == ["NSE:A"] and len(b["NSE:A"]["bids"]) == 50 and b["NSE:A"]["tbq"] == 1200.0
    assert b["NSE:A"]["signals"]["levels"] == 50
    w = s.watch()
    assert w[0]["chg"] == 1.0 and w[0]["levels"] == 50
    s.kill()
    assert (tmp_path / "KILL").exists() and s.status()["kill"] is True


def test_start_monitor_publishes_its_address_and_token(tmp_path):
    srv = start_monitor(fake_engine(tmp_path), {"monitor": {"enabled": True, "port": 0}}, tmp_path)
    try:
        info = json.loads((tmp_path / "monitor.json").read_text(encoding="utf-8"))
        assert info["url"] == srv.url and info["token"] == srv.token
        assert MonitorClient.from_run_dir(tmp_path).status()["mode"] == "forward"
    finally:
        srv.stop()
    assert start_monitor(fake_engine(tmp_path), {"monitor": {"enabled": False}}, tmp_path) is None


def test_strategy_view_and_price_series(tmp_path):
    s = EngineSnapshot(fake_engine(tmp_path))
    v = s.strategy("orb")
    assert v["horizon"] == "intraday" and len(v["signals"]) == 2 and len(v["trades"]) == 1
    t = v["trades"][0]
    assert t["status"] == "open" and t["pct"] == 1.0                     # marked to the 101.00 quote
    blocked = v["signals"][1]
    assert blocked["status"] == "BLOCKED" and blocked["would_pct"] == 1.0   # what it would have made
    assert v["summary"]["open"] == 1
    intraday = s.series("NSE:A")
    assert intraday["bars"][0][1:5] == [100.0, 101.0, 99.5, 100.5] and intraday["ltp"] == 101.0
    assert len(s.series("NSE:A", "daily")["bars"]) == 2
