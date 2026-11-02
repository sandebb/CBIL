"""
LINKED columns for the ALT-LINK model (config_alt_link.yaml, link_features: true): inputs whose meaning depends on
each other, handed to the model as one column instead of leaving it to find the link in noisy data.

Why (liquid stocks Oct 2019 - Oct 2026, next day vs the market, day-weighted): delivery % on its own predicts almost
nothing, but next to the day's move it does - after every kind of day high-delivery stocks beat low-delivery ones
(+7 .. +18 bps, t up to ~5), the gap is widest after big moves, and an up day on unusually LOW delivery fades
(-8 bps close-to-close, -15 bps open-to-close).

1. HAND links (LINK_HAND: from that evidence and market logic), per stock in features.stock_features.
   "Delivery surprise" = today's delivery % minus the stock's own 20-day average, in units of its 20-day spread.
   At 15:37 today's value is the nowcast (ALT-CLOSE family); with alt_lag: 0 it is the published value.
2. MINED links = the "system map" (LINK_PAIRS). The first build draws it ONCE, from the training window of the first
   test quarter only (no test data), and freezes it in <work folder>\\link_map.json (+ link_map.txt to read):
     a. the strongest inputs: LightGBM gain for next-day close-to-close, gap and open-to-close (summed);
     b. for every pair (A, B) of them: does B's effect on tomorrow change with A?  Each day the stocks are split into
        thirds by A; B's rank correlation with tomorrow's move (beyond what A itself explains: partial correlation)
        is measured in the top and in the bottom third; the difference, averaged over the days, gives a t value
        (also with A and B swapped);
     c. the strongest pairs are kept - near-duplicates skipped, every input in at most a few pairs.
   Each kept pair gets two columns from the day's cross-sectional ranks (bounded, scale-free, no division blow-ups):
     lp_A__B = rank A x rank B    "direct" link  (both high / both low)
     ld_A__B = rank A - rank B    "inverse" link (one high while the other is low)
   python -m nseml link-map shows the map; link-map --redo draws it again (the columns change -> the models retrain).
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import datetime as dt
import json
import math
import time

import numpy as np
import polars as pl

from .config import cfg, work_dir

LINK_HAND = [
    "lk_move_dlv",       # today's move (vol units) x today's delivery surprise       - conviction behind the move
    "lk_absmove_dlv",    # SIZE of today's move x delivery surprise                    - delivery matters most after big moves
    "lk_gap_dlv",        # today's opening gap (vol units) x delivery surprise         - was the gap accepted
    "lk_clv_dlv",        # where it closed in the day's range x delivery surprise      - strong hands at the close
    "lk_vol_dlv",        # volume surprise x delivery surprise                        - heavy volume on low delivery = churn
    "lk_dval_move",      # today's move x log(1 + delivered rupees / usual turnover)   - move backed by delivered money
    "lk_prev_move_dlv",  # yesterday's move x yesterday's PUBLISHED delivery surprise (exact, no nowcast)
    "lk_dlv_accel",      # today's delivery surprise minus yesterday's
    "lk_trend",          # 5-day price trend x 5-day delivery trend                    - accumulation / distribution
    "lk_short_move",     # latest short-selling surprise x today's move                - squeeze / pile-on
    "lk_mtf_rally",      # 5-day change in margin-funded shares x 5-day price trend     - leveraged chasing
    "lk_gap_vol",        # opening gap x volume surprise
]
TARGETS = {"close-to-close": "y_r_n", "gap": "y_g_n", "open-to-close": "oc_n"}
PREFIXES = ("lk_", "lp_", "ld_")


def _cfg() -> dict:
    return cfg().get("link_map", {}) or {}


def map_path():
    return work_dir() / "link_map.json"


def text_path():
    return work_dir() / "link_map.txt"


def load_pairs() -> list[tuple[str, str]]:
    p = map_path()
    if not p.exists():
        return []
    return [(x[0], x[1]) for x in json.loads(p.read_text(encoding="utf-8"))["pairs"]]


def pair_names(pairs) -> list[str]:
    return [f"lp_{a}__{b}" for a, b in pairs] + [f"ld_{a}__{b}" for a, b in pairs]


def show() -> str:
    p = text_path()
    return p.read_text(encoding="utf-8") if p.exists() else f"no link map yet ({map_path()}) - the first build draws it"


def day_rank(x: str) -> pl.Expr:
    """Centred percentile rank among the day's stocks (-0.5 .. +0.5); missing stays missing."""
    return (pl.col(x).rank("average") / pl.col(x).count()).over("datetime") - 0.5


# ---- 1. hand links ----------------------------------------------------------------------------------------------
def hand_links(d: pl.DataFrame, g, nowcast: bool, lag: int) -> pl.DataFrame:
    """LINK_HAND on per-stock rows, after the ALT (+ nowcast) features exist. g = per-stock window."""
    c, v = pl.col("close"), pl.col("volume").cast(pl.Float64)
    sd20 = g(pl.col("deliv_pct").rolling_std(20, min_samples=10))       # with alt_lag 1: the 20 days to yesterday
    if nowcast:
        tdz = pl.when(sd20 > 0).then(pl.col("deliv_now_vs20") / sd20)    # today, from the 15:37 nowcast
        dp_today = pl.col("a_dp_now")
        prev_dz = pl.col("deliv_z20")                                     # alt_lag 1: yesterday's published z-score
    elif lag == 0:
        tdz = pl.col("deliv_z20")                                         # today's published value (after 18:00)
        dp_today = pl.col("deliv_pct")
        prev_dz = g(pl.col("deliv_z20").shift(1))
    else:
        raise SystemExit("link_features with alt_lag >= 1 needs delivery_nowcast: true (today's delivery is not public yet)")
    mz = pl.col("lr_z")
    gz = pl.col("gap") / pl.col("sigma")
    dval = pl.when((dp_today >= 0) & (pl.col("med_tv60") > 0) & (v > 0)) \
             .then((1 + dp_today * v * c / pl.col("med_tv60")).log())
    d = d.with_columns(tdz.alias("_tdz"), prev_dz.alias("_pdz"))
    t, p = pl.col("_tdz"), pl.col("_pdz")
    return d.with_columns(
        lk_move_dlv=mz * t,
        lk_absmove_dlv=mz.abs() * t,
        lk_gap_dlv=gz * t,
        lk_clv_dlv=pl.col("clv") * t,
        lk_vol_dlv=pl.col("rel_vol") * t,
        lk_dval_move=mz * dval,
        lk_prev_move_dlv=g(mz.shift(1)) * p,
        lk_dlv_accel=t - p,
        lk_trend=pl.col("r5_z") * pl.col("deliv_5_20"),
        lk_short_move=pl.col("short_z60") * mz,
        lk_mtf_rally=pl.col("mtf_chg5") * pl.col("r5_z"),
        lk_gap_vol=gz * pl.col("rel_vol"),
    ).drop("_tdz", "_pdz")


# ---- 2. mined pairs ---------------------------------------------------------------------------------------------
def add_pairs(d: pl.DataFrame, pairs) -> pl.DataFrame:
    """The system map's pair columns (rows = the day's universe, like the cs_ features)."""
    if not pairs:
        return d
    r = {x: day_rank(x) for p in pairs for x in p}
    return d.with_columns([(r[a] * r[b]).cast(pl.Float32).alias(f"lp_{a}__{b}") for a, b in pairs]
                          + [(r[a] - r[b]).cast(pl.Float32).alias(f"ld_{a}__{b}") for a, b in pairs])


def window(days) -> tuple:
    """Training window of the first test quarter = the first of the last `test_folds` walk-forward folds."""
    from .splits import make_folds
    folds = make_folds(sorted(days))
    if not folds:
        raise SystemExit("link map: the data has no walk-forward folds")
    n = int(_cfg().get("test_folds", 12))
    f = folds[-n] if len(folds) >= n else folds[0]
    return f.train[0], f.train[1], f.label()


def mine(d: pl.DataFrame, candidates: list[str], log=print, days=None) -> list[tuple[str, str]]:
    """Draw the system map from the rows of `d` inside window(); write link_map.json / .txt; return the kept pairs."""
    import lightgbm as lgb
    c0 = _cfg()
    top_k, n_pairs = int(c0.get("top_features", 20)), int(c0.get("pairs", 8))
    per_feat, max_corr = int(c0.get("max_per_feature", 3)), float(c0.get("max_corr", 0.85))
    t0 = time.time()
    lo, hi, first = window(days if days is not None else d["datetime"].unique().to_list())
    rows = d.filter(pl.col("datetime").is_between(lo, hi), pl.col("y_r_n").is_not_null(), pl.col("y_g_n").is_not_null(),
                    pl.col("sigma") > 0)
    if rows.height < 20_000:
        raise SystemExit(f"link map: only {rows.height:,} stock-days in {lo:%Y-%m-%d}..{hi:%Y-%m-%d} - it needs the "
                         f"full dataset (python -m nseml build)")
    rows = rows.with_columns(((pl.col("y_r") - pl.col("y_g")) / pl.col("sigma")).alias("oc_n"))
    cov = rows.select([pl.col(x).is_not_null().mean().alias(x) for x in candidates]).row(0, named=True)
    thin = [x for x in candidates if (cov[x] or 0) < 0.3]
    cand = [x for x in candidates if x not in thin]
    # Is an ALT data source MISSING (backfill not run) or only SPARSE by nature? Judge each source by the share of the
    # window's DAYS on which any stock has a value: short selling is reported for only ~20-25 stocks a day, so its
    # inputs are empty for most stock-days even when the history is complete (that stopped the 22 Oct 2026 build).
    # Missing source -> stop (a frozen map must not be drawn without it). Sparse inputs are simply left out of the map.
    src_names = {"deliv_": "delivery", "short_": "short_selling", "mtf_": "mtf"}
    missing = []
    for pre, name in src_names.items():
        cols = [x for x in candidates if x.startswith(pre)]
        if not cols:
            continue
        day_cov = (rows.group_by("datetime").agg(pl.any_horizontal([pl.col(x).is_not_null() for x in cols]).any()
                                                 .alias("has")).get_column("has").mean())
        if (day_cov or 0) < 0.5:
            missing.append(f"{name} ({100 * (day_cov or 0):.0f}% of the days)")
    if missing and not c0.get("allow_missing_inputs"):
        raise SystemExit(f"link map: no history for {', '.join(missing)} in {lo:%Y-%m-%d}..{hi:%Y-%m-%d} - the map is "
                         f"frozen, so it is not drawn without them. Run NSEDATAENGINE's backfill first (python "
                         f"nse_alt_data.py backfill --start 2019-01-01 --only short_selling,mtf), then build again. "
                         f"(To draw it anyway: link_map: allow_missing_inputs: true in config_alt_link.yaml)")
    log(f"link map: window {lo:%Y-%m-%d}..{hi:%Y-%m-%d} = training data of {first} (the first test quarter), "
        f"{rows.height:,} stock-days, {len(cand)} inputs"
        + (f"; left out (data on < 30% of the days): {', '.join(thin)}" if thin else ""))

    # a. the strongest inputs
    smp = rows.sample(min(rows.height, int(c0.get("sample_rows", 600_000))), seed=7)
    X = smp.select(cand).to_numpy().astype(np.float32)
    prm = dict(objective="huber", learning_rate=0.1, num_leaves=31, min_data_in_leaf=max(200, smp.height // 500),
               feature_fraction=0.8, bagging_fraction=0.7, bagging_freq=1, max_bin=63, verbose=-1, seed=7)
    imp = np.zeros(len(cand))
    for t in TARGETS.values():
        y = smp[t].to_numpy().astype(np.float64)
        k = np.isfinite(y)
        b = lgb.train(prm, lgb.Dataset(X[k], label=y[k]), num_boost_round=200)
        gi = b.feature_importance(importance_type="gain")
        imp += gi / max(gi.sum(), 1e-12) / len(TARGETS)
    del X, smp
    top = [cand[i] for i in np.argsort(-imp)[:top_k]]
    log(f"link map: strongest inputs ({time.time() - t0:.0f}s): {', '.join(top)}")

    # b. does B's effect on tomorrow change with A?  (top third of A vs bottom third, per day; B's partial rank
    #    correlation with the target given A, so a curve in A alone - which the trees learn anyway - does not count)
    R = rows.select(["datetime"] + [day_rank(x).alias(x) for x in top + list(TARGETS.values())])
    R = R.with_columns([((pl.col(x) + 0.5) * 3).floor().clip(0, 2).cast(pl.Int8).alias(f"_t3_{x}") for x in top])
    res = {}                     # (A, B, target name) -> (B's IC in top third of A, in bottom third, t of the gap, days)
    for a in top:
        ta = f"_t3_{a}"
        others = [b for b in top if b != a]
        sub = R.filter(pl.col(ta).is_in([0, 2]))
        rba = sub.group_by("datetime", ta).agg([pl.corr(b, a).alias(f"_ba_{b}") for b in others])
        for tn, t in TARGETS.items():
            gb = (sub.group_by("datetime", ta).agg([pl.corr(b, t).alias(b) for b in others] + [pl.corr(a, t).alias("_at")])
                     .join(rba, on=["datetime", ta]))
            gb = gb.select(["datetime", ta] + [
                ((pl.col(b) - pl.col(f"_ba_{b}") * pl.col("_at"))
                 / ((1 - pl.col(f"_ba_{b}") ** 2) * (1 - pl.col("_at") ** 2)).sqrt()).alias(b) for b in others])
            j = gb.filter(pl.col(ta) == 2).drop(ta).join(gb.filter(pl.col(ta) == 0).drop(ta), on="datetime", suffix="_lo")
            for b in others:
                hi_ic, lo_ic = j[b].fill_nan(None), j[b + "_lo"].fill_nan(None)
                diff = (hi_ic - lo_ic).drop_nulls()
                n = diff.len()
                if n < 30:
                    continue
                sd = diff.std()
                tv = float(diff.mean() / sd * math.sqrt(n)) if sd else 0.0
                res[(a, b, tn)] = (float(hi_ic.mean()), float(lo_ic.mean()), tv, n)
    best = {}                    # (pair, target) -> strongest direction
    for (a, b, tn), (hic, lic, tv, n) in res.items():
        key = tuple(sorted((a, b)))
        if abs(tv) > best.get((key, tn), (0.0,))[0]:
            best[(key, tn)] = (abs(tv), a, b, tn, hic, lic, tv)
    score, desc = {}, {}
    for (key, tn), v in best.items():
        score[key] = score.get(key, 0.0) + v[0]
        if key not in desc or v[0] > desc[key][0]:
            desc[key] = v
    ranked = sorted(score.items(), key=lambda kv: -kv[1])

    # c. keep the strongest pairs (no near-duplicates, each input in at most per_feat pairs)
    def rcorr(a, b):
        x = R.select(pl.corr(a, b)).item()
        return abs(x) if x is not None and x == x else 0.0
    chosen, used, notes = [], {}, {}
    for i, (key, s) in enumerate(ranked):
        if len(chosen) >= n_pairs and i >= 40:
            break
        a, b = key
        rc = rcorr(a, b)
        if rc > max_corr:
            notes[key] = f"skipped: near-duplicates (rank corr {rc:.2f})"
        elif len(chosen) >= n_pairs:
            notes[key] = "below the cut"
        elif used.get(a, 0) >= per_feat or used.get(b, 0) >= per_feat:
            notes[key] = f"skipped: {a if used.get(a, 0) >= per_feat else b} already in {per_feat} pairs"
        else:
            chosen.append((a, b, s, rc))
            used[a], used[b] = used.get(a, 0) + 1, used.get(b, 0) + 1

    def explain(key):
        _, a, b, tn, hic, lic, tv = desc[key]
        return (f"{b}'s effect on next-day {tn}: IC {hic:+.3f} when {a} is high, {lic:+.3f} when {a} is low "
                f"(t {tv:+.1f})")

    # the hand links in the same window (univariate check: their own next-day rank IC)
    hand = [h for h in LINK_HAND if h in rows.columns and rows[h].is_not_null().mean() >= 0.3]
    hand_ic = {}
    if hand:
        H = rows.select(["datetime"] + [day_rank(x).alias(x) for x in hand + list(TARGETS.values())])
        for tn, t in TARGETS.items():
            gb = H.group_by("datetime").agg([pl.corr(h, t).alias(h) for h in hand])
            for h in hand:
                x = gb[h].fill_nan(None).drop_nulls()
                sd = x.std()
                hand_ic[(h, tn)] = (float(x.mean()), float(x.mean() / sd * math.sqrt(x.len())) if sd else 0.0)
    out = {"window": [f"{lo:%Y-%m-%d}", f"{hi:%Y-%m-%d}"], "first_test_fold": first, "stock_days": rows.height,
           "targets": TARGETS, "settings": {"top_features": top_k, "pairs": n_pairs, "max_per_feature": per_feat,
                                            "max_corr": max_corr},
           "inputs_left_out": thin, "top_inputs": [[x, round(float(imp[cand.index(x)]), 4)] for x in top],
           "pairs": [[a, b, round(s, 2), round(rc, 3), explain((a, b))] for a, b, s, rc in chosen],
           "ranked": [[k[0], k[1], round(s, 2), notes.get(k, ""), explain(k)] for k, s in ranked[:40]],
           "hand_links_ic": {f"{h}|{tn}": [round(v[0], 4), round(v[1], 2)] for (h, tn), v in hand_ic.items()},
           "created": f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}"}
    map_path().write_text(json.dumps(out, indent=1), encoding="utf-8")
    kept = {(a, b) for a, b, _, _ in chosen}
    txt = [f"SYSTEM MAP of the ALT-LINK model - drawn {out['created']}, frozen (redraw: link-map --redo)",
           f"Data: {rows.height:,} stock-days {lo:%Y-%m-%d} .. {hi:%Y-%m-%d} = the training window of {first}, the first "
           f"test quarter - nothing from the test quarters.", "",
           "Strongest inputs (share of LightGBM gain, next-day close-to-close + gap + open-to-close):"]
    txt += [f"  {i + 1:2d}. {x:22s} {100 * imp[cand.index(x)]:5.1f}%" for i, x in enumerate(top)]
    if thin:
        txt += [f"  (not considered - data on < 30% of the days: {', '.join(thin)})"]
    txt += ["", "Pairs KEPT -> columns lp_A__B (rank A x rank B, direct) and ld_A__B (rank A - rank B, inverse):"]
    for a, b, s, rc in chosen:
        txt += [f"  {a} <-> {b}   score {s:.1f}", f"      {explain((a, b))}"]
    txt += ["", "Next strongest pairs (not kept):"]
    txt += [f"  {k[0]} <-> {k[1]}   score {s:.1f}   {notes.get(k, '')}" for k, s in ranked[:25] if k not in kept]
    if hand:
        txt += ["", "Hand links in the same window - their own next-day rank IC (t), a univariate check only:",
                f"  {'':22s} " + "  ".join(f"{tn:>19s}" for tn in TARGETS)]
        txt += [f"  {h:22s} " + "  ".join(f"{hand_ic[(h, tn)][0]:+8.4f} (t {hand_ic[(h, tn)][1]:+5.1f})" for tn in TARGETS)
                for h in hand]
    txt += ["", "How to read it: IC = rank correlation of B with tomorrow's move beyond what A explains (partial),",
            "inside the top / bottom third of the day's stocks by A, averaged over the days. A big difference",
            "(|t| > 3) = A changes how B pulls the price. score = sum over the three targets of the stronger",
            "direction's |t|. Near-duplicate inputs (|rank corr| > max_corr) are never paired."]
    text_path().write_text("\n".join(txt) + "\n", encoding="utf-8")
    log(f"link map: {len(chosen)} pairs kept ({time.time() - t0:.0f}s) -> {map_path()}")
    for a, b, s, _ in chosen:
        log(f"   {a} <-> {b}  (score {s:.1f})")
    return [(a, b) for a, b, _, _ in chosen]
