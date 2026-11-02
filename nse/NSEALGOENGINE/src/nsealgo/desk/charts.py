"""Charts for the strategy windows (pyqtgraph): a per-signal price chart and a per-strategy trade map."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import time
from datetime import datetime

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .widgets import AMBER, BLUE, GREEN, MUTED, RED, TEXT, mono

pg.setConfigOptions(antialias=True, background="#0f1218", foreground="#c9d1db")
VIOLET = "#a78bfa"


def epoch(iso) -> float | None:
    try:
        return datetime.fromisoformat(iso).timestamp() if iso else None
    except (TypeError, ValueError):
        return None


def hhmmss(x: float | None, daily: bool = False) -> str:
    return "" if x is None else datetime.fromtimestamp(x).strftime("%d %b" if daily else "%H:%M:%S")


def touches(bars, level, after) -> float | None:
    """When price first traded at `level` at or after time `after`: the first bar ending after `after` whose
    low-high range contains the level (where a resting order at that price would have traded)."""
    if level is None or after is None or not bars:
        return None
    width = (bars[1][0] - bars[0][0]) if len(bars) > 1 else 60.0
    for b in bars:
        if b[0] + width > after and b[3] <= level <= b[2]:
            return max(b[0], after)
    return None


class SignalChart(QWidget):
    """One signal on one scrip: the price path, when the signal fired and the order went out, the buy and sell
    levels, where the price met them, the actual fills, and the % profit or loss."""

    def __init__(self, trade: dict, series: dict, parent=None):
        super().__init__(parent)
        self.trade = trade
        bars = series.get("bars") or []
        daily = series.get("kind") == "daily"
        long = trade.get("direction", "long") == "long"
        entry = trade.get("entry_price")
        out = trade.get("exit_price") if trade.get("exit_price") is not None else trade.get("mark")
        buy, sell = (entry, out) if long else (out, entry)
        pct = trade.get("pct")
        sym = trade["key"].split(":", 1)[-1]
        col = GREEN if (pct or 0) >= 0 else RED
        state = trade.get("status", "")
        self.title = QLabel(f"<b>{sym}</b> · {'LONG' if long else 'SHORT'} {trade.get('qty', '')} · "
                            f"<b style='color:{col}'>{'–' if pct is None else f'{pct:+.2f}%'}</b> · "
                            f"<span style='color:{MUTED}'>{state}{' · ' + trade['exit_reason'] if trade.get('exit_reason') else ''}</span>")
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.title.setFont(mono(10))
        self.plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(orientation="bottom")})
        self.plot.showGrid(x=True, y=True, alpha=0.12)
        self.plot.setMenuEnabled(False)
        if bars:
            t = [b[0] for b in bars]
            lo = self.plot.plot(t, [b[3] for b in bars], pen=None)
            hi = self.plot.plot(t, [b[2] for b in bars], pen=None)
            self.plot.addItem(pg.FillBetweenItem(lo, hi, brush=(96, 165, 250, 38)))
            self.plot.plot(t, [b[4] for b in bars], pen=pg.mkPen("#60a5fa", width=1.6))
        self.vlines, self.hlines, self.points = {}, {}, {}

        def vline(name, iso, colour, style=Qt.PenStyle.SolidLine, at=0.96):
            x = epoch(iso)
            if x is None:
                return
            self.plot.addItem(pg.InfiniteLine(pos=x, angle=90, pen=pg.mkPen(colour, width=1.3, style=style),
                                              label=f"{name} {hhmmss(x, daily)}",
                                              labelOpts={"position": at, "color": colour, "fill": (15, 18, 24, 200)}))
            self.vlines[name] = x

        def hline(name, price, colour, style=Qt.PenStyle.SolidLine):
            if price is None:
                return
            self.plot.addItem(pg.InfiniteLine(pos=price, angle=0, pen=pg.mkPen(colour, width=1.3, style=style),
                                              label=f"{name} {price:,.2f}",
                                              labelOpts={"position": 0.01, "color": colour, "fill": (15, 18, 24, 200),
                                                         "anchors": [(0, 1), (0, 1)]}))
            self.hlines[name] = price

        vline("signal", trade.get("entry_signal_ts"), AMBER, Qt.PenStyle.DashLine, 0.96)
        vline("order", trade.get("entry_order_ts"), BLUE, Qt.PenStyle.SolidLine, 0.86)
        vline("exit signal", trade.get("exit_signal_ts"), VIOLET, Qt.PenStyle.DashLine, 0.96)
        vline("exit", trade.get("exit_fill_ts"), col, Qt.PenStyle.SolidLine, 0.86)
        hline("BUY", buy, GREEN)
        hline("SELL", sell, RED)
        hline("STOP", trade.get("stop"), AMBER, Qt.PenStyle.DotLine)
        # where the price path met the entry level after the order went out, and the exit level after the entry
        after = epoch(trade.get("entry_order_ts")) or epoch(trade.get("entry_signal_ts"))
        t_in = touches(bars, entry, after)
        t_out = touches(bars, out, epoch(trade.get("entry_fill_ts")) or t_in) if trade.get("status") == "closed" else None
        pts = [(t_in, entry, GREEN if long else RED), (t_out, out, RED if long else GREEN)]
        pts = [p for p in pts if p[0] is not None and p[1] is not None]
        if pts:
            self.plot.addItem(pg.ScatterPlotItem([p[0] for p in pts], [p[1] for p in pts], size=13, symbol="o",
                                                 pen=pg.mkPen("#ffffff", width=1.2),
                                                 brush=[pg.mkBrush(p[2]) for p in pts]))
        self.points = {"entry_touch": (t_in, entry), "exit_touch": (t_out, out)}
        fills = [(epoch(trade.get("entry_fill_ts")), entry, "t1" if long else "t"),
                 (epoch(trade.get("exit_fill_ts")), trade.get("exit_price"), "x")]
        for x, y, sym_ in fills:
            if x is not None and y is not None:
                self.plot.addItem(pg.ScatterPlotItem([x], [y], size=15, symbol=sym_, pen=pg.mkPen("#ffffff"),
                                                     brush=pg.mkBrush(TEXT)))
        self.pct_text = None
        px_end = epoch(trade.get("exit_fill_ts")) or (bars[-1][0] if bars else None)
        if pct is not None and px_end is not None and out is not None:
            self.pct_text = pg.TextItem(f" {pct:+.2f}% ", color=col, anchor=(0, 1), fill=(15, 18, 24, 220))
            self.pct_text.setFont(mono(11, True))
            self.pct_text.setPos(px_end, out)
            self.plot.addItem(self.pct_text)
        xs = [x for x in (epoch(trade.get("entry_signal_ts")), px_end) if x is not None]
        if xs:
            pad = 10 * 86400 if daily else 20 * 60
            lo_x, hi_x = min(xs) - pad, max(xs) + pad
            self.plot.setXRange(lo_x, hi_x, padding=0)
            ys = [b[3] for b in bars if lo_x <= b[0] <= hi_x] + [b[2] for b in bars if lo_x <= b[0] <= hi_x]
            ys += [v for v in (buy, sell, trade.get("stop")) if v is not None]
            if ys:
                span = max(ys) - min(ys) or max(ys) * 0.001
                self.plot.setYRange(min(ys) - 0.08 * span, max(ys) + 0.08 * span, padding=0)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.title)
        lay.addWidget(self.plot)


class TradeMap(QWidget):
    """A strategy's trades over time: each trade a bar from entry to exit at its % P&L (dashed while open),
    triangles at the entries, crosses at the exits, and the cumulative % below."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(orientation="bottom")})
        self.cum = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(orientation="bottom")})
        for p, label in ((self.plot, "trade %"), (self.cum, "cumulative %")):
            p.showGrid(x=True, y=True, alpha=0.12)
            p.setLabel("left", label)
            p.setMenuEnabled(False)
        self.cum.setXLink(self.plot)
        self.segments, self.cum_points = [], []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(self.plot, 3)
        lay.addWidget(self.cum, 2)

    def set_trades(self, trades: list[dict], now: float | None = None) -> None:
        self.plot.clear()
        self.cum.clear()
        self.segments, self.cum_points = [], []
        for p in (self.plot, self.cum):
            p.addItem(pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(MUTED, style=Qt.PenStyle.DashLine)))
        now = now or time.time()
        for n, tr in enumerate(trades):
            x0 = epoch(tr.get("entry_fill_ts")) or epoch(tr.get("entry_signal_ts"))
            x1 = epoch(tr.get("exit_fill_ts")) or now
            p = tr.get("pct")
            if x0 is None or p is None:
                continue
            col = GREEN if p >= 0 else RED
            closed = tr.get("status") == "closed"
            self.segments.append(self.plot.plot([x0, x1], [p, p], pen=pg.mkPen(
                col, width=5, style=Qt.PenStyle.SolidLine if closed else Qt.PenStyle.DashLine)))
            long = tr.get("direction") == "long"
            self.plot.addItem(pg.ScatterPlotItem([x0], [p], size=12, symbol="t1" if long else "t", brush=pg.mkBrush(col),
                                                 pen=pg.mkPen("#ffffff")))
            if closed:
                self.plot.addItem(pg.ScatterPlotItem([x1], [p], size=11, symbol="x", brush=pg.mkBrush(TEXT),
                                                     pen=pg.mkPen(TEXT)))
            label = pg.TextItem(f"{tr['key'].split(':', 1)[-1]} {p:+.2f}%", color=col,
                                anchor=(0, 1.15) if n % 2 == 0 else (0, -0.15))     # alternate: clustered trades stay readable
            label.setFont(mono(8))
            label.setPos(x0, p)
            self.plot.addItem(label)
        closed = sorted((t for t in trades if t.get("status") == "closed" and t.get("pct") is not None),
                        key=lambda t: t["exit_fill_ts"])
        total, xs, ys = 0.0, [], []
        for t in closed:
            total += t["pct"]
            xs.append(epoch(t["exit_fill_ts"]))
            ys.append(round(total, 3))
        if xs:
            self.cum_points = list(zip(xs, ys))
            self.cum.plot(xs, ys, pen=pg.mkPen(BLUE, width=2), symbol="o", symbolSize=6,
                          symbolBrush=[pg.mkBrush(GREEN if y >= 0 else RED) for y in ys])
