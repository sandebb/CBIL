"""The desk's windows: the main dashboard, tier-1 depth, market watch, and a single-stock ladder."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
                               QTableView, QTableWidget, QTableWidgetItem, QTabWidget, QToolBar, QToolButton, QVBoxLayout,
                               QWidget)

from .widgets import AMBER, BLUE, GREEN, MUTED, RED, TEXT, Ladder, indian, money, mono, signed

BANNERS = {"live": (RED, "LIVE · real orders"), "forward": (BLUE, "FORWARD TEST · paper orders"),
           "demo": ("#7c3aed", "DEMO DATA · not connected to any session"),
           "review": ("#0f766e", "REVIEW · saved session")}
STATUS_COLOUR = {"FILLED": GREEN, "REJECTED": RED, "OPEN": BLUE, "NEW": BLUE, "PARTIAL": BLUE, "CANCELLED": MUTED,
                 "UNKNOWN": AMBER}
LEVEL_COLOUR = {"ERROR": RED, "CRITICAL": RED, "WARNING": AMBER}


def make_table() -> QTableWidget:
    t = QTableWidget()
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(22)
    t.horizontalHeader().setStretchLastSection(True)
    return t


def fill(t: QTableWidget, headers, rows, colour=None) -> None:
    t.setColumnCount(len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            it = t.item(r, c)
            if it is None:
                it = QTableWidgetItem()
                t.setItem(r, c, it)
            it.setText("" if v is None else str(v))
            num = isinstance(v, (int, float)) or (isinstance(v, str) and v[:1] in "+-0123456789" and c > 0)
            it.setTextAlignment((Qt.AlignmentFlag.AlignRight if num else Qt.AlignmentFlag.AlignLeft)
                                | Qt.AlignmentFlag.AlignVCenter)
            it.setForeground(QColor((colour(r, c) if colour else None) or TEXT))


class Card(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("card")
        self.t, self.v = QLabel(title.upper()), QLabel("–")
        self.t.setObjectName("cardTitle")
        self.v.setObjectName("cardValue")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.addWidget(self.t)
        lay.addWidget(self.v)

    def set(self, text: str, colour: str = TEXT) -> None:
        self.v.setText(text)
        self.v.setStyleSheet(f"color: {colour};")


def pnl_colour(x) -> str:
    return TEXT if not x else (GREEN if x > 0 else RED)


class MainWindow(QMainWindow):
    CARDS = [("now", "Time (IST)"), ("equity", "Equity"), ("pnl", "Day P&L"), ("unrealized", "Unrealized"),
             ("realized", "Realized"), ("exposure", "Gross exposure"), ("regime", "Regime"),
             ("positions", "Open positions"), ("orders", "Working orders"), ("instruments", "Instruments"),
             ("link", "Session link")]

    def __init__(self, client, refresh_ms: int = 1000):
        super().__init__()
        self.client, self.depth_window, self.watch_window = client, None, None
        self.strategy_windows, self.last_status = {}, {}
        self.setWindowTitle("nsealgo desk")
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.depth_button = QPushButton("Tier-1 depth · 50 levels")
        self.depth_button.setObjectName("primary")
        self.depth_button.clicked.connect(self.open_depth)
        self.watch_button = QPushButton("Market watch")
        self.watch_button.clicked.connect(self.open_watch)
        self.strategy_button = QToolButton()
        self.strategy_button.setText("Strategy windows ▾")
        self.strategy_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.strategy_menu = QMenu(self)
        self.strategy_menu.aboutToShow.connect(self._fill_strategy_menu)
        self.strategy_button.setMenu(self.strategy_menu)
        self.kill_button = QPushButton("KILL SWITCH")
        self.kill_button.setObjectName("kill")
        self.kill_button.clicked.connect(self.on_kill)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        widgets = [self.depth_button, self.watch_button, self.strategy_button]
        self.review = bool(getattr(client, "review", False))
        self.day_box = None
        if hasattr(client, "days"):                       # review: pick any saved day
            self.day_box = QComboBox()
            self.day_box.addItems(client.days())
            self.day_box.setCurrentText(client.day)
            self.day_box.currentTextChanged.connect(self.change_day)
            widgets += [QLabel("  Day "), self.day_box]
        for w in widgets + [spacer, self.kill_button]:
            tb.addWidget(w)
        if self.review:
            for b in (self.depth_button, self.watch_button, self.kill_button):
                b.setEnabled(False)
                b.setToolTip("needs a running session (nsealgo forwardtest or live)")
        central = QWidget()
        lay = QVBoxLayout(central)
        self.banner = QLabel()
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.banner)
        grid = QGridLayout()
        self.cards = {}
        for i, (key, title) in enumerate(self.CARDS):
            self.cards[key] = Card(title)
            grid.addWidget(self.cards[key], i // 6, i % 6)
        lay.addLayout(grid)
        self.tabs = QTabWidget()
        self.positions, self.orders, self.strategies, self.alerts = (make_table() for _ in range(4))
        for t, name in ((self.positions, "Positions"), (self.orders, "Orders"), (self.strategies, "Strategies"),
                        (self.alerts, "Alerts")):
            self.tabs.addTab(t, name)
        for t in (self.positions, self.strategies):
            t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.tabs, 1)
        self.strategies.doubleClicked.connect(
            lambda idx: self.open_strategy(self.strategies.item(idx.row(), 0).text()))
        self.setCentralWidget(central)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(refresh_ms)
        self.refresh()

    def refresh(self) -> None:
        try:
            s = self.client.status()
        except Exception as ex:
            self.cards["link"].set("DISCONNECTED", RED)
            self.statusBar().showMessage(f"no session data: {ex}")
            return
        self.cards["link"].set("saved day" if self.review else "connected", "#2dd4bf" if self.review else GREEN)
        self.last_status = s
        mode = s.get("mode", "?")
        colour, text = BANNERS.get(mode, (MUTED, str(mode).upper()))
        if mode == "review":
            text = f"REVIEW · saved {s.get('label', '')} session · {s.get('day', '')} · no live data"
        if s.get("kill"):
            colour, text = RED, "KILL SWITCH SET · no new orders"
        self.banner.setText(text)
        self.banner.setStyleSheet(f"background:{colour}; color:#fff; padding:5px; border-radius:4px; font-weight:bold;")
        self.setWindowTitle(f"nsealgo desk · {mode}")
        day = (s.get("realized") or 0) + (s.get("unrealized") or 0) - (s.get("costs") or 0)
        working = [o for o in s.get("orders", []) if o["status"] in ("OPEN", "NEW", "PARTIAL", "UNKNOWN")]
        c = self.cards
        c["now"].set((s.get("now") or "")[11:19])
        c["equity"].set(indian(s.get("equity"), 0))
        c["pnl"].set(money(day), pnl_colour(day))
        c["unrealized"].set(money(s.get("unrealized")), pnl_colour(s.get("unrealized")))
        c["realized"].set(money(s.get("realized")), pnl_colour(s.get("realized")))
        c["exposure"].set(indian(s.get("exposure"), 0))
        c["regime"].set(str(s.get("regime") or "–"))
        c["positions"].set(str(len(s.get("positions", []))))
        c["orders"].set(str(len(working)), BLUE if working else TEXT)
        c["instruments"].set(indian(s.get("instruments")))
        pos = s.get("positions", [])
        fill(self.positions, ["Strategy", "Symbol", "Product", "Qty", "Avg", "LTP", "Unrealized", "Since"],
             [[p["strategy"], p["key"].split(":")[-1], p["product"], p["qty"], indian(p["avg"], 2), indian(p["ltp"], 2),
               money(p["unrealized"]), p["since"]] for p in pos],
             lambda r, col: pnl_colour(pos[r]["unrealized"]) if col == 6 else (GREEN if pos[r]["qty"] > 0 else RED) if col == 3 else None)
        od = s.get("orders", [])
        fill(self.orders, ["Time", "Strategy", "Symbol", "Side", "Qty", "Price", "Product", "Intent", "Status", "Filled",
                           "Avg", "Message"],
             [[o["time"], o["strategy"], o["key"].split(":")[-1], o["side"], o["qty"], indian(o["price"], 2), o["product"],
               o["intent"], o["status"], o["filled"], indian(o["avg"], 2), o["message"]] for o in od],
             lambda r, col: STATUS_COLOUR.get(od[r]["status"]) if col == 8 else (GREEN if od[r]["side"] == "BUY" else RED) if col == 3 else None)
        st = s.get("strategies", [])
        fill(self.strategies, ["Strategy", "Horizon", "Active today", "Positions", "Unrealized", "Working orders"],
             [[x["name"], x["horizon"], "yes" if x["active"] else "no", x["positions"], money(x["unrealized"]),
               x["open_orders"]] for x in st],
             lambda r, col: (GREEN if st[r]["active"] else MUTED) if col == 2 else pnl_colour(st[r]["unrealized"]) if col == 4 else None)
        al = list(reversed(s.get("alerts", [])))
        fill(self.alerts, ["Time", "Level", "Source", "Message"],
             [[a["ts"][11:19], a["level"], a["source"], a["message"]] for a in al],
             lambda r, col: LEVEL_COLOUR.get(al[r]["level"]) if col in (1, 3) else None)
        self.statusBar().showMessage(f"updated {datetime.now():%H:%M:%S}")

    def open_depth(self) -> None:
        if self.depth_window is None:
            self.depth_window = DepthWindow(self.client)
        self.depth_window.show()
        self.depth_window.raise_()
        self.depth_window.activateWindow()

    def change_day(self, day: str) -> None:
        if not day:
            return
        self.client.set_day(day)
        for w in self.strategy_windows.values():
            w.mdi.closeAllSubWindows()                    # charts of the previous day
            w.refresh()
        self.refresh()

    def _fill_strategy_menu(self) -> None:
        self.strategy_menu.clear()
        for st in self.last_status.get("strategies", []):
            act = self.strategy_menu.addAction(f"{st['name']}  ({st['horizon']}{', active' if st['active'] else ''})")
            act.triggered.connect(lambda _=False, n=st["name"]: self.open_strategy(n))

    def open_strategy(self, name: str):
        from .strategy import StrategyWindow
        w = self.strategy_windows.get(name)
        if w is None:
            w = self.strategy_windows[name] = StrategyWindow(self.client, name)
        w.show()
        w.raise_()
        w.activateWindow()
        return w

    def open_watch(self) -> None:
        if self.watch_window is None:
            self.watch_window = WatchWindow(self.client)
        self.watch_window.show()
        self.watch_window.raise_()
        self.watch_window.activateWindow()

    def confirm_kill(self) -> bool:
        return QMessageBox.question(
            self, "Kill switch", "Set the kill switch now?\n\nIt is the same switch as `nsealgo kill`: the session stops "
            "placing new orders. Remove it later with `nsealgo resume`.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def on_kill(self) -> None:
        if not self.confirm_kill():
            return
        try:
            self.client.kill()
            self.statusBar().showMessage("kill switch set")
        except Exception as ex:
            QMessageBox.warning(self, "Kill switch", f"Couldn't reach the session ({ex}).\nRun `nsealgo kill` instead.")
        self.refresh()

    def closeEvent(self, ev) -> None:
        for w in (self.depth_window, self.watch_window, *self.strategy_windows.values()):
            if w is not None:
                w.close()
        super().closeEvent(ev)


class DepthWindow(QMainWindow):
    """Deep books, a tab per connection (Fyers TBT: up to 3 connections of 5 stocks, 50 levels each)."""
    RATES = [("250 ms", 250), ("500 ms", 500), ("1 s", 1000)]

    def __init__(self, client, refresh_ms: int = 250):
        super().__init__()
        self.client, self.tiers, self.sig, self.pages = client, [], None, []
        self.setWindowTitle("nsealgo desk · market depth")
        tb = QToolBar("Depth")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.tier_box, self.rate_box = QComboBox(), QComboBox()
        self.rate_box.addItems([r[0] for r in self.RATES])
        self.cumulative, self.pause, self.info = QCheckBox("Cumulative depth"), QCheckBox("Pause"), QLabel()
        self.info.setStyleSheet(f"color: {MUTED};")
        for w in (QLabel("Tier"), self.tier_box, QLabel("Refresh"), self.rate_box, self.cumulative, self.pause, self.info):
            tb.addWidget(w)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.tier_box.currentIndexChanged.connect(lambda _: self.build())
        self.rate_box.currentIndexChanged.connect(lambda i: self.timer.setInterval(self.RATES[i][1]))
        self.tabs.currentChanged.connect(lambda _: self.refresh())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(refresh_ms)
        self.tier_timer = QTimer(self)
        self.tier_timer.timeout.connect(self.load_tiers)
        self.tier_timer.start(5000)                      # focus rotation can change which stocks are in a tier
        self.resize(1800, 980)
        self.load_tiers()
        self.refresh()

    def load_tiers(self) -> None:
        try:
            tiers = self.client.tiers()
        except Exception as ex:
            self.info.setText(f"no session data: {ex}")
            return
        names = [f"Tier {i + 1} · {t['name']} · {t['levels']} levels" for i, t in enumerate(tiers)]
        if names != [self.tier_box.itemText(i) for i in range(self.tier_box.count())]:
            cur = self.tier_box.currentIndex()
            self.tier_box.blockSignals(True)
            self.tier_box.clear()
            self.tier_box.addItems(names)
            self.tier_box.setCurrentIndex(max(0, min(cur, len(names) - 1)))
            self.tier_box.blockSignals(False)
        self.tiers = tiers
        self.build()

    def build(self) -> None:
        i = self.tier_box.currentIndex()
        tier = self.tiers[i] if 0 <= i < len(self.tiers) else None
        sig = (i, tuple(tuple(g) for g in tier["groups"])) if tier else None
        if sig == self.sig:
            return
        self.sig, keep = sig, self.tabs.currentIndex()
        self.tabs.clear()
        self.pages = []
        if not tier:
            self.tabs.addTab(QLabel("  No deep-book tier yet. Set feeds.depth_overlay.source (e.g. fyers_tbt) and start "
                                    "a session; tiers fill as positions and candidates are ranked."), "Waiting")
            return
        word = "TBT connection" if "TBT" in tier["name"] else "Group"
        for g, keys in enumerate(tier["groups"]):
            page = QWidget()
            row = QHBoxLayout(page)
            row.setSpacing(8)
            ladders = []
            for k in keys:
                lad = Ladder(int(tier["levels"]))
                lad.set_book(k, None)
                row.addWidget(lad)
                ladders.append(lad)
            row.addStretch(1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, f"{word} {g + 1} · {len(keys)} stocks")
            self.pages.append(ladders)
        self.tabs.setCurrentIndex(max(0, min(keep, len(self.pages) - 1)))

    def ladders_in_tab(self, i: int) -> list:
        return self.pages[i] if 0 <= i < len(self.pages) else []

    def refresh(self) -> None:
        if self.pause.isChecked():
            return
        lads = self.ladders_in_tab(self.tabs.currentIndex())
        if not lads:
            return
        try:
            books = self.client.books([lad.key for lad in lads])
        except Exception as ex:
            self.info.setText(f"no session data: {ex}")
            return
        for lad in lads:
            lad.set_book(lad.key, books.get(lad.key), self.cumulative.isChecked())
        self.info.setText(f"  updated {datetime.now():%H:%M:%S}")


WATCH_COLS = ["Symbol", "LTP", "Chg %", "Volume", "Bid", "Ask", "Spread bps", "Levels", "Imbalance", "OFI z", "Iceberg"]


class WatchModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows: list[dict] = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(WATCH_COLS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return WATCH_COLS[section]
        return None

    def raw(self, r: int, c: int):
        row = self.rows[r]
        s = row.get("signals") or {}
        return [row["key"].split(":", 1)[-1], row.get("ltp"), row.get("chg"), row.get("volume"), row.get("bid"),
                row.get("ask"), row.get("spread_bps"), row.get("levels"), s.get("imbalance"), s.get("ofi_z"),
                max(s.get("absorb_ask") or 0, s.get("absorb_bid") or 0) or None][c]

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        if not idx.isValid():
            return None
        r, c = idx.row(), idx.column()
        v = self.raw(r, c)
        if role == Qt.ItemDataRole.DisplayRole:
            if v is None:
                return "–"
            if c == 0:
                return v
            if c in (1, 4, 5, 6):
                return f"{v:,.2f}"
            if c == 3:
                return indian(v)
            if c in (2, 9):
                return signed(v, 2)
            if c == 8:
                return signed(v, 2)
            if c == 10:
                return f"×{v:.1f}"
            return str(v)
        if role == Qt.ItemDataRole.UserRole:                       # sort on the raw value
            return v if v is not None else (-1e18 if c else "")
        if role == Qt.ItemDataRole.ForegroundRole:
            if c == 2 and v is not None:
                return QColor(GREEN if v >= 0 else RED)
            if c in (8, 9) and v is not None and abs(v) >= (0.3 if c == 8 else 2.0):
                return QColor(GREEN if v > 0 else RED)
            if c == 10:
                return QColor(AMBER if v is not None and v >= 3 else MUTED)
            return QColor(TEXT)
        if role == Qt.ItemDataRole.TextAlignmentRole and c > 0:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def set_rows(self, rows: list[dict]) -> None:
        if rows and [r["key"] for r in rows] == [r["key"] for r in self.rows]:
            self.rows = rows
            self.dataChanged.emit(self.index(0, 0), self.index(len(rows) - 1, len(WATCH_COLS) - 1))
        else:
            self.beginResetModel()
            self.rows = rows
            self.endResetModel()


class WatchWindow(QMainWindow):
    """Every streamed instrument with its book signals; double-click a row for its ladder."""

    def __init__(self, client, refresh_ms: int = 1000):
        super().__init__()
        self.client, self.ladders = client, []
        self.setWindowTitle("nsealgo desk · market watch")
        tb = QToolBar("Watch")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.search, self.deep_only, self.info = QLineEdit(), QCheckBox("Deep books only"), QLabel()
        self.search.setPlaceholderText("filter symbols…")
        self.info.setStyleSheet(f"color: {MUTED};")
        for w in (self.search, self.deep_only, self.info):
            tb.addWidget(w)
        self.model = WatchModel()
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.proxy.setFilterKeyColumn(0)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(3, Qt.SortOrder.DescendingOrder)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(21)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.view.setFont(mono(9))
        self.view.doubleClicked.connect(self.open_ladder)
        self.setCentralWidget(self.view)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.deep_only.stateChanged.connect(lambda _: self.refresh())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(refresh_ms)
        self.resize(1250, 780)
        self.refresh()

    def refresh(self) -> None:
        try:
            rows = self.client.watch()
        except Exception as ex:
            self.info.setText(f"no session data: {ex}")
            return
        if self.deep_only.isChecked():
            rows = [r for r in rows if (r.get("levels") or 0) > 5]
        self.model.set_rows(sorted(rows, key=lambda r: r["key"]))
        self.info.setText(f"  {len(rows):,} instruments · updated {datetime.now():%H:%M:%S}")

    def key_at(self, proxy_index) -> str:
        return self.model.rows[self.proxy.mapToSource(proxy_index).row()]["key"]

    def open_ladder(self, proxy_index) -> None:
        w = LadderWindow(self.client, self.key_at(proxy_index))
        w.show()
        self.ladders.append(w)


class LadderWindow(QMainWindow):
    """One stock's book in its own window."""

    def __init__(self, client, key: str, refresh_ms: int = 300):
        super().__init__()
        self.client, self.key = client, key
        self.setWindowTitle(f"nsealgo desk · {key}")
        self.ladder = Ladder(50)
        self.setCentralWidget(self.ladder)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(refresh_ms)
        self.resize(430, 980)
        self.refresh()

    def refresh(self) -> None:
        try:
            b = self.client.books([self.key]).get(self.key)
        except Exception:
            return
        self.ladder.set_book(self.key, b)
