"""The morning sale of the overnight trade (nsealgo.sizing.overnight_exit)."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import datetime as dt

import numpy as np
import pandas as pd

from nsealgo.sizing import overnight_exit as X


def _plan(tmp_path):
    p = pd.DataFrame({"rank": [1, 2, 3, 4], "company_id": ["AAA", "BBB", "CCC", "DDD"],
                      "tradingsymbol": ["AAA-EQ", "BBB-EQ", "CCC-EQ", "DDD-EQ"], "qty": [10, 20, 30, 0],
                      "price": [100.0, 200.0, 50.0, 10.0], "open_p50": [101.0, 202.0, 51.0, 10.0],
                      "high_p50": [104.0, 206.0, 52.0, 11.0], "close_p50": [102.0, 201.0, 52.5, 10.0]})
    f = tmp_path / "overnight_sizing_2026-10-16.csv"
    p.to_csv(f, index=False)
    return f


def _preopen(tmp_path, day="2026-10-19"):
    d = tmp_path / "preopen"
    d.mkdir()
    s = pd.Timestamp(f"{day} 09:06:40")
    pd.DataFrame({"snap": [s - pd.Timedelta(seconds=30), s, s, s], "company_id": ["AAA", "AAA", "BBB", "CCC"],
                  "iep": [100.5, 101.0, 202.0, 52.6], "final_price": [np.nan] * 4}).to_parquet(d / f"{day}.parquet")
    return d


def test_rule(tmp_path):
    bp = _plan(tmp_path)
    e, s = X.plan_exit(tmp_path, _preopen(tmp_path), day="2026-10-19", buy_plan=bp)
    e = e.set_index("company_id")
    assert "DDD" not in e.index                                 # not bought
    a = e.loc["AAA"]                                            # latest IEP 101, pred close 102 > 101 -> hold
    assert a["action"] == "LIMIT" and a["open"] == 101.0
    assert abs(a["limit_price"] - 103.25) < 1e-9                # 101 + 0.75 x 3 = 103.25 (on the tick)
    assert e.loc["BBB", "action"] == "AUCTION"                  # pred close 201 <= open 202 -> fades
    assert e.loc["CCC", "action"] == "AUCTION"                  # close 52.5 < open 52.6
    assert s["n_limit"] == 1 and s["n_auction"] == 2


def test_typed_price_and_tick(tmp_path):
    bp = _plan(tmp_path)
    e, _ = X.plan_exit(tmp_path, tmp_path / "none", day="2026-10-19", buy_plan=bp, prices={"BBB": 199.99, "AAA": 100.01})
    e = e.set_index("company_id")
    assert e.loc["BBB", "action"] == "LIMIT" and e.loc["BBB", "price_from"] == "typed"
    assert abs(e.loc["AAA", "limit_price"] - 103.05) < 1e-9     # 100.01 + 0.75 x 3.99 = 103.0025 -> up to 103.05
    assert e.loc["CCC", "action"] == "AUCTION"                  # no price -> auction
    o = X.orders(e.reset_index())
    assert set(o["side"]) == {"S"} and (o.loc[o["price_type"] == "MKT", "price"] == 0).all()


def test_latest_plan_and_thresholds(tmp_path):
    _plan(tmp_path)
    assert X.latest_buy_plan(tmp_path, pd.Timestamp("2026-10-19")).name == "overnight_sizing_2026-10-16.csv"
    t = X.thresholds(pd.read_csv(tmp_path / "overnight_sizing_2026-10-16.csv"))
    assert list(t["company_id"]) == ["AAA", "BBB", "CCC"] and list(t["hold_below"]) == [102.0, 201.0, 52.5]
