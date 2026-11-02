"""
Read today's Fyers access token from any script on this PC (NSEALGOENGINE, NSEDATAENGINE, NSEMLENGINE, ...).

    import os, sys; sys.path.insert(0, os.environ.get("NSEUIENGINE_HOME") or os.path.join(os.environ.get("NSE_ROOT", ""), "code", "NSEUIENGINE"))
    from fyers_token import get_access_token, get_ws_token, get_client_id

    fyers = fyersModel.FyersModel(client_id=get_client_id(), token=get_access_token(), is_async=False, log_path="")
    sock  = data_ws.FyersDataSocket(access_token=get_ws_token(), ...)

Reads the Windows registry directly, so a process started BEFORE the 08:00 refresh (an open IDE or terminal) still
gets the new token - plain os.environ["FYERS_ACCESS_TOKEN"] in such a process would return yesterday's value.
No third-party dependencies.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))


class FyersTokenMissing(RuntimeError):
    pass


def _read(name: str) -> str | None:
    if os.name == "nt":
        import winreg
        for root, path in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                           (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(root, path) as k:
                    v = winreg.QueryValueEx(k, name)[0]
                    if v:
                        return str(v)
            except OSError:
                pass
    return os.environ.get(name) or None


def data_dir() -> Path:
    """NSEUIENGINE's data folder (state, reports, logs): %NSEUIENGINE_DATA_DIR%, else %NSE_DATA_ROOT%\\NSEUIENGINE
    (the shared data area, e.g. C:\\NSE\\DATA\\NSEUIENGINE), else the folder this file is in."""
    d = _read("NSEUIENGINE_DATA_DIR")
    if d:
        return Path(os.path.expandvars(d)).resolve()
    root = _read("NSE_DATA_ROOT") or (str(Path(_read("NSE_ROOT")) / "data") if _read("NSE_ROOT") else str(Path(__file__).resolve().parents[2] / "data"))
    return (Path(os.path.expandvars(root)) / "NSEUIENGINE").resolve()


def shared_dir(env_name: str, engine: str, sub: str, default: Path) -> Path:
    """Another engine's folder: its own variable, else %NSE_DATA_ROOT%\\<engine>\\<sub>, else the old default."""
    d = _read(env_name)
    if d:
        return Path(d)
    root = _read("NSE_DATA_ROOT") or (str(Path(_read("NSE_ROOT")) / "data") if _read("NSE_ROOT") else str(Path(__file__).resolve().parents[2] / "data"))
    base = Path(os.path.expandvars(root)) / engine
    return (base / sub if sub else base).resolve()
    return default


def get_client_id() -> str:
    v = _read("FYERS_CLIENT_ID")
    if not v:
        raise FyersTokenMissing("FYERS_CLIENT_ID is not set - run NSEUIENGINE fyers_token_manager.py --setup")
    return v


def get_access_token(require_today: bool = True) -> str:
    tok, day = _read("FYERS_ACCESS_TOKEN"), _read("FYERS_TOKEN_DATE")
    if not tok:
        raise FyersTokenMissing("FYERS_ACCESS_TOKEN is not set - has the 08:00 NSEUIENGINE task run?")
    today = datetime.now(IST).date().isoformat()
    if require_today and day != today:
        raise FyersTokenMissing(f"FYERS_ACCESS_TOKEN is from {day}, not today ({today}). Run: "
                                f"schtasks /run /tn NSEUIENGINE-FyersToken")
    return tok


def get_ws_token(require_today: bool = True) -> str:
    """'<app_id>:<access_token>' for FyersDataSocket, the TBT socket and the REST Authorization header."""
    return f"{get_client_id()}:{get_access_token(require_today)}"


if __name__ == "__main__":
    t = get_access_token(require_today=False)
    print(f"FYERS_ACCESS_TOKEN {t[:6]}...{t[-4:]}  date={_read('FYERS_TOKEN_DATE')}  app={_read('FYERS_CLIENT_ID')}")
