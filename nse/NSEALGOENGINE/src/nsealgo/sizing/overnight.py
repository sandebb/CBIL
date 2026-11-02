"""
Position sizing for the overnight trade - buy in the 15:40-16:00 post-close session at the official close, sell in the
next pre-open auction at the official open: how many rupees of each ranked stock, so the orders fit the two thin
sessions and the money goes where the expected profit after costs and slippage is highest.

Per stock i tonight (from the 15:37 forecast):
  edge_i      expected overnight move before costs (bps) = a + b x predicted gap, clipped (EdgeModel - fitted on past
              nights, recent ones weighted more: the edge has been shrinking)
  cap_i       the most we buy = the smallest of
                PC_PART      x the stock's usual post-close session (rupees)   ENTRY: an order fills only as far as
                                                                               sellers turn up in those 20 minutes
                AUCTION_PART x its usual pre-open auction (rupees)             EXIT
                MAX_WEIGHT   x capital
  impact_i(x) our own sale pushes the opening price down: KAPPA x daily sigma x (x / usual auction rupees), in bps.
              KAPPA is a prior (0.5: selling the whole usual auction volume again would cost half a daily sigma) until
              the pre-open recordings calibrate it.
  costs       Shoonya delivery: 22.2 bps of the value (STT 0.1% each side, stamp 0.015%, NSE + SEBI + GST) and
              Rs 10.62 DP per stock sold.

allocate() maximises  sum_i [ x_i (edge_i - 22.2)/1e4  -  x_i impact_i(x_i)/1e4  -  10.62 ]  with 0 <= x_i <= cap_i and
sum x_i <= capital. The answer is "water-filling": every stock bought ends at the same marginal net edge lambda,
x_i = (edge_i - 22.2 - lambda) / (2 KAPPA sigma_i / auction_i), clipped to [0, cap_i]. lambda > 0 only when the money
runs out before the good ideas do; otherwise part of the capital stays in cash ON PURPOSE - the sessions cannot take
more without slippage. Positions whose expected profit does not cover the DP charge are dropped; whole shares only.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

STT, STAMP, EXCH, SEBI, GST, DP = 0.001, 0.00015, 0.0000297, 0.000001, 0.18, 9.0
DP_RS = DP * (1 + GST)                                                     # Rs 10.62 per stock sold
VAR_COST_BPS = (2 * STT + STAMP + 2 * (EXCH + SEBI) * (1 + GST)) * 1e4     # 22.2 bps of the value, round trip


def costs_rs(buy_value, sell_value) -> np.ndarray:
    """Shoonya delivery charges of a round trip (0 when nothing was bought)."""
    b, s = np.asarray(buy_value, float), np.asarray(sell_value, float)
    c = STT * (b + s) + STAMP * b + (EXCH + SEBI) * (b + s) * (1 + GST) + DP_RS
    return np.where(b > 0, c, 0.0)


@dataclass
class Params:
    pc_part: float = 0.5        # share of the stock's usual post-close session we try to buy
    auction_part: float = 0.10  # most of its usual pre-open auction we sell into
    kappa: float = 0.5          # auction impact = kappa x daily sigma x (our sale / usual auction volume)
    max_weight: float = 0.5     # most of the capital in one stock
    min_profit_rs: float = 10.0  # expected profit (after all costs) a position must beat - no Rs 1 "positions"
    max_stocks: int = 40        # candidates considered, by rank


@dataclass
class EdgeModel:
    """Expected overnight move before costs (bps) = a + b x predicted gap (bps), the gap clipped to [lo, hi]."""
    a: float = 0.0
    b: float = 0.5
    lo: float = -50.0
    hi: float = 200.0
    nights: int = 0
    fitted_through: str = ""
    half_life: float = 250.0

    def edge_bps(self, pred_bps) -> np.ndarray:
        return self.a + self.b * np.clip(np.asarray(pred_bps, float), self.lo, self.hi)

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "EdgeModel":
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))


def fit_edge(rows: pd.DataFrame, half_life: float = 250.0, min_nights: int = 40, prior: EdgeModel | None = None) -> EdgeModel:
    """rows: buy_day, pred_bps, gross_bps of EARLIER nights' candidates. Weighted least squares, the weight halving every
    `half_life` nights back; overnight moves winsorised at +-600 bps, the predicted gap clipped at its 1st / 99th
    percentile. Fewer than `min_nights` nights: the prior (a = 0, b = 0.5 - half the predicted gap)."""
    prior = prior or EdgeModel(half_life=half_life)
    days = np.sort(rows["buy_day"].unique()) if len(rows) else np.array([])
    if len(days) < min_nights:
        return EdgeModel(**{**asdict(prior), "nights": int(len(days))})
    lo, hi = (float(v) for v in rows["pred_bps"].quantile([0.01, 0.99]))
    x = rows["pred_bps"].clip(lo, hi).to_numpy(float)
    y = rows["gross_bps"].clip(-600, 600).to_numpy(float)
    age = len(days) - 1 - np.searchsorted(days, rows["buy_day"].to_numpy())
    w = 0.5 ** (age / half_life)
    sw, sx, sy = w.sum(), (w * x).sum(), (w * y).sum()
    sxx, sxy = (w * x * x).sum(), (w * x * y).sum()
    den = sw * sxx - sx * sx
    b = (sw * sxy - sx * sy) / den if den > 0 else 0.0
    b = max(b, 0.0)                      # an inverted fit would mean "buy what the model likes least" - not this trade
    a = (sy - b * sx) / sw
    return EdgeModel(a=float(a), b=float(b), lo=lo, hi=hi, nights=int(len(days)),
                     fitted_through=str(pd.Timestamp(days[-1]).date()), half_life=half_life)


def _waterfill(a: np.ndarray, k: np.ndarray, cap: np.ndarray, budget: float, on: np.ndarray) -> np.ndarray:
    """x_i = clip((a_i - lam) / k_i, 0, cap_i) for the active stocks, lam >= 0 the smallest that fits the budget."""
    kk = np.maximum(k, 1e-12)

    def xs(lam):
        return np.where(on, np.clip((a - lam) / kk, 0.0, cap), 0.0)

    x = xs(0.0)
    if x.sum() <= budget:
        return x
    lo, hi = 0.0, float(a[on].max())
    for _ in range(80):
        mid = (lo + hi) / 2
        if xs(mid).sum() > budget:
            lo = mid
        else:
            hi = mid
    x = xs(hi)
    left = budget - x.sum()                       # (no-impact stocks jump from 0 to their cap: hand out the rest)
    for i in np.argsort(-a):
        if left <= 0:
            break
        if on[i] and x[i] < cap[i] and a[i] > hi - 1e-9:
            add = min(cap[i] - x[i], left)
            x[i] += add
            left -= add
    return x


def allocate_arrays(price, edge_bps, sigma_bps, pc_rs, pre_rs, capital: float, p: Params):
    """The optimisation on plain arrays (one night, candidates in rank order). Returns qty, cap_rs, cap_index
    (0 post-close session, 1 pre-open auction, 2 max weight), net_edge_bps, k (marginal impact, bps per rupee)."""
    price = np.asarray(price, float)
    a = np.asarray(edge_bps, float) - VAR_COST_BPS
    pre = np.maximum(np.asarray(pre_rs, float), 1.0)
    sig = np.asarray(sigma_bps, float)
    n = len(price)
    caps = np.vstack([p.pc_part * np.asarray(pc_rs, float), p.auction_part * pre, np.full(n, p.max_weight * capital)])
    cap = caps.min(axis=0)
    k = 2 * p.kappa * sig / pre                         # marginal impact, bps per rupee sold
    on = (a > 0) & (cap >= price) & np.isfinite(a) & np.isfinite(cap) & np.isfinite(k)
    x = np.zeros(n)
    for _ in range(20):                                 # drop positions that cannot cover the DP charge, re-solve
        x = _waterfill(a, k, cap, capital, on)
        prof = x * (a - k / 2 * x) / 1e4 - DP_RS * (x > 0)
        bad = on & (x > 0) & (prof <= p.min_profit_rs)
        if not bad.any():
            break
        on &= ~bad
    qty = _topup(np.floor(x / price), price, a, k, cap, capital)
    return qty, cap, caps.argmin(axis=0), a, k


def _topup(qty: np.ndarray, price: np.ndarray, a: np.ndarray, k: np.ndarray, cap: np.ndarray, budget: float) -> np.ndarray:
    """Whole shares: after rounding down, spend what is left one share at a time on the stock whose NEXT share still
    earns the most after its own push on the open (positions already chosen only, caps respected)."""
    qty = qty.copy()
    x = qty * price
    left = budget - x.sum()
    for _ in range(10_000):
        ok = (qty > 0) & (x + price <= cap + 1e-6) & (price <= left)
        if not ok.any():
            break
        m = np.where(ok, a - k * (x + price / 2), -np.inf)      # marginal net edge of one more share, bps
        i = int(np.argmax(m))
        if m[i] <= 0:
            break
        qty[i] += 1
        x[i] += price[i]
        left -= price[i]
    return qty


CAP_BY = np.array(["post-close session", "pre-open auction", "max weight"])


def allocate(c: pd.DataFrame, capital: float, p: Params | None = None) -> pd.DataFrame:
    """One night. c: rank, company_id, price, edge_bps (expected move before costs), sigma_bps (daily volatility),
    pc_rs / pre_rs (the stock's usual post-close session and pre-open auction, rupees). Returns c (best ranks first,
    at most p.max_stocks) with: net_edge_bps, cap_rs, cap_by, x_rs (rupees bought = qty x price), qty,
    impact_bps (expected, on the exit), exp_net_rs (expected profit after costs and impact), limited_by (what stopped
    the position from being bigger - or why the stock was not bought)."""
    p = p or Params()
    c = c.sort_values("rank").head(p.max_stocks).reset_index(drop=True).copy()
    if c.empty:
        return c.assign(net_edge_bps=[], cap_rs=[], cap_by=[], x_rs=[], qty=[], impact_bps=[], exp_net_rs=[])
    qty, cap, by, a, k = allocate_arrays(c["price"], c["edge_bps"], c["sigma_bps"], c["pc_rs"], c["pre_rs"], capital, p)
    x = qty * c["price"].to_numpy(float)
    imp = k / 2 * x
    c["net_edge_bps"] = a
    c["cap_rs"] = cap
    c["cap_by"] = CAP_BY[by]
    c["x_rs"] = x
    c["qty"] = qty.astype(int)
    c["impact_bps"] = imp
    c["exp_net_rs"] = np.where(qty > 0, x * (a - imp) / 1e4 - DP_RS, 0.0)
    price = c["price"].to_numpy(float)
    at_cap = x >= cap - price
    inner = (qty > 0) & ~at_cap
    lam = float(np.median(a[inner] - k[inner] * x[inner])) if inner.any() else 0.0   # marginal edge of the last rupee
    short = np.array(["post-close cap", "auction cap", "max weight"])[by]
    c["limited_by"] = np.where(qty > 0, np.where(at_cap, short, np.where(lam > 0.5, "money shared out", "edge vs impact")),
                               np.where(a <= 0, "move < costs", np.where(cap < price, "cap < 1 share",
                                                                         "others better / DP")))
    return c


def caps_arrays(price, pc_rs, pre_rs, capital: float, p: Params) -> np.ndarray:
    """The per-stock rupee caps allocate() uses (post-close session, auction, max weight)."""
    pre = np.maximum(np.asarray(pre_rs, float), 1.0)
    return np.minimum(np.minimum(p.pc_part * np.asarray(pc_rs, float), p.auction_part * pre), p.max_weight * capital)


def capped_equal_arrays(price, cap, capital: float, n: int) -> np.ndarray:
    """Volume-aware but rank-blind: equal rupees in the top n (rank order), none above its cap; what a capped stock
    cannot take goes to the others. Returns whole shares."""
    price, cap = np.asarray(price, float), np.asarray(cap, float)
    on = (np.arange(len(price)) < n) & (cap >= price)
    if not on.any():
        return np.zeros(len(price))
    lo, hi = 0.0, float(capital)
    if np.minimum(cap[on], hi).sum() <= capital:
        level = hi
    else:
        for _ in range(60):
            mid = (lo + hi) / 2
            if np.minimum(cap[on], mid).sum() > capital:
                hi = mid
            else:
                lo = mid
        level = lo
    x = np.where(on, np.minimum(cap, level), 0.0)
    return np.floor(x / price)


def expected_outcome(c: pd.DataFrame, qty, p: Params) -> dict:
    """What a set of share quantities is EXPECTED to do tonight (c: price, edge_bps, sigma_bps, pc_rs, pre_rs):
    the post-close session fills at most its expected size; the sale pushes the open by kappa x sigma x (sale /
    auction). Returns ordered, filled, unfilled, slippage (the push, Rs) and expected profit after charges, all Rs."""
    price = c["price"].to_numpy(float)
    order = np.asarray(qty, float) * price
    fill = np.minimum(order, np.maximum(c["pc_rs"].to_numpy(float), 0.0))
    fill = np.floor(fill / price) * price
    pre = np.maximum(c["pre_rs"].to_numpy(float), 1.0)
    imp = p.kappa * c["sigma_bps"].to_numpy(float) * fill / pre                     # average push, bps of the sale
    a = c["edge_bps"].to_numpy(float) - VAR_COST_BPS
    prof = np.where(fill > 0, fill * (a - imp) / 1e4 - DP_RS, 0.0)
    return {"ordered": float(order.sum()), "filled": float(fill.sum()), "unfilled": float((order - fill).sum()),
            "slippage": float((fill * imp / 1e4).sum()), "profit": float(prof.sum()), "stocks": int((fill > 0).sum()),
            "fill_rs": fill, "impact_bps": imp, "profit_rs": prof}


def equal(c: pd.DataFrame, capital: float, n: int) -> pd.DataFrame:
    """The blind version: capital / n rupees in each of the top n ranks, whole shares."""
    c = c.sort_values("rank").reset_index(drop=True).copy()
    x = np.where(c["rank"].to_numpy() <= n, capital / n, 0.0)
    qty = np.floor(x / c["price"].to_numpy(float))
    c["qty"] = qty.astype(int)
    c["x_rs"] = qty * c["price"].to_numpy(float)
    return c


# ---- tonight's plan (nsealgo size-overnight) ---------------------------------------------------------------------
BANDS = (0.02, 0.05, 0.10, 0.20)
MIN_TURNOVER, MIN_PRICE = 25e7, 0.0            # the trade's universe. The Rs 50 floor was dropped on 19 Oct 2026
FALLBACK_MIN_PRICE = 50.0                       # ... but comes back when the surveillance list cannot be checked
SURV_MAX_AGE_DAYS = 5                           # an ASM / GSM snapshot older than this counts as missing


def tradable_check(day: pd.Timestamp, ids, fyers_symbols, surv_path: Path | None, fresh_path: Path | None):
    """Stocks that cannot be bought tonight and sold tomorrow morning (no BTST):
      trade-for-trade series BE / BZ (every trade settles by delivery) - from the at-close snapshot's Fyers symbol
        (NSE:XYZ-BE), else the last series in NSEDATAENGINE's fresh rebuild;
      NSE surveillance: any ASM (long / short term) or GSM stage - NSEDATAENGINE data_ref/surveillance.parquet (daily
        snapshot by nse_alt_data.py daily), the newest snapshot on or before `day`.
    Returns (blocked: dict company_id -> reason, surveillance_ok: bool, note)."""
    ids = list(ids)
    blocked, notes = {}, []
    series = {}
    for cid, fs in zip(ids, fyers_symbols if fyers_symbols is not None else [None] * len(ids)):
        if isinstance(fs, str) and "-" in fs:
            series[cid] = fs.rsplit("-", 1)[-1].upper()
    miss = [c for c in ids if c not in series]
    if miss and fresh_path is not None and Path(fresh_path).exists():
        try:
            f = pd.read_parquet(fresh_path, columns=["company_id", "datetime", "series"],
                                filters=[("datetime", ">=", day - pd.Timedelta(days=45)), ("datetime", "<=", day)])
            last = f.sort_values("datetime").groupby("company_id")["series"].last()
            for c in miss:
                if c in last.index:
                    series[c] = str(last[c]).upper()
        except Exception as ex:                             # noqa: BLE001
            notes.append(f"series check: {ex}")
    for c, sr in series.items():
        if sr in ("BE", "BZ"):
            blocked[c] = f"trade-for-trade series {sr} (no BTST)"
    ok = False
    if surv_path is not None and Path(surv_path).exists():
        s = pd.read_parquet(surv_path)
        s["date"] = pd.to_datetime(s["date"])
        s = s[s["date"] <= day]
        if len(s):
            last = s["date"].max()
            age = (day - last).days
            if age <= SURV_MAX_AGE_DAYS:
                ok = True
                s = s[s["date"] == last]
                for r in s.itertuples():
                    lst = str(r.list).replace(".data", "").replace("asm:longterm", "ASM long term") \
                        .replace("asm:shortterm", "ASM short term").replace("gsm", "GSM")
                    blocked.setdefault(r.company_id, f"{lst} {r.stage}".strip())
                if age > 1:
                    notes.append(f"surveillance list is from {last:%Y-%m-%d} ({age} days old)")
            else:
                notes.append(f"surveillance list is from {last:%Y-%m-%d} - older than {SURV_MAX_AGE_DAYS} days")
        else:
            notes.append(f"no surveillance snapshot on or before {day:%Y-%m-%d}")
    else:
        notes.append(f"no surveillance list ({surv_path})")
    return blocked, ok, notes


def ml_home(root: Path) -> Path:
    import os
    env = os.environ.get("NSEMLENGINE_HOME", "").strip().strip('"')
    return Path(env) if env else Path(root).resolve().parent / "NSEMLENGINE"


def plan_tonight(root: Path, archive: Path, data_dir: Path, runs_dir: Path, capital: float, p: Params | None = None,
                 forecast: Path | None = None, ml_work: str = "work", day=None, log=print,
                 top: int | None = None, min_price: float | None = None) -> tuple[pd.DataFrame, dict]:
    """Tonight's sizing from the 15:37 at-close forecast: the plan (one row per candidate, x_rs / qty > 0 = buy) and a
    summary. top = N: split the capital among the top N ranked stocks only (and compare with an equal split of
    them). Nothing is sent to a broker."""
    import datetime as dt
    from . import liquidity as L
    p = p or Params()
    day = pd.Timestamp(day or dt.date.today()).normalize()
    fpath = Path(forecast) if forecast else ml_home(root) / ml_work / "predictions" / "latest_close.csv"
    if not fpath.exists():
        raise SystemExit(f"no forecast file {fpath} - the 15:37 forecast (python -m nseml predict-close) writes it")
    f = pd.read_csv(fpath)
    fday = pd.Timestamp(str(f["date"].iloc[0])).normalize()
    if fday != day:
        raise SystemExit(f"{fpath.name} is the forecast made on {fday:%Y-%m-%d}, not {day:%Y-%m-%d} - wait for today's "
                         f"15:37 run (or pass --day {fday:%Y-%m-%d} to size that day)")
    paths = L.paths(root, archive)
    snap_p = Path(paths["at_close"]) / f"{day:%Y-%m-%d}.parquet"
    snap = pd.read_parquet(snap_p) if snap_p.exists() else None
    if snap is not None:
        snap = snap[snap["kind"] == "stock"][["company_id", "fyers_symbol", "high", "low", "close", "volume", "prev_close"]]
    else:
        log(f"no at-close snapshot {snap_p} - today's turnover and the upper-band test are not available")
    raw = pd.read_parquet(paths["raw"], columns=["company_id", "datetime", "high", "low", "close", "volume"],
                          filters=[("datetime", ">=", day - pd.Timedelta(days=60)), ("datetime", "<", day)])
    raw = raw.sort_values(["company_id", "datetime"])
    last20 = raw.groupby("company_id").tail(20)
    g = last20.assign(tv=last20["close"] * last20["volume"], flat=(last20["high"] == last20["low"]).astype(int)) \
        .groupby("company_id")
    hist = pd.DataFrame({"tv20": g["tv"].median(), "lock20": g["flat"].sum(),
                         "prev_close_hist": raw.groupby("company_id")["close"].last()})
    hist.index.name = "company_id"
    c = f.rename(columns={"close_today": "price"}).merge(hist.reset_index(), on="company_id", how="left")
    if snap is not None:
        c = c.merge(snap, on="company_id", how="left", suffixes=("", "_snap"))
        c["tv_today"] = c["close"] * c["volume"]
        at_high = c["close"] >= c["high"] * 0.9995
        band = np.zeros(len(c), bool)
        prev = c["prev_close"].fillna(c["prev_close_hist"])
        for b in BANDS:
            band |= ((c["close"] / (prev * (1 + b)) - 1).abs() <= 0.0015).to_numpy()
        c["locked"] = at_high & band
        c["lock20"] = c["lock20"].fillna(0) + (c["high"] == c["low"]).astype(int)
    else:
        c["tv_today"], c["locked"], c["fyers_symbol"] = c["tv20"], False, None
    # stocks that cannot be sold tomorrow morning: trade-for-trade series, ASM / GSM surveillance
    blocked, surv_ok, surv_notes = tradable_check(day, c["company_id"], c.get("fyers_symbol"), paths.get("surveillance"),
                                                  paths.get("raw_fresh"))
    floor = MIN_PRICE if min_price is None else float(min_price)
    if not surv_ok and floor < FALLBACK_MIN_PRICE:
        surv_notes.append(f"surveillance list not checked -> the Rs {FALLBACK_MIN_PRICE:.0f} price floor is back on tonight "
                          "(run: python nse_alt_data.py daily --only surveillance in NSEDATAENGINE)")
        floor = FALLBACK_MIN_PRICE
    c["blocked"] = c["company_id"].map(blocked)
    c["pred_bps"] = np.log1p(c["exp_gap_pct"] / 100) * 1e4
    c["sigma_bps"] = c["daily_vol_pct"] * 100
    base_ok = (c["median_turnover_60d"] >= MIN_TURNOVER) & (c["price"] >= floor) & (c["lock20"].fillna(0) == 0) \
        & ~c["locked"].fillna(False).astype(bool) & c["pred_bps"].notna() & c["tv20"].notna()
    ok = base_ok & c["blocked"].isna()
    # the stocks the check removed that would otherwise have ranked in the top 40
    rm = c[base_ok & c["blocked"].notna()].copy()
    rm["would_rank"] = (c.loc[base_ok, "pred_bps"].rank(ascending=False, method="first")).reindex(rm.index)
    rm = rm[rm["would_rank"] <= 40].sort_values("would_rank")
    removed = [(r.company_id, int(r.would_rank), r.blocked) for r in rm.itertuples()]
    n_locked = int(c["locked"].fillna(False).astype(bool).sum())
    if top:
        from dataclasses import replace
        p = replace(p, max_stocks=int(top))
    c = c[ok].sort_values("pred_bps", ascending=False).head(p.max_stocks).reset_index(drop=True)
    c["rank"] = np.arange(1, len(c) + 1)
    # edge model (fitted by scripts/research/overnight_sizing_backtest.py) and liquidity table
    ep = Path(runs_dir) / "overnight_edge.json"
    em = EdgeModel.load(ep) if ep.exists() else EdgeModel()
    notes = []
    if not ep.exists():
        notes.append("no runs/overnight_edge.json - using the prior edge (half the predicted gap); run "
                     "scripts/research/overnight_sizing_backtest.py")
    elif em.fitted_through and (day - pd.Timestamp(em.fitted_through)).days > 45:
        notes.append(f"the edge model was fitted through {em.fitted_through} - re-run overnight_sizing_backtest.py")
    tp = Path(data_dir) / "liquidity_table.parquet"
    if not tp.exists() or (pd.Timestamp.now() - pd.Timestamp(tp.stat().st_mtime, unit="s")).days >= 7:
        log("liquidity table missing or a week old - rebuilding it")
        L.build(root, archive, tp, log=log)
    table = L.load(tp)
    c["edge_bps"] = em.edge_bps(c["pred_bps"])
    c["pc_rs"], c["pre_rs"] = L.expected_rs(table, c["company_id"], c["tv_today"], c["tv20"])
    r = allocate(c, capital, p)
    if top:                                             # the same stocks with equal rupees, scored the same way
        eq = c.sort_values("rank").reset_index(drop=True)
        eq_qty = np.floor(np.where(eq["rank"] <= top, capital / top, 0.0) / eq["price"].to_numpy(float))
        e = expected_outcome(eq, eq_qty, p)
        o = expected_outcome(r, r["qty"].to_numpy(), p)
        r["eq_qty"] = eq_qty.astype(int)
        r["eq_fill_rs"], r["eq_impact_bps"], r["eq_profit_rs"] = e["fill_rs"], e["impact_bps"], e["profit_rs"]
        r["opt_fill_rs"], r["opt_impact_bps"], r["opt_profit_rs"] = o["fill_rs"], o["impact_bps"], o["profit_rs"]
    r["tradingsymbol"] = [str(s).split(":", 1)[-1] if isinstance(s, str) else f"{cid}-EQ"
                          for s, cid in zip(r.get("fyers_symbol", [None] * len(r)), r["company_id"])]
    buy = r[r["qty"] > 0]
    summ = {"day": f"{day:%Y-%m-%d}", "capital": capital, "invested": float(buy["x_rs"].sum()), "stocks": int(len(buy)),
            "top": top, "equal": {k: v for k, v in e.items() if not k.endswith(("_rs", "_bps"))} if top else None,
            "optimized": {k: v for k, v in o.items() if not k.endswith(("_rs", "_bps"))} if top else None,
            "exp_net_rs": float(buy["exp_net_rs"].sum()), "candidates": int(len(r)), "locked_excluded": n_locked,
            "edge_model": f"a {em.a:+.1f}, b {em.b:.2f} (through {em.fitted_through or 'prior'})", "notes": notes,
            "forecast": str(fpath), "friday": day.weekday() == 4, "min_price": floor,
            "surveillance_checked": surv_ok, "removed_untradable": removed}
    notes.extend(surv_notes)
    if len(buy) == 0:
        summ["why_cash"] = "no stock's expected move covers the costs tonight - stay in cash"
    elif summ["invested"] < 0.95 * capital:
        summ["why_cash"] = ("the rest stays in cash on purpose: the stocks with an edge are at their volume caps, or more "
                            "of them would cost more in auction impact than they are expected to earn")
    return r, summ
