"""Configuration and paths.

No path is hard-coded: the project root, the work folder and the data project come from environment
variables (defaults in brackets), and every NSEDATAENGINE file and column is resolved through that
project's contract (nse_contract.py + contract\\schema.yaml), which is imported from NSEDATAENGINE itself.

  NSEMLENGINE_HOME      [folder that contains nseml\\]
  NSEMLENGINE_WORK_DIR  [%NSE_DATA_ROOT%\\NSEMLENGINE when the shared data area is in use, else %NSEMLENGINE_HOME%\\work]
  NSEDATAENGINE_HOME    [%NSEMLENGINE_HOME%\\..\\NSEDATAENGINE]
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import importlib
import os
import sys
from functools import lru_cache
from pathlib import Path

import yaml

PKG_DIR = Path(__file__).resolve().parent


def _env(name: str) -> str:
    return os.environ.get(name, "").strip().strip('"')


def nse_root() -> Path:
    env = _env("NSE_ROOT")
    if env and Path(env).is_dir():
        return Path(env).resolve()
    for parent in PKG_DIR.parents:
        if (parent / "code").is_dir() and (parent / "data").is_dir():
            return parent
    return PKG_DIR.parents[2]

def home() -> Path:
    return Path(_env("NSEMLENGINE_HOME") or (nse_root() / "code" / "NSEMLENGINE")).resolve()

def work_dir() -> Path:
    alt = bool(cfg().get("extra_features"))
    folder = str(cfg().get("work_folder") or ("work_alt" if alt else "work"))
    d_root = Path(_env("NSE_DATA_ROOT") or (nse_root() / "data")).resolve()
    p = Path(_env("NSEMLENGINE_WORK_DIR") or (d_root / "NSEMLENGINE" / folder)).resolve()
    if alt and p.name == "work":
        sys.exit("the ALT model (extra_features: true) must not use the original work folder...")
    p.mkdir(parents=True, exist_ok=True)
    return p

def data_engine_home() -> Path:
    return Path(_env("NSEDATAENGINE_HOME") or (nse_root() / "code" / "NSEDATAENGINE")).resolve()


@lru_cache(maxsize=1)
def contract():
    """NSEDATAENGINE's nse_contract module (the single source of truth for files and columns)."""
    dh = data_engine_home()
    if not (dh / "nse_contract.py").exists():
        sys.exit(f"NSEDATAENGINE not found at {dh} - set NSEDATAENGINE_HOME")
    if str(dh) not in sys.path:
        sys.path.insert(0, str(dh))
    os.environ.setdefault("NSEDATAENGINE_HOME", str(dh))
    return importlib.import_module("nse_contract")


@lru_cache(maxsize=1)
def cfg() -> dict:
    p = Path(_env("NSEMLENGINE_CONFIG") or home() / "config.yaml")
    if not p.is_file():
        sys.exit(f"settings file not found: {p}\n(check NSEMLENGINE_CONFIG - clear it with:  set NSEMLENGINE_CONFIG=)")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


# file names inside the work folder (one place)
def dataset_path() -> Path:
    return work_dir() / "dataset_v2.parquet"


WF_LOCK = "WALKFORWARD_RUNNING.txt"      # in the work folder while `baselines` (a walk-forward) runs there


def wf_lock() -> Path:
    return work_dir() / WF_LOCK


def wf_running(max_age_hours: float = 12.0) -> bool:
    """True while a walk-forward runs in this work folder: its lock file exists and was touched within max_age_hours
    (it is touched after every fold; an older one is left over from a crash and ignored)."""
    import time
    p = wf_lock()
    return p.exists() and (time.time() - p.stat().st_mtime) < max_age_hours * 3600


def results_dir(name: str) -> Path:
    p = work_dir() / "results" / name
    p.mkdir(parents=True, exist_ok=True)
    return p
