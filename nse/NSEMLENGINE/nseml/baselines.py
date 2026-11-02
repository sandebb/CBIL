"""Baselines the deep model must beat (research design, "Validation, baselines and metrics").

  naive   unconditional quantiles of the RAW targets in the training window (no volatility scaling):
          "tomorrow looks like an average day for an average stock"
  ewma    unconditional quantiles of the volatility-SCALED targets: the same, but each stock's band is
          stretched by its own EWMA volatility - the classic volatility-model baseline
  lgbm    LightGBM quantile regression on the features_v2 inputs, one model per target and quantile,
          early-stopped on the validation window. Usually the hardest baseline on daily data.

Every model returns predictions in model units (see metrics.py) and never crosses its quantiles.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import time
import warnings

import numpy as np
import polars as pl

from .features import FEATURES, SCALED
from .metrics import MODEL_TARGETS, qcol

KEYS = ["company_id", "datetime"]


def _sorted(pred: pl.DataFrame, quantiles) -> pl.DataFrame:
    """Sort each target's quantiles row-wise (no crossing); wick targets cannot be negative."""
    cols = {}
    for t in MODEL_TARGETS:
        a = np.sort(np.column_stack([pred[qcol(t, q)].to_numpy() for q in quantiles]), axis=1)
        if t in ("y_u_n", "y_d_n"):
            a = np.maximum(a, 0)
        for j, q in enumerate(quantiles):
            cols[qcol(t, q)] = a[:, j]
    return pred.select(KEYS).with_columns([pl.Series(k, v, dtype=pl.Float32) for k, v in cols.items()])


class Naive:
    name = "naive"

    def fit(self, train, val, quantiles, log=print):
        self.q = quantiles
        raw = {v: k for k, v in SCALED.items()}            # model target -> raw target
        self.levels = {t: np.nanquantile(train[raw[t]].to_numpy(), quantiles) for t in MODEL_TARGETS}
        return self

    def predict(self, test):
        s = test["sigma"].to_numpy()
        cols = {}
        for t in MODEL_TARGETS:
            for q, lv in zip(self.q, self.levels[t]):
                cols[qcol(t, q)] = np.full(test.height, lv) / (1.0 if t == "y_v" else s)
        return _sorted(test.select(KEYS).with_columns([pl.Series(k, v) for k, v in cols.items()]), self.q)


class EWMA(Naive):
    name = "ewma"

    def fit(self, train, val, quantiles, log=print):
        self.q = quantiles
        self.levels = {t: np.nanquantile(train[t].to_numpy(), quantiles) for t in MODEL_TARGETS}
        return self

    def predict(self, test):
        cols = {qcol(t, q): np.full(test.height, lv) for t in MODEL_TARGETS for q, lv in zip(self.q, self.levels[t])}
        return _sorted(test.select(KEYS).with_columns([pl.Series(k, v) for k, v in cols.items()]), self.q)


class LGBM:
    name = "lgbm"

    def __init__(self, params: dict, seed: int = 7):
        self.p, self.seed = params, seed

    def fit(self, train, val, quantiles, log=print):
        import lightgbm as lgb
        self.q, self.models, self.best = quantiles, {}, {}
        p = self.p
        rng = np.random.default_rng(self.seed)
        tr = train if train.height <= p["max_train_rows"] else train[np.sort(rng.choice(train.height, p["max_train_rows"], replace=False))]
        va = val if val.height <= p["max_val_rows"] else val[np.sort(rng.choice(val.height, p["max_val_rows"], replace=False))]
        Xtr_all, Xva_all = tr.select(FEATURES).to_numpy().astype(np.float32), va.select(FEATURES).to_numpy().astype(np.float32)
        base = dict(boosting_type="gbdt", num_leaves=p["num_leaves"], learning_rate=p["learning_rate"],
                    min_child_samples=p["min_child_samples"], colsample_bytree=p["feature_fraction"],
                    subsample=p["bagging_fraction"], subsample_freq=p["bagging_freq"], max_bin=p["max_bin"],
                    n_estimators=p["n_estimators"], n_jobs=p["num_threads"] or -1, verbose=-1, random_state=self.seed)
        for t in MODEL_TARGETS:
            ytr, yva = tr[t].to_numpy(), va[t].to_numpy()
            ktr, kva = np.isfinite(ytr), np.isfinite(yva)      # e.g. y_v is missing when the 20-day volume is
            Xtr, Xva, ytr, yva = Xtr_all[ktr], Xva_all[kva], ytr[ktr], yva[kva]
            for q in quantiles:
                t0 = time.time()
                m = lgb.LGBMRegressor(objective="quantile", alpha=q, **base)
                with warnings.catch_warnings():   # lightgbm >= 4.7 renamed eval_set; the old name works everywhere
                    warnings.filterwarnings("ignore", message=".*eval_set.*")
                    m.fit(Xtr, ytr, eval_set=[(Xva, yva)], eval_metric="quantile",
                          callbacks=[lgb.early_stopping(p["early_stopping"], verbose=False)])
                self.models[(t, q)] = m
                self.best[(t, q)] = m.best_iteration_
                log(f"      lgbm {t} q{q:.2f}: {m.best_iteration_} trees ({time.time() - t0:.0f}s)")
        return self

    def predict(self, test):
        X = test.select(FEATURES).to_numpy().astype(np.float32)
        cols = {qcol(t, q): m.predict(X, num_iteration=m.best_iteration_) for (t, q), m in self.models.items()}
        return _sorted(test.select(KEYS).with_columns([pl.Series(k, v) for k, v in cols.items()]), self.q)

    def importance(self) -> pl.DataFrame:
        rows = []
        for (t, q), m in self.models.items():
            gain = m.booster_.feature_importance(importance_type="gain")
            rows += [{"target": t, "q": q, "feature": f, "gain": float(gv)} for f, gv in zip(FEATURES, gain)]
        return pl.DataFrame(rows)


def make(name: str, cfg: dict):
    return {"naive": Naive, "ewma": EWMA}[name]() if name != "lgbm" else LGBM(cfg["lgbm"])
