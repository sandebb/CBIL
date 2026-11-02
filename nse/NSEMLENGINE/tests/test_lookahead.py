"""No look-ahead: every feature on day t must be identical whether or not data after t exists.
Run:  python -m pytest tests  (or python tests/test_lookahead.py)"""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import datetime as dt
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nseml import data, features as FT  # noqa: E402


def test_features_do_not_see_the_future():
    cut = dt.datetime(2024, 6, 28)
    ids = (data.load_raw("2024-01-01").group_by("company_id").agg((pl.col("close") * pl.col("volume")).median().alias("tv"))
               .sort("tv", descending=True)["company_id"].head(60).to_list())
    kw = dict(companies=ids, start="2023-01-01", warmup_start="2021-06-01", out=False, log=lambda m: None)
    full = FT.build(**kw).filter(pl.col("datetime") <= cut)
    trunc = FT.build(end=cut, **kw)
    assert full.height == trunc.height > 0
    a = full.sort("company_id", "datetime").select(FT.FEATURES)
    b = trunc.sort("company_id", "datetime").select(FT.FEATURES)
    bad = [c for c in FT.FEATURES if not a[c].equals(b[c])]
    assert not bad, f"features that change when the future is removed: {bad}"


def test_targets_are_next_day():
    ids = ["RELIANCE", "INFY"]
    d = FT.build(companies=ids, start="2024-01-01", warmup_start="2022-06-01", out=False, log=lambda m: None)
    r = d.filter(pl.col("company_id") == "RELIANCE").sort("datetime")
    import math
    row = r.row(10, named=True); nxt = r.row(11, named=True)
    assert abs(row["y_r"] - math.log(nxt["close"] / row["close"])) < 1e-5
    assert abs(row["y_g"] - math.log(nxt["open"] / row["close"])) < 1e-5
    assert row["y_u"] >= 0 and row["y_d"] >= 0





def test_deeplob_windows_are_causal():
    """A window ending on row i holds only rows <= i of the same stock, and the network's output for
    a window does not change when later rows of the panel change."""
    import numpy as np
    from nseml import deep
    d = FT.build(companies=["RELIANCE", "INFY", "TCS"], start="2024-01-01", warmup_start="2022-06-01",
                 out=False, log=lambda m: None)
    P = deep.Panel(d)
    rows = np.arange(len(P.dates))
    idx, mask = deep.window_index(P.start, rows, 60)
    assert (idx <= rows[:, None]).all()                                   # never a later row
    assert (P.start[idx] == P.start[rows][:, None]).all()                  # never another stock
    try:
        import torch
    except ImportError:
        return
    import yaml
    from nseml.config import cfg
    m = deep.build_model(P.X.shape[1], cfg()["deeplob"]).eval()
    W = 60
    i = rows[P.start == 0][-30]                                           # a row with 29 later rows behind it
    x = torch.from_numpy(P.X[idx[i]][None]); mk = torch.from_numpy(mask[i].astype(np.float32)[None])
    before = m(x, mk)
    P.X[i + 1:] = 99.0                                                    # change every later row
    idx2, mask2 = deep.window_index(P.start, np.array([i]), W)
    after = m(torch.from_numpy(P.X[idx2[0]][None]), torch.from_numpy(mask2[0].astype(np.float32)[None]))
    assert torch.allclose(before, after)


if __name__ == "__main__":
    test_targets_are_next_day(); test_features_do_not_see_the_future(); test_deeplob_windows_are_causal()
    print("look-ahead tests passed")
