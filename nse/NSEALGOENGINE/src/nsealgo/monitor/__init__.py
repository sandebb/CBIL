"""Read-only monitoring of a running session for the trading desk (nsealgo desk): snapshot.py builds the views,
server.py serves them on 127.0.0.1 with a per-session token."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
from .server import MonitorServer, start_monitor
from .snapshot import EngineSnapshot

__all__ = ["EngineSnapshot", "MonitorServer", "start_monitor"]
