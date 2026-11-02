"""nsealgo desk: a PySide6 trading-desk GUI over a running session's monitor (or synthetic demo data).
Read-only apart from the kill switch. Install: pip install -e .[desk]"""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
