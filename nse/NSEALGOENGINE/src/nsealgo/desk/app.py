"""Start the desk: nsealgo desk [--mode live|forward] [--demo]."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import sys


def run_desk(client) -> int:
    from PySide6.QtWidgets import QApplication

    from .widgets import STYLE
    from .windows import MainWindow
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("nsealgo desk")
    app.setStyleSheet(STYLE)
    w = MainWindow(client)
    w.resize(1440, 880)
    w.show()
    return app.exec()
