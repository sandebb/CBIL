"""The trading desk's windows, offscreen: the main window's button opens tier-1 depth with 50-level ladders,
tiers switch, depth bars and iceberg flags, market watch filtering, the kill switch asks first."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from nsealgo.desk.client import DemoClient  # noqa: E402
from nsealgo.desk.widgets import BarDelegate, Ladder  # noqa: E402
from nsealgo.desk.windows import MainWindow, WatchWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_main_window_button_opens_tier1_depth_with_50_level_ladders(app):
    w = MainWindow(DemoClient(seed=1))
    w.timer.stop()
    assert w.cards["link"].v.text() == "connected" and "DEMO" in w.banner.text()
    assert w.positions.rowCount() == 3 and w.strategies.rowCount() == 7
    w.depth_button.click()
    d = w.depth_window
    d.timer.stop()
    d.tier_timer.stop()
    assert d.isVisible()
    assert [d.tabs.tabText(i) for i in range(d.tabs.count())] == [f"TBT connection {i} · 5 stocks" for i in (1, 2, 3)]
    d.refresh()
    lad = d.ladders_in_tab(0)[0]
    assert lad.key == "NSE:RELIANCE" and lad.table.rowCount() == 50
    assert lad.table.item(49, 2).text() and lad.table.item(0, 3).text() and "50 lv" in lad.stats.text()
    d.tier_box.setCurrentIndex(1)                                   # tier 2: 30-level books
    assert d.tabs.count() == 1 and d.ladders_in_tab(0)[0].table.rowCount() == 30
    w.close()


def test_depth_bars_cumulative_and_iceberg_flags(app):
    lad = Ladder(3)
    b = {"ltp": 100.0, "prev_close": 100.0, "bids": [[99.95, 10, 1], [99.9, 10, 1], [99.85, 20, 2]],
         "asks": [[100.05, 40, 3], [100.1, 10, 1], [100.15, 10, 1]], "signals": {"absorb_ask": 4.2}}
    lad.set_book("NSE:X", b)
    assert lad.table.item(0, 4).data(BarDelegate.FRAC) == 1.0 and lad.table.item(2, 1).data(BarDelegate.FRAC) == 0.5
    assert "ICEBERG ASK" in lad.flags.text() and "&nbsp;" not in lad.flags.text().replace("&nbsp;&nbsp;", "")
    lad.set_book("NSE:X", b, cumulative=True)                      # running totals: 60 is the deepest
    assert lad.table.item(2, 4).data(BarDelegate.FRAC) == 1.0
    assert lad.table.item(2, 1).data(BarDelegate.FRAC) == pytest.approx(40 / 60)


def test_market_watch_filters_and_opens_a_ladder(app):
    client = DemoClient(seed=2)
    ww = WatchWindow(client)
    ww.timer.stop()
    assert ww.model.rowCount() == len(client.keys)
    ww.search.setText("bank")
    assert ww.proxy.rowCount() == sum("BANK" in k for k in client.keys)
    ww.open_ladder(ww.proxy.index(0, 0))
    assert ww.ladders and "BANK" in ww.ladders[-1].ladder.key
    ww.search.setText("")
    ww.deep_only.setChecked(True)
    assert ww.model.rowCount() == 25                               # 15 at 50 levels + 10 at 30
    ww.close()


def test_kill_switch_asks_first(app, monkeypatch):
    w = MainWindow(DemoClient())
    w.timer.stop()
    monkeypatch.setattr(w, "confirm_kill", lambda: False)
    w.kill_button.click()
    assert not w.client.killed
    monkeypatch.setattr(w, "confirm_kill", lambda: True)
    w.kill_button.click()
    assert w.client.killed and "KILL SWITCH SET" in w.banner.text()
    w.close()


def test_a_missing_session_shows_disconnected(app):
    class Down:
        demo = False

        def status(self):
            raise ConnectionError("connection refused")

    w = MainWindow(Down())
    w.timer.stop()
    assert w.cards["link"].v.text() == "DISCONNECTED"
    w.close()


def test_touches_finds_when_price_met_a_level():
    from nsealgo.desk.charts import touches
    bars = [[0, 100, 101, 99, 100.5, 1], [60, 100.5, 100.8, 100.2, 100.6, 1], [120, 100.6, 102, 100.5, 101.8, 1]]
    assert touches(bars, 99.5, 0) == 0                     # inside the first bar's range
    assert touches(bars, 101.5, 30) == 120                 # first reached in the third bar
    assert touches(bars, 99.5, 70) is None                 # never again after the signal


def test_signal_chart_shows_times_levels_touches_and_pct(app):
    from nsealgo.desk.charts import SignalChart
    c = DemoClient(seed=4)
    tr = next(t for t in c.strategy("orb")["trades"] if t["status"] == "closed")
    ch = SignalChart(tr, c.series(tr["key"]))
    assert set(ch.vlines) == {"signal", "order", "exit signal", "exit"}
    long = tr["direction"] == "long"
    assert ch.hlines["BUY"] == (tr["entry_price"] if long else tr["exit_price"])
    assert ch.hlines["SELL"] == (tr["exit_price"] if long else tr["entry_price"]) and ch.hlines["STOP"] == tr["stop"]
    assert ch.points["entry_touch"][0] is not None and ch.points["exit_touch"][0] is not None
    assert ch.pct_text is not None and f"{tr['pct']:+.2f}%" in ch.pct_text.toPlainText()


def test_strategy_window_from_the_main_window(app):
    c = DemoClient(seed=4)
    w = MainWindow(c)
    w.timer.stop()
    sw = w.open_strategy("orb")
    sw.timer.stop()
    d = c.strategy("orb")
    assert sw.table.rowCount() == len(d["signals"]) and len(sw.map.segments) == len(d["trades"])
    assert sw.map.cum_points and abs(sw.map.cum_points[-1][1] - d["summary"]["total_pct"]) < 1e-6
    sw.open_all()
    assert len(sw.mdi.subWindowList()) == len(d["trades"])
    row = next(i for i, g in enumerate(d["signals"]) if g["status"] == "CANCELLED")
    sub = sw.open_row(row)                                  # a signal that never filled: what it would have made
    assert "would have" in sub.widget().trade["status"]
    assert w.open_strategy("orb") is sw                     # one window per strategy
    w.close()


def saved_days(tmp_path):
    from datetime import datetime, timedelta
    from types import SimpleNamespace
    from nsealgo.core.journal import SignalJournal
    from nsealgo.core.models import Bar, OrderRequest, Product, Side, Status
    run = tmp_path / "forward"
    for day, exit_px in (("2026-09-30", 101.0), ("2026-10-01", 98.0)):
        t = datetime.fromisoformat(f"{day}T10:00:00")
        j = SignalJournal()
        j.open_day(run / "journal", day)
        e = OrderRequest(key="NSE:A", side=Side.BUY, qty=10, price=100.0, strategy="orb", intent="entry",
                         product=Product.MIS, meta={"stop": 99.0})
        j.on_submit(t, e, SimpleNamespace(client_id=f"E{day}", created=t, status=Status.FILLED), ref_price=100.0)
        j.on_fill(f"E{day}", t, 100.0, 10)
        x = OrderRequest(key="NSE:A", side=Side.SELL, qty=10, price=exit_px, strategy="orb", intent="exit",
                         product=Product.MIS, meta={"reason": "time"})
        t2 = t + timedelta(minutes=30)
        j.on_submit(t2, x, SimpleNamespace(client_id=f"X{day}", created=t2, status=Status.FILLED), ref_price=exit_px)
        j.on_fill(f"X{day}", t2, exit_px, 10)
        j.save_bars({"NSE:A": [Bar("NSE:A", t + timedelta(minutes=m), 100.0, 101.5, 97.5,
                                   100.0 + (exit_px - 100.0) * m / 40, 1000.0) for m in range(40)]})
    return run


def test_review_mode_charts_saved_days(app, tmp_path):
    from nsealgo.desk.client import ReviewClient
    c = ReviewClient(saved_days(tmp_path))
    assert c.days() == ["2026-09-30", "2026-10-01"] and c.day == "2026-10-01"
    w = MainWindow(c)
    w.timer.stop()
    assert "REVIEW" in w.banner.text() and "2026-10-01" in w.banner.text()
    assert not w.depth_button.isEnabled() and not w.watch_button.isEnabled() and not w.kill_button.isEnabled()
    sw = w.open_strategy("orb")
    sw.timer.stop()
    assert sw.data["horizon"] == "intraday" and sw.data["trades"][0]["pct"] == -2.0
    sub = sw.open_row(0)
    assert sub.widget().hlines["BUY"] == 100.0 and sub.widget().hlines["SELL"] == 98.0
    w.day_box.setCurrentText("2026-09-30")                  # another saved day
    assert sw.data["trades"][0]["pct"] == 1.0 and not sw.mdi.subWindowList()
    w.close()
