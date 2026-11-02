"""A window per strategy: its trade map, its signals, and a sub-window per signal with the scrip's chart."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QMainWindow, QMdiArea, QPushButton, QSplitter, QToolBar

from .charts import SignalChart, TradeMap, epoch, hhmmss
from .widgets import AMBER, BLUE, GREEN, MUTED, RED, mono
from .windows import STATUS_COLOUR, fill, make_table


class StrategyWindow(QMainWindow):
    COLS = ["#", "Signal", "Scrip", "Side", "Intent", "Price at signal", "Order sent", "Status", "Filled at",
            "Fill price", "% P&L", "Note"]

    def __init__(self, client, name: str, refresh_ms: int = 3000):
        super().__init__()
        self.client, self.name, self.data = client, name, None
        self.setWindowTitle(f"nsealgo desk · strategy · {name}")
        tb = QToolBar("Strategy")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.summary = QLabel()
        self.summary.setFont(mono(10))
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        buttons = [("Open all trade charts", self.open_all), ("Tile", lambda: self.mdi.tileSubWindows()),
                   ("Cascade", lambda: self.mdi.cascadeSubWindows()), ("Close charts", lambda: self.mdi.closeAllSubWindows())]
        tb.addWidget(self.summary)
        for text, fn in buttons:
            b = QPushButton(text)
            b.clicked.connect(fn)
            tb.addWidget(b)
        self.map = TradeMap()
        self.table = make_table()
        self.table.doubleClicked.connect(lambda idx: self.open_row(idx.row()))
        self.mdi = QMdiArea()
        self.mdi.setBackground(Qt.GlobalColor.black)
        split = QSplitter(Qt.Orientation.Vertical)
        for w in (self.map, self.table, self.mdi):
            split.addWidget(w)
        split.setSizes([300, 200, 480])
        self.setCentralWidget(split)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(refresh_ms)
        self.resize(1500, 1000)
        self.refresh()

    def refresh(self) -> None:
        try:
            d = self.client.strategy(self.name)
        except Exception as ex:
            self.summary.setText(f"<span style='color:{RED}'>no session data: {ex}</span>")
            return
        self.data = d
        s = d.get("summary") or {}
        tot = s.get("total_pct") or 0.0
        col = GREEN if tot >= 0 else RED
        fmt = lambda v: "–" if v is None else f"{v:+.2f}%"   # noqa: E731
        self.summary.setText(
            f"<b>{self.name}</b> <span style='color:{MUTED}'>({d.get('horizon', '')})</span> · trades {s.get('trades', 0)}"
            f" + {s.get('open', 0)} open · win rate {s.get('win_rate') if s.get('win_rate') is not None else '–'}% · avg "
            f"{fmt(s.get('avg_pct'))} · <b style='color:{col}'>total {fmt(tot)}</b> · best {fmt(s.get('best_pct'))} · "
            f"worst {fmt(s.get('worst_pct'))}&nbsp;&nbsp;")
        self.map.set_trades(d.get("trades") or [])
        daily = d.get("horizon") == "swing"
        sigs = d.get("signals") or []
        rows, pcts = [], []
        for g in sigs:
            tr = self.trade_for(g)
            pct, note = (tr.get("pct"), tr.get("exit_reason") or tr.get("status")) if tr else (None, "")
            if pct is None and g.get("would_pct") is not None:
                pct, note = g["would_pct"], "not filled: would have"
            pcts.append(pct)
            rows.append([g["id"], hhmmss(epoch(g["signal_ts"]), daily), g["key"].split(":", 1)[-1], g["side"], g["intent"],
                         f"{g['ref_price']:,.2f}" if g.get("ref_price") else "–", hhmmss(epoch(g.get("order_ts")), daily),
                         g["status"], hhmmss(epoch(g.get("fill_ts")), daily),
                         f"{g['fill_price']:,.2f}" if g.get("fill_price") else "–",
                         "–" if pct is None else f"{pct:+.2f}%", note])
        fill(self.table, self.COLS, rows, lambda r, c: (
            STATUS_COLOUR.get(sigs[r]["status"], AMBER) if c == 7 else
            (GREEN if sigs[r]["side"] == "BUY" else RED) if c == 3 else
            (MUTED if pcts[r] is None else GREEN if pcts[r] >= 0 else RED) if c == 10 else
            (BLUE if sigs[r]["intent"] == "entry" else None) if c == 4 else None))

    def trade_for(self, sig: dict) -> dict | None:
        for tr in (self.data or {}).get("trades") or []:
            if tr["key"] != sig["key"]:
                continue
            if sig["intent"] == "entry" and tr.get("entry_signal_ts") == sig["signal_ts"]:
                return tr
            if sig["intent"] == "exit" and tr.get("exit_signal_ts") == sig["signal_ts"]:
                return tr
        return None

    def as_trade(self, sig: dict) -> dict:
        """A signal that never filled, charted as the trade it would have been (entered at the signal price)."""
        return {"key": sig["key"], "direction": "long" if sig["side"] == "BUY" else "short", "qty": sig.get("qty"),
                "entry_signal_ts": sig["signal_ts"], "entry_order_ts": sig.get("order_ts"), "entry_fill_ts": None,
                "entry_price": sig.get("ref_price"), "stop": sig.get("stop"), "mark": None, "exit_price": None,
                "pct": sig.get("would_pct"), "status": f"not filled ({sig['status'].lower()}): would have",
                "exit_reason": ""}

    def open_row(self, row: int):
        sigs = (self.data or {}).get("signals") or []
        if 0 <= row < len(sigs):
            return self.open_chart(self.trade_for(sigs[row]) or self.as_trade(sigs[row]))
        return None

    def open_chart(self, tr: dict):
        kind = "daily" if (self.data or {}).get("horizon") == "swing" else "intraday"
        try:
            series = self.client.series(tr["key"], kind)
        except Exception:
            series = {"bars": [], "kind": kind}
        chart = SignalChart(tr, series)
        sub = self.mdi.addSubWindow(chart)
        pct = tr.get("pct")
        sub.setWindowTitle(f"{tr['key'].split(':', 1)[-1]}  {'' if pct is None else f'{pct:+.2f}%'}")
        sub.resize(620, 400)
        sub.show()
        return sub

    def open_all(self) -> None:
        for tr in (self.data or {}).get("trades") or []:
            self.open_chart(tr)
        self.mdi.tileSubWindows()
