import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import logging
import warnings
from datetime import date
from pathlib import Path

import pytest

warnings.filterwarnings("ignore")
logging.getLogger("nsealgo").setLevel(logging.WARNING)


@pytest.fixture(scope="session")
def synth(tmp_path_factory):
    """A small synthetic market shared by the end-to-end tests."""
    from nsealgo.backtest.runner import load_master
    from nsealgo.cli import demo_overrides
    from nsealgo.config import deep_merge, load_config
    from nsealgo.data.synthetic import build_synthetic
    root = tmp_path_factory.mktemp("synth")
    info = build_synthetic(root, end=date(2026, 9, 25), days=420, minute_days=18, n_stocks=32, seed=3)
    cfg = deep_merge(load_config(None), demo_overrides(root))
    cfg["_root"] = str(root)
    cfg["regime"]["mode"] = "sma200"          # fast; the HMM has its own tests
    master = load_master(str(Path(root) / "data" / "synthetic_master.csv"), cfg)
    return {"root": root, "info": info, "cfg": cfg, "master": master}
