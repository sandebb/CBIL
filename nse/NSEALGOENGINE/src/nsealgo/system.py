"""Operating-system integration, tuned for Windows 11 (safe no-ops elsewhere).

- keep_awake(): stop the PC sleeping during forward tests, live sessions and long backtests
  (SetThreadExecutionState); a sleeping desktop silently drops every broker WebSocket.
- disable_power_throttling(): opt the process out of Windows 11 EcoQoS / power throttling, which can
  push a minimised console's threads to low clocks or efficiency cores.
- set_priority(): backtest workers run BELOW_NORMAL so the desktop and a running live engine stay
  responsive; the live/forward engine runs ABOVE_NORMAL. HIGH for 16+ busy workers would starve
  everything else (including the live engine's network threads) without making the backtest faster.
- console_utf8(): print the rupee sign and other UTF-8 when output is redirected (Task Scheduler logs).
- fresh_dir(): empty a directory robustly on Windows, where a file held open elsewhere cannot be deleted.
- clock_offset(): local clock error against an NTP server (Windows syncs rarely by default; the engine's
  09:15 / 15:10 timers and ORB windows depend on the local clock).
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import contextlib
import ctypes
import logging
import os
import shutil
import stat
import sys
import time
from pathlib import Path

log = logging.getLogger("nsealgo.system")
IS_WINDOWS = sys.platform == "win32"

ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x00000001, 0x00000002
PROCESS_POWER_THROTTLING = 4                        # PROCESS_INFORMATION_CLASS.ProcessPowerThrottling
POWER_THROTTLING_VERSION = 1
THROTTLE_EXECUTION_SPEED, THROTTLE_IGNORE_TIMER_RESOLUTION = 0x1, 0x4


class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):   # noqa: N801  (Win32 name)
    _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]


def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True) if IS_WINDOWS else None


def disable_power_throttling() -> bool:
    """Opt this process out of EcoQoS power throttling (execution speed and timer-resolution throttling)."""
    k = _kernel32()
    if k is None:
        return False
    try:
        state = PROCESS_POWER_THROTTLING_STATE(POWER_THROTTLING_VERSION,
                                               THROTTLE_EXECUTION_SPEED | THROTTLE_IGNORE_TIMER_RESOLUTION, 0)
        ok = bool(k.SetProcessInformation(k.GetCurrentProcess(), PROCESS_POWER_THROTTLING, ctypes.byref(state),
                                          ctypes.sizeof(state)))
        if not ok:
            log.debug("SetProcessInformation(ProcessPowerThrottling) failed: %s", ctypes.get_last_error())
        return ok
    except Exception as ex:  # older Windows builds, restricted tokens
        log.debug("power throttling opt-out unavailable: %s", ex)
        return False


@contextlib.contextmanager
def keep_awake(display: bool = False, enabled: bool = True):
    """Keep the system (optionally the display) awake while the block runs; restores normal sleep after."""
    k = _kernel32() if enabled else None
    active = False
    if k is not None:
        try:
            flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | (ES_DISPLAY_REQUIRED if display else 0)
            active = bool(k.SetThreadExecutionState(flags))
        except Exception as ex:
            log.debug("SetThreadExecutionState unavailable: %s", ex)
    try:
        yield active
    finally:
        if active:
            with contextlib.suppress(Exception):
                k.SetThreadExecutionState(ES_CONTINUOUS)


_PRIORITIES = {"idle": ("IDLE_PRIORITY_CLASS", 19), "below_normal": ("BELOW_NORMAL_PRIORITY_CLASS", 10),
               "normal": ("NORMAL_PRIORITY_CLASS", 0), "above_normal": ("ABOVE_NORMAL_PRIORITY_CLASS", -5),
               "high": ("HIGH_PRIORITY_CLASS", -10)}


def set_priority(level: str | None) -> bool:
    """Scheduling priority of the current process: idle | below_normal | normal | above_normal | high.
    (No 'realtime': it can starve the OS's own input and network threads.)"""
    if not level or level not in _PRIORITIES:
        return False
    try:
        import psutil
        cls, nice = _PRIORITIES[level]
        psutil.Process().nice(getattr(psutil, cls) if IS_WINDOWS else nice)
        return True
    except Exception as ex:   # raising priority needs privileges on Linux/macOS
        log.debug("could not set priority %s: %s", level, ex)
        return False


def apply_process_policy(cfg: dict | None, role: str) -> None:
    """role: 'worker' (backtest process), 'live' (forward/live engine) or 'main' (interactive/backtest parent)."""
    sc = (cfg or {}).get("system", {}) if isinstance(cfg, dict) else {}
    if sc.get("disable_power_throttling", True):
        disable_power_throttling()
    if role == "worker":
        set_priority(sc.get("worker_priority", "below_normal"))
    elif role == "live":
        set_priority(sc.get("live_priority", "above_normal"))


def console_utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            s.reconfigure(encoding="utf-8", errors="replace")


def _force_remove(func, path, _exc):
    with contextlib.suppress(Exception):
        os.chmod(path, stat.S_IWRITE)          # read-only attribute blocks deletion on Windows
        func(path)


def fresh_dir(path: str | Path, retries: int = 5) -> Path:
    """Empty (or create) a directory. Retries briefly because antivirus/indexer scans hold files for a moment
    on Windows; if something keeps a file open (a report in a browser, a DB viewer), raise a clear error."""
    path = Path(path)
    for i in range(retries):
        if not path.exists():
            break
        shutil.rmtree(path, onerror=_force_remove)
        if not path.exists():
            break
        time.sleep(0.2 * (i + 1))
    if path.exists() and any(path.iterdir()):
        stuck = [str(p) for p in path.rglob("*") if p.is_file()][:3]
        raise RuntimeError(f"cannot clear {path}: file(s) in use by another program ({'; '.join(stuck)}). "
                           f"Close them or choose another run directory.")
    path.mkdir(parents=True, exist_ok=True)
    return path


def keep_awake_mode(cfg: dict | None) -> str:
    """'session' (default: only while a session or backtest runs), 'always' (whole daemon lifetime) or 'off'."""
    v = ((cfg or {}).get("system") or {}).get("keep_awake", "session")
    if v is True:
        return "session"
    if v in (False, None, "off", "false"):
        return "off"
    return str(v)


def clock_offset(server: str = "time.windows.com", timeout: float = 2.0) -> float | None:
    """Seconds to ADD to the local clock to match NTP (SNTP, one round trip); None if unreachable."""
    import socket
    import struct
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            t0 = time.time()
            s.sendto(b"\x1b" + 47 * b"\0", (server, 123))
            data, _ = s.recvfrom(48)
            t1 = time.time()
        if len(data) < 48:
            return None
        secs, frac = struct.unpack("!II", data[40:48])          # server transmit timestamp
        return (secs - 2208988800 + frac / 2 ** 32) - (t0 + t1) / 2
    except OSError:
        return None


def process_snapshot(_=None) -> dict:
    """pid and scheduling priority of the calling process (diagnostics; picklable for worker checks)."""
    info = {"pid": os.getpid(), "priority": None}
    with contextlib.suppress(Exception):
        import psutil
        info["priority"] = psutil.Process().nice()
    return info


def windows_checks() -> list[str]:
    """Human-readable notes for `nsealgo doctor` on Windows (empty elsewhere)."""
    if not IS_WINDOWS:
        return []
    notes = [f"UTF-8 mode: {'on' if sys.flags.utf8_mode else 'off (set PYTHONUTF8=1; scripts/windows/setup.ps1 does)'}"]
    with contextlib.suppress(Exception):
        import subprocess
        out = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True, timeout=5).stdout
        notes.append("power plan: " + (out.split("(")[-1].rstrip(")\n ") if "(" in out else out.strip()))
    notes.append("keep-awake during sessions: " + ("available" if _kernel32() is not None else "unavailable"))
    return notes
