"""Windows 11 support: exact fast IST conversion, explicit text encodings, OS integration, pool reuse."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import ast
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from nsealgo.core.clock import IST, localize_ist
from nsealgo.parallel.pool import _POOL, process_map
from nsealgo.system import fresh_dir, keep_awake, keep_awake_mode, process_snapshot

SRC = Path(__file__).resolve().parents[2] / "src" / "nsealgo"


def test_fast_ist_localisation_is_exact():
    rng = np.random.default_rng(3)
    naive = pd.Timestamp("2025-01-01") + pd.to_timedelta(rng.integers(0, 300_000_000, 5000), unit="s")
    fast, slow = localize_ist(naive), pd.DatetimeIndex(naive).tz_localize(IST)
    assert (fast == slow).all() and str(fast.tz) == "Asia/Kolkata"


def _mode(node):
    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
        return node.args[1].value
    return next((k.value.value for k in node.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)), None)


def test_every_text_read_and_write_names_its_encoding():
    """Windows defaults to the ANSI code page: an unnamed encoding crashes on the rupee sign."""
    bad = []
    for f in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            fn, kws = node.func, {k.arg for k in node.keywords}
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
            if "encoding" in kws:
                continue
            if name in ("read_text", "write_text"):
                bad.append(f"{f.relative_to(SRC)}:{node.lineno}")
            elif name == "open" and isinstance(fn, ast.Name) and "b" not in (_mode(node) or "r"):
                bad.append(f"{f.relative_to(SRC)}:{node.lineno}")
            elif (name == "open" and isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                  and fn.value.id == "gzip" and "t" in (_mode(node) or "rb")):
                bad.append(f"{f.relative_to(SRC)}:{node.lineno}")
    assert not bad, f"text I/O without encoding=: {bad}"


def test_keep_awake_and_modes_are_safe_on_any_os():
    with keep_awake() as active:
        assert active is (sys.platform == "win32") or active is False
    assert keep_awake_mode({}) == "session"
    assert keep_awake_mode({"system": {"keep_awake": False}}) == "off"
    assert keep_awake_mode({"system": {"keep_awake": "always"}}) == "always"


def test_fresh_dir_empties_and_recreates(tmp_path):
    d = tmp_path / "run"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "x.txt").write_text("1", encoding="utf-8")
    assert fresh_dir(d) == d and d.is_dir() and not any(d.iterdir())
    assert fresh_dir(tmp_path / "new").is_dir()


def test_pool_is_reused_and_workers_run_below_normal():
    a = process_map(process_snapshot, range(6), 2)
    ex = _POOL["ex"]
    b = process_map(process_snapshot, range(6), 2)
    assert _POOL["ex"] is ex                                   # second call spawned no new workers
    pids = set(getattr(ex, "_processes", {}))
    assert {x["pid"] for x in a + b} <= pids
    if sys.platform != "win32":
        assert all(x["priority"] >= 10 for x in a + b)          # below_normal == nice 10 on POSIX
