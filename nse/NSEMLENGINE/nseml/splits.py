"""Walk-forward folds on trading days (never by stock).

   |------------- train -------------| gap |-- val --| gap |-- test --|
                                                                         -> next fold: + step_days

A row on day t carries a target from day t+1, so the `gap` (>= 1) also keeps every training label
strictly before the validation window, and validation labels before the test window.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .config import cfg

TRADING_DAYS_PER_YEAR = 248


@dataclass(frozen=True)
class Fold:
    k: int
    train: tuple
    val: tuple
    test: tuple

    def label(self) -> str:
        return f"f{self.k:02d}_{self.test[0]:%Y%m%d}"


def make_folds(days: list, wf: dict | None = None) -> list[Fold]:
    wf = wf or cfg()["walk_forward"]
    days = sorted(days)
    gap, nv, nt, step = wf["gap_days"], wf["val_days"], wf["test_days"], wf["step_days"]
    ntr = int(wf["train_years"] * TRADING_DAYS_PER_YEAR)
    first = dt.datetime.fromisoformat(str(wf["first_test"]))
    i = next(j for j, d in enumerate(days) if d >= first)
    out, k = [], 0
    while i < len(days):
        te = (i, min(i + nt, len(days)) - 1)
        if te[1] - te[0] + 1 < 20:                      # a stub of < 20 days is not worth a fold
            break
        va = (i - gap - nv, i - gap - 1)
        tr_end = va[0] - gap - 1
        tr_start = 0 if wf["mode"] == "expanding" else max(0, tr_end - ntr + 1)
        if va[0] < 0 or tr_end - tr_start < TRADING_DAYS_PER_YEAR:
            i += step
            continue
        out.append(Fold(k, (days[tr_start], days[tr_end]), (days[va[0]], days[va[1]]), (days[te[0]], days[te[1]])))
        k += 1
        i += step
    return out
