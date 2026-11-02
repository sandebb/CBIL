"""Runtime-only third-party dependency auto-installer for the NSEUIENGINE monorepo."""
from __future__ import annotations

import builtins
import importlib
import os
import subprocess
import sys
from pathlib import Path

PACKAGE_MAP: dict[str, str] = {
    "yaml": "PyYAML>=6.0",
    "polars": "polars>=1.21",
    "pandas": "pandas>=2.1",
    "numpy": "numpy>=1.26",
    "pyarrow": "pyarrow>=14.0",
    "scipy": "scipy>=1.11",
    "sklearn": "scikit-learn>=1.3",
    "lightgbm": "lightgbm>=4.0",
    "torch": "torch>=2.4",
    "statsmodels": "statsmodels>=0.14",
    "hmmlearn": "hmmlearn>=0.3",
    "threadpoolctl": "threadpoolctl>=3.1",
    "psutil": "psutil>=5.9",
    "requests": "requests>=2.31",
    "urllib3": "urllib3>=2.0",
    "websocket": "websocket-client>=1.6",
    "fyers_apiv3": "fyers-apiv3>=3.1",
    "yfinance": "yfinance>=0.2.40",
    "bs4": "beautifulsoup4>=4.12",
    "lxml": "lxml>=5.0",
    "openpyxl": "openpyxl>=3.1",
    "xlsxwriter": "XlsxWriter>=3.1",
    "pyotp": "pyotp>=2.9",
    "cryptography": "cryptography>=42.0",
    "typer": "typer>=0.12",
    "rich": "rich>=13.7",
    "click": "click>=8.1",
    "plotly": "plotly>=5.20",
    "matplotlib": "matplotlib>=3.8",
    "PySide6": "PySide6>=6.7",
    "pyqtgraph": "pyqtgraph>=0.13",
    "duckdb": "duckdb>=1.0",
    "joblib": "joblib>=1.3",
    "tqdm": "tqdm>=4.66",
    "dotenv": "python-dotenv>=1.0",
    "dateutil": "python-dateutil>=2.8",
    "pytz": "pytz>=2024.1",
    "tzdata": "tzdata>=2024.1",
    "tabulate": "tabulate>=0.9",
    "jinja2": "Jinja2>=3.1",
}

_ATTEMPTED: set[str] = set()
_ORIG_IMPORT = builtins.__import__


def _setup_monorepo_sys_path() -> None:
    nse_root = os.environ.get("NSE_ROOT", "").strip()
    if nse_root:
        code_dir = Path(nse_root) / "code"
    else:
        here = Path(__file__).resolve()
        code_dir = next((p for p in here.parents if p.name == "code"), here.parent)
    if not code_dir.is_dir():
        return
    candidates = [
        code_dir,
        code_dir / "NSEDATAENGINE",
        code_dir / "NSEMLENGINE",
        code_dir / "NSEALGOENGINE" / "src",
        code_dir / "NSEUIENGINE",
        code_dir / "Fyerstbt",
        code_dir / "scripts" / "pipeline",
        code_dir / "scripts" / "validation",
    ]
    for p in candidates:
        s = str(p)
        if p.is_dir() and s not in sys.path:
            sys.path.insert(0, s)


def _auto_import(name, globals=None, locals=None, fromlist=(), level=0):
    try:
        return _ORIG_IMPORT(name, globals, locals, fromlist, level)
    except ModuleNotFoundError as exc:
        if level != 0 or os.environ.get("NSE_DISABLE_AUTO_INSTALL") == "1":
            raise
        top_pkg = (getattr(exc, "name", None) or name).split(".")[0]
        if not top_pkg or top_pkg.startswith("_") or top_pkg in _ATTEMPTED:
            raise
        if top_pkg in {
            "pip",
            "setuptools",
            "pkg_resources",
            "wheel",
            "distutils",
            "sitecustomize",
            "usercustomize",
            "runtime_deps",
            "nse_contract",
            "nseml",
            "nsealgo",
            "fyers_token",
        }:
            raise
        pip_spec = PACKAGE_MAP.get(top_pkg)
        if not pip_spec:
            raise
        _ATTEMPTED.add(top_pkg)
        sys.stderr.write(
            f"[runtime-deps] Installing missing library '{pip_spec}' into {sys.executable} ...\n"
        )
        sys.stderr.flush()
        try:
            subprocess.check_call(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-warn-conflicts",
                    "--quiet",
                    pip_spec,
                ]
            )
            importlib.invalidate_caches()
        except Exception as install_err:
            sys.stderr.write(
                f"[runtime-deps] Warning: auto-install of '{pip_spec}' failed ({install_err}).\n"
            )
            raise exc
        return _ORIG_IMPORT(name, globals, locals, fromlist, level)


def ensure_package(module_name: str) -> None:
    top_pkg = str(module_name).split(".")[0].strip()
    if not top_pkg:
        return
    _auto_import(top_pkg)


def ensure_packages(*module_names: str) -> None:
    for mod in module_names:
        ensure_package(mod)


def install_hook() -> None:
    _setup_monorepo_sys_path()
    if getattr(builtins.__import__, "__name__", "") != "_auto_import":
        builtins.__import__ = _auto_import


install_hook()