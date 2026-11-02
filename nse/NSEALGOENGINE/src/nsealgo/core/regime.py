"""Regime engine: Gaussian HMM on index returns + India VIX level, gated by a 200-DMA baseline.

Look-ahead discipline: only the FILTERED probability of the last day is used (forward algorithm
implemented here), never the smoothed posterior, which silently uses future data. States are
relabelled by return variance after every refit so 'calm/turbulent/crisis' keep their meaning.

Speed: the forward filter advances incrementally, one step per new observation, and restarts only
after a refit. Refits follow a data-driven schedule (every `refit_every` new observations), so a
backtest can fit all of its models in parallel up front (build_timeline) and still reproduce exactly
what a day-by-day run would have done.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import logging
import warnings

import numpy as np
import pandas as pd
from scipy.stats import multivariate_normal

from .indicators import logsumexp_small as _lse

log = logging.getLogger("nsealgo.regime")
LABELS = ("calm", "turbulent", "crisis")
SEVERITY = {s: i for i, s in enumerate(LABELS)}


class RegimeEngine:
    def __init__(self, cfg: dict, fitter=None):
        self.cfg = cfg
        self.k = int(cfg.get("n_states", 3))
        self.labels = LABELS[: self.k] if self.k <= 3 else tuple(f"s{i}" for i in range(self.k))
        self.params: dict | None = None
        self._fit_n: int | None = None           # observations used by the current model
        self._fitter = fitter                    # optional (n, X) -> params, e.g. precomputed in parallel
        self._cache: tuple | None = None         # (params, n_rows, log_alpha) for the incremental filter
        self.hmm_state: str | None = None
        self._pending: tuple[str | None, int] = (None, 0)
        self.state = cfg.get("default_state", "turbulent")
        self.last: dict = {"state": self.state, "probs": None, "source": "init"}

    @staticmethod
    def features(close: pd.Series, vix: pd.Series | None = None) -> pd.DataFrame:
        """Daily log return + log India VIX LEVEL (persistent, so regimes stay regimes; daily VIX changes
        made single quiet days inside volatile spells look calm). Fallback: log 20-day realised vol."""
        r = np.log(close).diff()
        if vix is not None and len(vix.dropna()) > 30:
            v = np.log(vix.reindex(close.index).ffill())
        else:
            v = np.log(r.rolling(20).std() * np.sqrt(252))
        return pd.concat([r, v], axis=1, keys=["r", "v"]).replace([np.inf, -np.inf], np.nan).dropna()

    def _fit(self, X: np.ndarray) -> dict | None:
        from hmmlearn.hmm import GaussianHMM
        logging.getLogger("hmmlearn").setLevel(logging.ERROR)
        best, best_ll = None, -np.inf
        sticky = float(self.cfg.get("sticky", 20.0))   # Dirichlet prior favouring persistent regimes
        for i in range(int(self.cfg.get("restarts", 3))):
            m = GaussianHMM(n_components=self.k, covariance_type="full", n_iter=200, tol=1e-4, min_covar=1e-6,
                            transmat_prior=1.0 + sticky * np.eye(self.k),
                            random_state=int(self.cfg.get("seed", 7)) + i)
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    m.fit(X)
                    ll = m.score(X)
            except Exception as ex:  # degenerate covariance etc.
                log.debug("HMM restart %s failed: %s", i, ex)
                continue
            if np.isfinite(ll) and ll > best_ll:
                best, best_ll = m, ll
        if best is None:
            return None
        order = np.argsort([best.covars_[i][0, 0] for i in range(self.k)])
        return {"start": best.startprob_[order], "trans": best.transmat_[np.ix_(order, order)],
                "means": best.means_[order], "covars": best.covars_[order]}

    @staticmethod
    def _logb(X: np.ndarray, p: dict) -> np.ndarray:
        return np.column_stack([multivariate_normal.logpdf(X, p["means"][j], p["covars"][j], allow_singular=True)
                                for j in range(len(p["start"]))]).reshape(len(X), -1)

    @classmethod
    def filter(cls, X: np.ndarray, p: dict) -> np.ndarray:
        """Forward algorithm: P(state_t | x_1..x_t) for every t."""
        logb = cls._logb(X, p)
        logA = np.log(p["trans"] + 1e-300)
        la = np.log(p["start"] + 1e-300) + logb[0]
        la = la - _lse(la)
        out = np.empty((len(X), len(la)))
        out[0] = np.exp(la)
        for t in range(1, len(X)):
            la = _lse(la[:, None] + logA, axis=0) + logb[t]
            la = la - _lse(la)
            out[t] = np.exp(la)
        return out

    def _filtered_last(self, X: np.ndarray) -> np.ndarray:
        p, n, c = self.params, len(X), self._cache
        logA = np.log(p["trans"] + 1e-300)
        if c is not None and c[0] is p and c[1] <= n:
            la, start = c[2], c[1]
        else:
            logb0 = self._logb(X[:1], p)[0]
            la = np.log(p["start"] + 1e-300) + logb0
            la, start = la - _lse(la), 1
        if n > start:
            logb = self._logb(X[start:n], p)
            for t in range(len(logb)):
                la = _lse(la[:, None] + logA, axis=0) + logb[t]
                la = la - _lse(la)
        self._cache = (p, n, la)
        return np.exp(la)

    def _hysteresis(self, probs: np.ndarray) -> str:
        cand = self.labels[int(np.argmax(probs))]
        cur = self.hmm_state or cand
        if cand == cur:
            self._pending = (None, 0)
            self.hmm_state = cur
            return cur
        if probs[self.labels.index(cand)] >= float(self.cfg.get("hysteresis_prob", 0.7)):
            n = self._pending[1] + 1 if self._pending[0] == cand else 1
            self._pending = (cand, n)
            if n >= int(self.cfg.get("hysteresis_days", 2)):
                self.hmm_state, self._pending = cand, (None, 0)
        else:
            self._pending = (None, 0)
        return self.hmm_state or cur

    def needs_fit(self, n: int) -> bool:
        return self.params is None or n - (self._fit_n or 0) >= int(self.cfg.get("refit_every", 5))

    def update(self, asof, close: pd.Series, vix: pd.Series | None = None, X_all: pd.DataFrame | None = None) -> dict:
        mode = self.cfg.get("mode", "both")
        cutoff = pd.Timestamp(asof)
        close = close.loc[:cutoff].dropna() if close is not None else pd.Series(dtype=float)
        sma_state = None
        if len(close) >= 200:
            sma_state = "calm" if close.iloc[-1] > close.iloc[-200:].mean() else "turbulent"
        hmm_state, probs = None, None
        if mode in ("hmm", "both") and len(close) > 30:
            if X_all is not None:
                X = X_all.iloc[: X_all.index.searchsorted(cutoff, side="right")]
            else:
                X = self.features(close, vix.loc[:cutoff] if vix is not None and len(vix) else None)
            n = len(X)
            if n >= int(self.cfg.get("min_obs", 750)):
                if self.needs_fit(n):
                    Xn = X.to_numpy()
                    p = self._fitter(n, Xn) if self._fitter else self._fit(Xn)
                    if p is not None:
                        self.params, self._fit_n = p, n
                        if p.get("_n") == n and p.get("_la") is not None:
                            self._cache = (p, n, p["_la"])      # filter state computed by the fit job
                if self.params is not None:
                    probs = self._filtered_last(X.to_numpy())
                    hmm_state = self._hysteresis(probs)
        if mode == "sma200":
            state = sma_state
        elif mode == "hmm":
            state = hmm_state
        else:
            cands = [s for s in (hmm_state, sma_state) if s]
            state = max(cands, key=lambda s: SEVERITY.get(s, 0)) if cands else None
        source = mode if state else "default"
        self.state = state or self.cfg.get("default_state", "turbulent")
        self.last = {"date": str(asof), "state": self.state, "hmm": hmm_state, "sma200": sma_state, "source": source,
                     "probs": None if probs is None else [round(float(x), 4) for x in probs]}
        return self.last

    def posture(self, horizon: str, style: str) -> tuple[bool, float, bool]:
        """(new entries allowed, size multiplier, liquidate existing)."""
        s = self.state
        if s == "calm":
            return True, 1.0, False
        if s == "turbulent":
            if style == "liquidity_providing":
                return False, 0.0, False
            return True, (0.5 if horizon == "swing" else 1.0), False
        if horizon == "swing":
            return False, 0.0, style == "liquidity_providing"
        if style == "liquidity_providing":
            return False, 0.0, False
        return True, 0.5, False


def fit_job(args) -> dict | None:
    """Top-level (picklable) HMM fit for the parallel timeline builder. Also runs the O(T) forward filter
    over the fitted window here, in parallel, so the sequential pass that follows only advances a step per day."""
    cfg, X = args
    X = np.asarray(X)
    eng = RegimeEngine(cfg)
    p = eng._fit(X)
    if p is not None:
        eng.params = p
        eng._filtered_last(X)
        p = {**p, "_n": len(X), "_la": eng._cache[2]}
    return p


def build_timeline(cfg: dict, close: pd.Series, vix: pd.Series | None, days, workers: int = 1,
                   stats: dict | None = None, policy: dict | None = None) -> dict[str, dict]:
    """Regime state for every session in `days` (computed from data up to the previous session), identical
    to calling RegimeEngine.update() day by day, but with every HMM fit run in parallel first."""
    from ..parallel.pool import process_map
    close = close.dropna()
    X_all = RegimeEngine.features(close, vix) if cfg.get("mode", "both") in ("hmm", "both") else None
    asofs = []
    for d in days:
        i = close.index.searchsorted(pd.Timestamp(d), side="left")      # strictly before the session day
        asofs.append(close.index[i - 1] if i > 0 else None)
    fits: dict[int, dict | None] = {}
    if X_all is not None:
        probe, points = RegimeEngine(cfg), []
        for a in asofs:
            if a is None:
                continue
            n = int(X_all.index.searchsorted(a, side="right"))
            if n >= int(cfg.get("min_obs", 750)) and probe.needs_fit(n):
                points.append(n)
                probe.params, probe._fit_n = {}, n        # assume success; failures refit on the fly below
        Xv = X_all.to_numpy()
        results = process_map(fit_job, [(cfg, Xv[:n]) for n in points], workers, label="regime fits", stats=stats,
                              policy=policy)
        fits = dict(zip(points, results))
    eng = RegimeEngine(cfg, fitter=lambda n, X: fits[n] if fits.get(n) is not None else RegimeEngine(cfg)._fit(X))
    out = {}
    for d, a in zip(days, asofs):
        if a is None:
            continue
        out[d.isoformat()] = dict(eng.update(a, close, vix, X_all=X_all))
    return out
