"""Overnight sizing (nsealgo.sizing.overnight): the water-filling allocation on hand-worked cases."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import numpy as np
import pandas as pd

from nsealgo.sizing import overnight as O


def _three():
    # net edges 50 / 30 / 7.8 bps after the 22.2 bps costs; impact slope k = 2 x 0.5 x 200 / 2e6 = 1e-4 bps per rupee
    return pd.DataFrame({"rank": [1, 2, 3], "company_id": ["A", "B", "C"], "price": [100.0, 200.0, 50.0],
                         "edge_bps": [72.2, 52.2, 30.0], "sigma_bps": [200.0] * 3, "pc_rs": [4e5] * 3, "pre_rs": [2e6] * 3})


def test_small_capital_goes_to_the_best_stock():
    r = O.allocate(_three(), 1e5, O.Params(max_weight=1.0))
    assert list(r["qty"]) == [1000, 0, 0]                       # all Rs 1 lakh in A (its cap 2 lakh is not reached)
    assert abs(r.loc[0, "impact_bps"] - 5.0) < 1e-9            # average impact = k/2 x 1e5


def test_budget_binds_equal_marginal_edge():
    r = O.allocate(_three(), 3e5, O.Params(max_weight=1.0))
    assert list(r["x_rs"]) == [2e5, 1e5, 0.0]                   # A at its post-close cap, B where its marginal = lambda
    assert r.loc[0, "limited_by"] == "post-close cap" and r.loc[1, "limited_by"] == "money shared out"   # the budget binds (lambda ~20 bps)


def test_capacity_binds_cash_stays():
    r = O.allocate(_three(), 1e7, O.Params(max_weight=1.0))
    assert r["x_rs"].sum() < 5e5                                # the sessions cannot take Rs 1 crore
    assert r.loc[2, "x_rs"] == 77750.0                          # C stops where its net edge = its marginal impact


def test_negative_edge_is_never_bought():
    c = _three().assign(edge_bps=[20.0, 10.0, 0.0])
    assert O.allocate(c, 1e6)["qty"].sum() == 0


def test_capped_equal_respects_caps():
    q = O.capped_equal_arrays(np.array([100.0, 100.0, 100.0]), np.array([1e4, 1e6, 1e6]), 3e5, 3)
    assert list(q) == [100, 1450, 1450]                         # 10k in the capped one, the rest split evenly


def test_costs():
    assert abs(O.VAR_COST_BPS - 22.225) < 0.01 and abs(O.DP_RS - 10.62) < 1e-9
    assert O.costs_rs(0.0, 0.0) == 0.0


def test_top_n_only_uses_the_top_ranks():
    c = pd.concat([_three(), _three().assign(rank=4, company_id="D", edge_bps=90.0)], ignore_index=True)
    r = O.allocate(c, 3e5, O.Params(max_weight=1.0, max_stocks=2))
    assert set(r["company_id"]) == {"A", "B"}                   # D has the best edge but is rank 4


def test_whole_shares_leftover_is_spent():
    # one stock, price 3000, cap 1e5 -> 33 shares (99,000); a 0.5 lakh budget floors to 16 shares (48,000) and the
    # top-up cannot add a 17th (51,000 > 50,000); with 2 stocks the leftover of one goes to the other
    c = pd.DataFrame({"rank": [1, 2], "company_id": ["A", "B"], "price": [3000.0, 70.0], "edge_bps": [80.0, 79.0],
                      "sigma_bps": [200.0, 200.0], "pc_rs": [2e5, 2e5], "pre_rs": [1e7, 1e7]})
    r = O.allocate(c, 1e5, O.Params(max_weight=0.6))
    assert r["x_rs"].sum() > 1e5 - 70                           # less than one share of B left in cash


def test_equal_split_of_a_thin_stock_hangs_unfilled():
    c = _three().assign(pc_rs=[4e5, 4e5, 2e4])                   # C's post-close session ~Rs 20k
    e = O.expected_outcome(c, np.array([1000, 500, 2000]), O.Params())
    assert e["unfilled"] == 1e5 - 2e4 and e["filled"] == 2e5 + 2e4


def test_tradable_check(tmp_path):
    """Trade-for-trade series and the ASM / GSM list block a stock; a missing or stale list is reported."""
    import pandas as pd
    from nsealgo.sizing import overnight as O
    day = pd.Timestamp("2026-10-19")
    s = tmp_path / "surveillance.parquet"
    pd.DataFrame({"date": [pd.Timestamp("2026-10-17").date()] * 2 + [pd.Timestamp("2026-10-20").date()],
                  "company_id": ["AAA", "BBB", "CCC"], "symbol": ["AAA", "BBB", "CCC"],
                  "list": ["asm:shortterm.data", "gsm", "gsm"], "stage": ["Stage I", "II", "I"]}).to_parquet(s)
    b, ok, notes = O.tradable_check(day, ["AAA", "BBB", "CCC", "DDD", "EEE"],
                                    [None, "NSE:BBB-EQ", "NSE:CCC-EQ", "NSE:DDD-BE", "NSE:EEE-EQ"], s, None)
    assert ok and b["AAA"].startswith("ASM short term") and b["BBB"].startswith("GSM")
    assert "CCC" not in b                                   # its listing is dated after the day
    assert b["DDD"].startswith("trade-for-trade") and "EEE" not in b
    b, ok, notes = O.tradable_check(pd.Timestamp("2026-11-30"), ["AAA"], None, s, None)
    assert not ok and any("older" in n for n in notes)
    b, ok, notes = O.tradable_check(day, ["AAA"], None, tmp_path / "missing.parquet", None)
    assert not ok and b == {}
