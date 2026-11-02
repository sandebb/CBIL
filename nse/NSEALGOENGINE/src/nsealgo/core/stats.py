"""Statistical building blocks for the research strategies (vectorised NumPy/SciPy, no extra dependencies).

- Moments and tail probabilities: sample skewness / excess kurtosis; Edgeworth CDF and Cornish-Fisher
  quantiles WITH a validity check (the expansions stop being distributions for large skew or kurtosis and
  are least reliable in the far tails, where stop and gap decisions live), always next to the empirical rate.
- Bar anatomy: overnight gap, excursion above / below the open, open-to-close drift.
- AR(1) / Ornstein-Uhlenbeck fits with Dickey-Fuller t-statistics (MacKinnon critical values). An OLS
  half-life on its own is biased towards "finding" mean reversion in a random walk.
- Marchenko-Pastur cleaning of correlation matrices, eigen-portfolio factors and factor residuals.
- Absorption ratio and minimum-spanning-tree statistics (the MST edges are exactly the H0 persistence of a
  Vietoris-Rips filtration on correlation distances).
- Variance-ratio test (Lo-MacKinlay, heteroskedasticity-robust): trending vs mean-reverting behaviour.
- Research tools: transfer entropy with a shuffle-surrogate test; multifractal DFA (h(q), spectrum width).
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import math

import numpy as np
import pandas as pd
from scipy.stats import norm


# ---- moments and tail probabilities -------------------------------------------------------------
def moments(x) -> dict:
    """n, mean, std (ddof=1), skew (adjusted Fisher-Pearson G1) and excess kurtosis (G2), as pandas does."""
    a = np.asarray(x, dtype=float)
    a = a[np.isfinite(a)]
    n = int(a.size)
    out = {"n": n, "mean": math.nan, "std": math.nan, "skew": math.nan, "exkurt": math.nan}
    if n < 8:
        return out
    m = float(a.mean())
    d = a - m
    m2 = float(np.mean(d * d))
    if m2 <= 0:
        out.update(mean=m, std=0.0, skew=0.0, exkurt=0.0)
        return out
    g1 = float(np.mean(d ** 3)) / m2 ** 1.5
    g2 = float(np.mean(d ** 4)) / m2 ** 2 - 3.0
    out.update(mean=m, std=math.sqrt(m2 * n / (n - 1)), skew=g1 * math.sqrt(n * (n - 1)) / (n - 2),
               exkurt=((n + 1) * g2 + 6.0) * (n - 1) / ((n - 2) * (n - 3)))
    return out


def edgeworth_cdf(z, skew: float, exkurt: float = 0.0):
    """Second-order Edgeworth expansion of P(Z <= z) for a standardised variable."""
    z = np.asarray(z, dtype=float)
    he2, he3, he5 = z ** 2 - 1, z ** 3 - 3 * z, z ** 5 - 10 * z ** 3 + 15 * z
    return norm.cdf(z) - norm.pdf(z) * (skew / 6 * he2 + exkurt / 24 * he3 + skew ** 2 / 72 * he5)


def edgeworth_valid(skew: float, exkurt: float = 0.0, lo: float = -6.0, hi: float = 6.0) -> bool:
    """True when the expansion is a proper distribution on [lo, hi]: monotone CDF inside [0, 1]."""
    if not (math.isfinite(skew) and math.isfinite(exkurt)):
        return False
    c = edgeworth_cdf(np.linspace(lo, hi, 1201), skew, exkurt)
    return bool(np.all(np.diff(c) >= -1e-12) and c.min() >= -1e-9 and c.max() <= 1 + 1e-9)


def cornish_fisher_quantile(p, skew: float, exkurt: float = 0.0):
    """Standardised quantile adjusted for skewness and excess kurtosis (check edgeworth_valid first)."""
    z = norm.ppf(p)
    return z + (z ** 2 - 1) * skew / 6 + (z ** 3 - 3 * z) * exkurt / 24 - (2 * z ** 3 - 5 * z) * skew ** 2 / 36


def exceed_probability(sample, threshold: float, side: str = "above") -> dict:
    """P(X >= t) (side='above') or P(X <= t) ('below'). Empirical with add-one smoothing (a never-seen event
    is not reported as impossible) and Edgeworth, which is None when the expansion is invalid."""
    a = np.asarray(sample, dtype=float)
    a = a[np.isfinite(a)]
    n = int(a.size)
    hits = int((a >= threshold).sum() if side == "above" else (a <= threshold).sum())
    out = {"n": n, "empirical": (hits + 1) / (n + 2) if n else math.nan, "edgeworth": None}
    mo = moments(a)
    if n >= 30 and mo["std"] > 0 and edgeworth_valid(mo["skew"], mo["exkurt"]):
        c = float(edgeworth_cdf((threshold - mo["mean"]) / mo["std"], mo["skew"], mo["exkurt"]))
        out["edgeworth"] = min(1.0, max(0.0, 1.0 - c if side == "above" else c))
    return out


def bar_anatomy(df: pd.DataFrame) -> pd.DataFrame:
    """Log components of each daily bar: gap = ln(O/C_prev), upper = ln(H/O) >= 0, lower = ln(O/L) >= 0,
    drift = ln(C/O). gap + drift = the close-to-close return. The components are correlated (a strong
    drift day has a large excursion on the same side), so they are not treated as independent."""
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    pc = np.roll(c, 1)
    out = pd.DataFrame({"gap": np.log(o / pc), "upper": np.maximum(np.log(h / o), 0.0),
                        "lower": np.maximum(np.log(o / l), 0.0), "drift": np.log(c / o)}, index=df.index)
    return out.iloc[1:]


# ---- AR(1) / Ornstein-Uhlenbeck with Dickey-Fuller -------------------------------------------------
def df_critical(n: int, level: float = 0.05) -> float:
    """Dickey-Fuller critical value (constant, no trend) for n observations (MacKinnon response surface)."""
    idx = {0.01: 0, 0.05: 1, 0.10: 2}[level]
    try:
        from statsmodels.tsa.adfvalues import mackinnoncrit
        return float(mackinnoncrit(N=1, regression="c", nobs=n)[idx])
    except Exception:
        return (-3.43, -2.86, -2.57)[idx]


def ar1_fit(X) -> dict:
    """Fit x[t+1] = a + b x[t] + e for a series (1-D) or every column of a (T, N) array at once.

    Ornstein-Uhlenbeck view (per observation interval): kappa = -ln b, half-life = ln 2 / kappa,
    equilibrium mu = a / (1 - b), equilibrium sd = sd(e) / sqrt(1 - b^2).
    df_t is the Dickey-Fuller t-statistic of (b - 1); `stationary` requires 0 < b < 1 AND df_t below the
    critical value, because OLS b is biased below 1 even for a pure random walk."""
    A = np.asarray(X, dtype=float)
    one = A.ndim == 1
    if one:
        A = A[:, None]
    x, y = A[:-1], A[1:]
    n = x.shape[0]
    with np.errstate(divide="ignore", invalid="ignore"):
        xm, ym = x.mean(0), y.mean(0)
        dx = x - xm
        sxx = (dx * dx).sum(0)
        b = (dx * (y - ym)).sum(0) / sxx
        a = ym - b * xm
        resid = y - a - b * x
        s2 = (resid * resid).sum(0) / max(n - 2, 1)
        t = (b - 1.0) / np.sqrt(s2 / sxx)
        ok = np.isfinite(b) & (b > 0) & (b < 1)
        kappa = np.where(ok, -np.log(np.where(ok, b, 0.5)), 0.0)
        hl = np.where(ok, math.log(2) / np.where(ok, kappa, 1.0), np.inf)
        mu = np.where(ok, a / (1 - b), np.nan)
        sig = np.where(ok, np.sqrt(s2 / (1 - b * b)), np.nan)
    crit = df_critical(n)
    out = {"b": b, "a": a, "kappa": kappa, "half_life": hl, "mu": mu, "sigma_eq": sig, "resid_sd": np.sqrt(s2),
           "df_t": t, "df_crit": crit, "stationary": ok & (t < crit), "n": n}
    if one:
        out = {k: (v.item() if isinstance(v, np.ndarray) else v) for k, v in out.items()}
    return out


# ---- random-matrix cleaning, eigen-portfolios, fragility ------------------------------------------
def _standardize(R: np.ndarray):
    mu, sd = R.mean(0), R.std(0)
    sd = np.where(sd > 0, sd, 1.0)
    return (R - mu) / sd, sd


def mp_clean(R) -> dict:
    """Marchenko-Pastur cleaning of the correlation matrix of returns R (T observations x N assets).

    Noise bound lambda+ = sigma2 (1 + sqrt(N/T))^2 with sigma2 = 1 - lambda_1 / N (the market mode carries
    variance out of the noise bulk; assuming sigma2 = 1 overstates the bound). Eigenvalues above lambda+ are
    kept, the rest are replaced by their average (trace preserved) and the diagonal is reset to 1."""
    R = np.asarray(R, dtype=float)
    T, N = R.shape
    Z, _ = _standardize(R)
    C = (Z.T @ Z) / T
    evals, evecs = np.linalg.eigh(C)
    order = np.argsort(evals)[::-1]
    evals, evecs = evals[order], evecs[:, order]
    q = N / T
    sigma2 = min(1.0, max(1e-3, 1.0 - evals[0] / N))
    lam_plus = sigma2 * (1 + math.sqrt(q)) ** 2
    k = int((evals > lam_plus).sum())
    cleaned = evals.copy()
    if k < N:
        cleaned[k:] = evals[k:].mean()
    Cc = (evecs * cleaned) @ evecs.T
    d = np.sqrt(np.clip(np.diag(Cc), 1e-12, None))
    Cc = Cc / np.outer(d, d)
    return {"corr": Cc, "raw_corr": C, "evals": evals, "evecs": evecs, "n_signal": k, "lambda_plus": lam_plus,
            "q": q, "sigma2": sigma2}


def eigen_factor_returns(R_est, R_apply, k: int) -> np.ndarray:
    """Returns of the top-k eigen-portfolios (Avellaneda-Lee): weights v_i / sigma_i estimated on R_est,
    applied to the returns R_apply. Shape (len(R_apply), k)."""
    R_est = np.asarray(R_est, dtype=float)
    Z, sd = _standardize(R_est)
    evals, evecs = np.linalg.eigh((Z.T @ Z) / len(R_est))
    top = evecs[:, np.argsort(evals)[::-1][:max(1, k)]]
    return np.asarray(R_apply, dtype=float) @ (top / sd[:, None])


def factor_residuals(R, F) -> tuple[np.ndarray, np.ndarray]:
    """OLS of every column of R on [1, F] (shared design): residuals (T, N) and coefficients (1+k, N)."""
    R = np.asarray(R, dtype=float)
    X = np.column_stack([np.ones(len(R)), np.asarray(F, dtype=float)])
    B, *_ = np.linalg.lstsq(X, R, rcond=None)
    return R - X @ B, B


def absorption_ratio(R, frac: float = 0.2) -> float:
    """Share of total variance explained by the top frac*N eigenvectors of the covariance of returns
    (Kritzman, Li, Page & Rigobon 2011): high when markets are tightly coupled."""
    R = np.asarray(R, dtype=float)
    ev = np.sort(np.linalg.eigvalsh(np.cov(R, rowvar=False)))[::-1]
    k = max(1, int(round(R.shape[1] * frac)))
    tot = float(ev.sum())
    return float(ev[:k].sum() / tot) if tot > 0 else math.nan


def mst_stats(corr) -> dict:
    """Minimum spanning tree on correlation distances d = sqrt(2 (1 - rho)) (Mantegna). Its edges are the
    death times of H0 features in a Vietoris-Rips filtration. Normalised tree length shrinks when the market
    contracts (Onnela et al.); persistent entropy of the edge lengths is scaled to [0, 1]."""
    from scipy.sparse.csgraph import minimum_spanning_tree
    C = np.asarray(corr, dtype=float)
    D = np.sqrt(np.clip(2.0 * (1.0 - C), 0.0, None))
    off = ~np.eye(len(D), dtype=bool)
    D[off & (D <= 0)] = 1e-12                      # scipy treats exact zeros as "no edge"
    np.fill_diagonal(D, 0.0)
    T = minimum_spanning_tree(D).toarray()
    e = T[T > 0]
    if e.size < 2:
        return {"tree_length": math.nan, "persistent_entropy": math.nan}
    p = e / e.sum()
    return {"tree_length": float(e.mean()), "persistent_entropy": float(-(p * np.log(p)).sum() / math.log(e.size))}


# ---- variance ratio ---------------------------------------------------------------------------------
def variance_ratio(r, q: int, groups=None) -> tuple[float, float]:
    """Lo-MacKinlay VR(q) from overlapping q-period sums and the heteroskedasticity-robust z*.
    VR > 1 (z* > 0): returns trend at horizon q; VR < 1: they mean-revert. `groups` (e.g. session dates)
    keeps q-sums and lag products from spanning a boundary such as the overnight gap."""
    r = np.asarray(r, dtype=float)
    g = np.zeros(len(r), dtype=np.int64) if groups is None else pd.factorize(np.asarray(groups))[0]
    keep = np.isfinite(r)
    r, g = r[keep], g[keep]
    n = r.size
    if n < 4 * q or q < 2:
        return math.nan, math.nan
    d = r - r.mean()
    s1 = float((d * d).sum())
    if s1 <= 0:
        return math.nan, math.nan
    qs, dj = [], np.zeros(q)
    for gid in np.unique(g):
        x = d[g == gid]
        if x.size >= q:
            c = np.concatenate(([0.0], np.cumsum(x)))
            qs.append(c[q:] - c[:-q])
        x2 = x * x
        for j in range(1, q):
            if x.size > j:
                dj[j] += float((x2[j:] * x2[:-j]).sum())
    if not qs:
        return math.nan, math.nan
    qs = np.concatenate(qs)
    vr = float((qs * qs).mean() / q) / (s1 / n)
    theta = sum((2.0 * (q - j) / q) ** 2 * (n * dj[j] / s1 ** 2) for j in range(1, q))
    return vr, (math.sqrt(n) * (vr - 1.0) / math.sqrt(theta)) if theta > 0 else math.nan


def intraday_variance_ratio(minute_df: pd.DataFrame, q: int = 6, bar_minutes: int = 5) -> tuple[float, float]:
    """VR(q) and z* of within-session returns sampled every `bar_minutes` from 1-minute bars (overnight gaps
    excluded). With 5-minute returns and q=6 it asks: do 30-minute moves extend (trend) or fade (revert)?"""
    if minute_df is None or len(minute_df) < 100:
        return math.nan, math.nan
    sel = minute_df[(minute_df.index.minute % bar_minutes) == bar_minutes - 1]
    days = np.asarray(sel.index.date)
    r = np.diff(np.log(sel["close"].to_numpy(dtype=float)))
    same = days[1:] == days[:-1]
    return variance_ratio(r[same], q, groups=days[1:][same])


# ---- information flow and multifractality (research tools) --------------------------------------------
def _codes(x: np.ndarray, k: int) -> np.ndarray:
    """Equal-frequency bin codes 0..k-1 from quantile thresholds. Ties (e.g. the many zero 1-minute returns)
    share a code; rank-based splitting would order ties by time and manufacture dependence."""
    th = np.quantile(x, np.linspace(0, 1, k + 1)[1:-1])
    return np.searchsorted(th, x, side="right").astype(np.int64)


def _cmi(a: np.ndarray, b: np.ndarray, c: np.ndarray, k: int) -> float:
    """Conditional mutual information I(a; c | b) in bits from integer codes (plug-in estimate)."""
    p = np.bincount((a * k + b) * k + c, minlength=k ** 3).reshape(k, k, k).astype(float)
    p /= p.sum()
    pab, pbc, pb = p.sum(2), p.sum(0), p.sum((0, 2))
    A, B, C = np.nonzero(p)
    v = p[A, B, C]
    return float(np.sum(v * np.log2(v * pb[B] / (pab[A, B] * pbc[B, C]))))


def transfer_entropy(x, y, lag: int = 1, bins: int = 3, groups=None, n_shuffle: int = 200, seed: int = 0) -> dict:
    """Schreiber transfer entropy T(X -> Y) = I(Y_t ; X_{t-lag} | Y_{t-1}) in bits, history length 1.

    Plug-in TE is biased upwards (more so with more bins), so it is reported next to a shuffle-surrogate test:
    X_{t-lag} is permuted, which keeps Y's own dynamics and destroys only the X->Y link. `effective` = TE
    minus the surrogate mean; `p` = share of surrogates at least as large. `groups` (session dates) keeps
    lags from spanning the overnight gap. TE measures dependence, not its sign: see the lagged correlation."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    g = np.zeros(len(y), dtype=np.int64) if groups is None else pd.factorize(np.asarray(groups))[0]
    yt, yp, xl = [], [], []
    for gid in np.unique(g):
        m = g == gid
        xs, ys = x[m], y[m]
        if len(ys) > lag + 1:
            yt.append(ys[lag:]); yp.append(ys[lag - 1:-1]); xl.append(xs[:len(ys) - lag])
    if not yt:
        return {"te": math.nan, "effective": math.nan, "p": math.nan, "n": 0, "corr": math.nan}
    yt, yp, xl = (np.concatenate(v) for v in (yt, yp, xl))
    ok = np.isfinite(yt) & np.isfinite(yp) & np.isfinite(xl)
    yt, yp, xl = yt[ok], yp[ok], xl[ok]
    if len(yt) < 50 * bins:
        return {"te": math.nan, "effective": math.nan, "p": math.nan, "n": int(len(yt)), "corr": math.nan}
    cy, cyp, cx = _codes(yt, bins), _codes(yp, bins), _codes(xl, bins)
    te = _cmi(cy, cyp, cx, bins)
    rng = np.random.default_rng(seed)
    sur = np.array([_cmi(cy, cyp, rng.permutation(cx), bins) for _ in range(n_shuffle)])
    return {"te": te, "effective": te - float(sur.mean()), "p": float((1 + (sur >= te).sum()) / (1 + n_shuffle)),
            "n": int(len(yt)), "corr": float(np.corrcoef(xl, yt)[0, 1])}


def mfdfa(x, scales=None, qs=(-3.0, -2.0, -1.0, 1.0, 2.0, 3.0), order: int = 1, min_segments: int = 4) -> dict:
    """Multifractal detrended fluctuation analysis (Kantelhardt et al. 2002) of a return series.

    Segments are taken from both ends (2*Ns per scale), each detrended by a polynomial of `order`. Scales that
    leave fewer than `min_segments` segments are dropped instead of crashing, and fewer than 4 usable scales
    gives NaN, because a slope from 2-3 points means nothing. h(2) is the DFA Hurst exponent (0.5 = no memory);
    delta_h = h(q_min) - h(q_max); delta_alpha is the singularity-spectrum width via the Legendre transform.
    Wide spectra need thousands of points to be told apart from noise."""
    a = np.asarray(x, dtype=float)
    a = a[np.isfinite(a)]
    n = len(a)
    out = {"n": n, "h": {}, "hurst": math.nan, "delta_h": math.nan, "delta_alpha": math.nan, "scales": []}
    if scales is None:
        scales = np.unique(np.logspace(1, math.log10(max(n / min_segments, 10)), 12).astype(int))
    sc = [int(s) for s in scales if s >= order + 3 and n // int(s) >= min_segments]
    if len(sc) < 4:
        return out
    Y = np.cumsum(a - a.mean())
    F2 = {}
    for s in sc:
        ns = n // s
        V = np.vander(np.arange(s, dtype=float), order + 1)
        P = V @ np.linalg.pinv(V)                         # projection onto the polynomial trend
        segs = np.vstack([Y[:ns * s].reshape(ns, s), Y[n - ns * s:].reshape(ns, s)])
        res = segs - segs @ P.T
        F2[s] = np.maximum((res * res).mean(axis=1), 1e-300)
    ls = np.log(sc)
    h = {}
    for q in qs:
        if q == 0:
            fq = [math.exp(0.5 * float(np.mean(np.log(F2[s])))) for s in sc]
        else:
            fq = [float(np.mean(F2[s] ** (q / 2.0))) ** (1.0 / q) for s in sc]
        h[float(q)] = float(np.polyfit(ls, np.log(fq), 1)[0])
    qa = np.array(sorted(h))
    ha = np.array([h[q] for q in qa])
    out.update(h=h, scales=sc)
    if len(qa) >= 2:                                   # the spectrum needs at least two moment orders
        alpha = np.gradient(qa * ha - 1.0, qa)
        out.update(delta_h=float(ha[0] - ha[-1]), delta_alpha=float(alpha.max() - alpha.min()))
    if 2.0 in h:
        out["hurst"] = h[2.0]
    else:
        out["hurst"] = float(mfdfa(a, sc, (2.0,), order, min_segments)["h"].get(2.0, math.nan))
    return out
