"""`python -m nsealgo ...` (guarded entry point: required for worker processes on Windows)."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
from multiprocessing import freeze_support

from .cli import app

if __name__ == "__main__":
    freeze_support()
    app()
