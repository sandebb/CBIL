"""Theme, number formatting and the depth ladder."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

from datetime import datetime, timezone

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHeaderView, QLabel, QStyledItemDelegate, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

GREEN, RED, AMBER, BLUE, MUTED, TEXT = "#22c55e", "#ef4444", "#f59e0b", "#60a5fa", "#8b95a5", "#d9dee5"
STYLE = """
QWidget { background: #0f1218; color: #d9dee5; font-size: 10pt; }
QMainWindow::separator { background: #1f2530; }
QToolBar { background: #141922; border: none; border-bottom: 1px solid #222936; spacing: 6px; padding: 4px; }
QPushButton { background: #1d2430; border: 1px solid #2c3544; border-radius: 4px; padding: 5px 12px; }
QPushButton:hover { background: #253043; }
QPushButton#kill { background: #7f1d1d; border-color: #b91c1c; color: #fff; font-weight: bold; }
QPushButton#kill:hover { background: #991b1b; }
QPushButton#primary { background: #1e3a5f; border-color: #2f5d8f; font-weight: bold; }
QPushButton:disabled, QPushButton#kill:disabled, QPushButton#primary:disabled { background: #141820; color: #4b5563;
                                                                                border-color: #1f2530; }
QTabWidget::pane { border: 1px solid #222936; }
QTabBar::tab { background: #151a23; padding: 6px 14px; border: 1px solid #222936; border-bottom: none; }
QTabBar::tab:selected { background: #1d2430; color: #fff; }
QTableWidget, QTableView { background: #0f1218; gridline-color: #1a202a; border: none;
                           alternate-background-color: #121720; }
QHeaderView::section { background: #151a23; color: #8b95a5; border: none; border-bottom: 1px solid #222936;
                       padding: 3px 6px; font-size: 9pt; }
QFrame#card, QFrame#ladder { background: #131821; border: 1px solid #222936; border-radius: 6px; }
QLabel#cardTitle { color: #8b95a5; font-size: 8pt; }
QLabel#cardValue { font-size: 13pt; font-weight: bold; }
QLineEdit, QComboBox { background: #151a23; border: 1px solid #2c3544; border-radius: 4px; padding: 3px 6px; }
QScrollArea { border: none; }
QLabel { background: transparent; }
QCheckBox { background: transparent; spacing: 6px; }
QCheckBox::indicator { width: 13px; height: 13px; border: 1px solid #4a5568; border-radius: 3px; background: #151a23; }
QCheckBox::indicator:checked { background: #3b82f6; border-color: #60a5fa; }
"""


def mono(size: int = 9, bold: bool = False) -> QFont:
    f = QFont("Consolas")
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSize(size)
    f.setBold(bold)
    return f


def indian(x, decimals: int = 0) -> str:
    """12,34,567.89 (lakh/crore grouping)."""
    if x is None:
        return "–"
    neg, x = x < 0, abs(float(x))
    whole, dot, frac = f"{x:.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, tail, groups = whole[:-3], whole[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return ("-" if neg else "") + whole + (dot + frac if decimals else "")


def signed(x, decimals: int = 2, suffix: str = "") -> str:
    return "–" if x is None else f"{x:+.{decimals}f}{suffix}"


def money(x, decimals: int = 0) -> str:
    """Signed amount with lakh/crore grouping: +12,34,567."""
    return "–" if x is None else ("+" if x >= 0 else "-") + indian(abs(x), decimals)


def age_ms(ts: str | None) -> float | None:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() * 1000.0
    except (TypeError, ValueError):
        return None


class BarDelegate(QStyledItemDelegate):
    """Paints a depth bar behind the quantity: bids grow leftwards in green, asks rightwards in red."""
    FRAC, SIDE = Qt.ItemDataRole.UserRole, Qt.ItemDataRole.UserRole + 1

    def paint(self, painter, option, index):
        frac = index.data(self.FRAC) or 0.0
        if frac > 0:
            r = option.rect
            w = max(1, int((r.width() - 2) * min(float(frac), 1.0)))
            if index.data(self.SIDE) == "bid":
                bar, col = QRect(r.right() - w, r.top() + 2, w, r.height() - 4), QColor(34, 197, 94, 64)
            else:
                bar, col = QRect(r.left() + 1, r.top() + 2, w, r.height() - 4), QColor(239, 68, 68, 64)
            painter.fillRect(bar, col)
        super().paint(painter, option, index)


class Ladder(QFrame):
    """One stock's order book: up to 50 levels a side, with quantity bars, order counts and book signals."""
    COLS = ["Ord", "Bid qty", "Bid", "Ask", "Ask qty", "Ord"]

    def __init__(self, levels: int = 50, parent=None):
        super().__init__(parent)
        self.setObjectName("ladder")
        self.key, self.levels = None, levels
        self.title, self.stats, self.flags = QLabel(), QLabel(), QLabel()
        self.title.setFont(mono(11, True))
        for lab in (self.title, self.flags):
            lab.setTextFormat(Qt.TextFormat.RichText)
        self.stats.setWordWrap(True)
        self.stats.setFont(mono(8))
        self.stats.setStyleSheet(f"color: {MUTED};")
        self.flags.setFont(mono(8, True))
        self.table = QTableWidget(levels, 6)
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(17)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        self.table.setFont(mono(9))
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        for c, w in enumerate((32, 62, 74, 74, 62, 32)):     # prices need the room; order counts don't
            self.table.setColumnWidth(c, w)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.delegate = BarDelegate(self.table)
        for c in (1, 4):
            self.table.setItemDelegateForColumn(c, self.delegate)
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        left = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        centre = Qt.AlignmentFlag.AlignCenter
        colour = [MUTED, TEXT, GREEN, RED, TEXT, MUTED]
        align = [right, right, centre, centre, left, left]
        for r in range(levels):
            for c in range(6):
                it = QTableWidgetItem("")
                it.setTextAlignment(align[c])
                it.setForeground(QColor(colour[c]))
                if c in (1, 4):
                    it.setData(BarDelegate.SIDE, "bid" if c == 1 else "ask")
                if r == 0:
                    it.setFont(mono(9, True))
                self.table.setItem(r, c, it)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(2)
        for w in (self.title, self.stats, self.flags, self.table):
            lay.addWidget(w)
        self.setMinimumWidth(372)
        self.setMaximumWidth(372)

    def set_book(self, key: str, b: dict | None, cumulative: bool = False) -> None:
        self.key = key
        sym = key.split(":", 1)[-1]
        if not b:
            self.title.setText(f"{sym}  waiting for data…")
            return
        ltp, pc = b.get("ltp"), b.get("prev_close")
        chg = (ltp / pc - 1) * 100 if ltp and pc else None
        col = GREEN if (chg or 0) >= 0 else RED
        self.title.setText(f"<span style='color:{TEXT}'>{sym}</span>&nbsp;&nbsp;<span style='color:{col}'>"
                           f"{indian(ltp, 2)}&nbsp;&nbsp;{signed(chg, 2, '%')}</span>")
        s = b.get("signals") or {}
        age = age_ms(b.get("ts"))
        tot = (f" · buy {indian(b['tbq'])} / sell {indian(b['tsq'])}" if b.get("tbq") is not None else "")
        self.stats.setText(f"{len(b.get('bids') or [])} lv · spread {s.get('spread_bps', '–')} bps · imb "
                           f"{signed(s.get('imbalance'), 2)} · OFI z {signed(s.get('ofi_z'), 1)}{tot}"
                           + (f" · {age:,.0f} ms" if age is not None and age >= 0 else ""))
        flags = []
        if (s.get("absorb_ask") or 0) >= 3:
            flags.append(f"<span style='color:{RED}'>ICEBERG ASK ×{s['absorb_ask']:.1f}</span>")
        if (s.get("absorb_bid") or 0) >= 3:
            flags.append(f"<span style='color:{GREEN}'>ICEBERG BID ×{s['absorb_bid']:.1f}</span>")
        if age is not None and age > 2000:
            flags.append(f"<span style='color:{AMBER}'>STALE {age / 1000:.1f}s</span>")
        self.flags.setText("&nbsp;&nbsp;".join(flags) or "&nbsp;")
        bids, asks = b.get("bids") or [], b.get("asks") or []
        qb, qa = [x[1] for x in bids], [x[1] for x in asks]
        if cumulative:
            qb = [sum(qb[:i + 1]) for i in range(len(qb))]
            qa = [sum(qa[:i + 1]) for i in range(len(qa))]
        top = max(qb + qa + [1])
        t = self.table
        for r in range(self.levels):
            bl = bids[r] if r < len(bids) else None
            al = asks[r] if r < len(asks) else None
            vals = [indian(bl[2]) if bl and len(bl) > 2 else "", indian(bl[1]) if bl else "",
                    f"{bl[0]:,.2f}" if bl else "", f"{al[0]:,.2f}" if al else "", indian(al[1]) if al else "",
                    indian(al[2]) if al and len(al) > 2 else ""]
            for c, v in enumerate(vals):
                t.item(r, c).setText(v)
            t.item(r, 1).setData(BarDelegate.FRAC, (qb[r] / top) if bl else 0.0)
            t.item(r, 4).setData(BarDelegate.FRAC, (qa[r] / top) if al else 0.0)
