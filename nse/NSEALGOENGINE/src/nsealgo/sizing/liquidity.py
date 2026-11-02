"""
How much the two thin sessions of the overnight trade can take, per stock, as a share of the stock's daily volume:

  post-close session  15:40-16:00, market orders fill at the official close       -> the ENTRY (buy)
  pre-open auction    09:00-09:08, one price for everyone = the official open      -> the EXIT (sell)

Both are tiny next to the continuous session. Across liquid NSE stocks the post-close session trades a median ~0.04% of
the day's volume and the pre-open auction ~0.2-0.25%: in a stock trading Rs 25 cr a day the whole post-close session of
a typical day is about Rs 1 lakh.

Sources (volumes only - no prices, no returns):
  1. 1-minute archive (data/minute_archive) + NSE daily volume (NSEDATAENGINE raw_ohlcv): daily volume minus the sum of
     the 1-minute bars = the volume traded outside the continuous session. Fyers' 09:15 bar included the opening auction
     from Nov 2023 to Sep 2025 (outside volume = the post-close session alone) and leaves it out before and after
     (outside volume = auction + post-close). Each day is classified by the market-wide median of the outside share
     (below INCLUDED_MAX = auction included).
  2. pre-open recorder (NSEDATAENGINE data/preopen/YYYY-MM-DD.parquet): the auction's final quantity - exact.
  3. at-close snapshots (NSEDATAENGINE data/at_close/YYYY-MM-DD.parquet): daily volume minus the volume at 15:30 = the
     post-close session - exact.
Expected size tonight = share x today's volume^w x 20-day median^(1-w) (expected_rs): both sessions swell on busy days -
the post-close session mostly with the day's own volume (w ~0.78), the next morning's auction a little less (w ~0.63).
The day-to-day noise is large (a factor ~5 either way for the post-close session), hence the caps' safety margins.
Per stock: the median share of its most recent OBS_DAYS observations, shrunk toward the market-wide median when it has
few (log scale, weight n / (n + SHRINK_N)); a stock with no observations gets the market-wide median. Single days far
above any real session (PC_OBS_MAX / PRE_OBS_MAX - a volume adjusted for a later bonus in only one of the sources) are
dropped, and a stock whose median is above PC_MAX / PRE_MAX (a lasting mismatch between the sources) gets the market
median - erring on the small side only costs unused capacity.

    nsealgo liquidity-table        (re)build data/liquidity_table.parquet; the minute-day cache makes reruns quick
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import datetime as dt
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

INCLUDED_MAX = 0.0015   # market-wide median outside-share below this = Fyers' 09:15 bar held the auction that day
MIN_BARS = 370          # a full session has 375 one-minute bars; days with fewer are not used
OBS_DAYS = 250          # most recent observations used per stock
SHRINK_N = 20           # per-stock median shrunk toward the market median with weight n / (n + SHRINK_N)
FLOOR = 1e-6            # shares are floored here before taking logs
START = "2023-08-01"    # first day read from the minute archive
PC_OBS_MAX, PRE_OBS_MAX = 0.05, 0.20   # a single day above this is a data mismatch (e.g. volumes adjusted for a later
                                       # bonus in one source only), not a real session - dropped
PC_MAX, PRE_MAX = 0.002, 0.02          # a stock whose median is above this has a systematic volume mismatch between the
                                       # sources (seen: a few stocks around corporate actions) -> it gets the market median
ALL = "_ALL_"           # the market-wide row of the table
DEFAULT_PC, DEFAULT_PRE = 0.0003, 0.0025   # market medians measured Nov 2023 - Oct 2026, used while there is no data
W_PC, W_PRE = 0.78, 0.63  # how much a session follows TODAY's volume vs the stock's 20-day median (refitted by build):
                          # expected session = share x today^w x median20^(1 - w). Measured Nov 2023 - Oct 2026: the
                          # post-close session moves 0.78 with today's volume and 0.26 with the 20-day median (log-log),
                          # the next morning's auction 0.68 / 0.40.


def data_engine_home(root: Path) -> Path:
    env = os.environ.get("NSEDATAENGINE_HOME", "").strip().strip('"')
    return Path(env) if env else Path(root).resolve().parent / "NSEDATAENGINE"


def paths(root: Path, minute_archive: Path) -> dict:
    """NSEDATAENGINE files through its contract when it imports, else the default layout."""
    home = data_engine_home(root)
    out = {"raw": home / "data" / "raw_ohlcv.parquet", "preopen": home / "data" / "preopen",
           "at_close": home / "data" / "at_close", "archive": Path(minute_archive),
           "surveillance": home / "data_ref" / "surveillance.parquet", "raw_fresh": home / "data_fresh" / "raw_ohlcv.parquet"}
    try:
        if str(home) not in sys.path:
            sys.path.insert(0, str(home))
        import nse_contract as C
        out.update(raw=C.file_path("raw_ohlcv", "master"), preopen=C.location("preopen"), at_close=C.location("at_close"))
        try:
            out.update(surveillance=C.file_path("surveillance", "ref"), raw_fresh=C.file_path("raw_ohlcv", "fresh"))
        except Exception:
            pass
    except Exception:
        pass
    return out


def _empty(*cols: str) -> pd.DataFrame:
    """An empty frame with usable dtypes (company_id text, date datetime64[ns], the rest float) - so merges still work."""
    return pd.DataFrame({c: pd.Series(dtype=object if c == "company_id" else "datetime64[ns]" if c == "date" else float)
                         for c in cols})


def _ts(s) -> pd.Series:
    return pd.to_datetime(s).astype("datetime64[ns]")


def update_minute_days(archive: Path, cache: Path, start: str = START, max_seconds: float | None = None,
                       log=print) -> pd.DataFrame:
    """Per stock and day: sum of the archive's 1-minute volumes and number of bars (company_id, date, min_vol, n_bars),
    cached in `cache`. Incremental: a stock is read again only from its newest cached day on (that day is replaced,
    so a day cached while the archive was still incomplete gets corrected)."""
    cols = ["company_id", "date", "min_vol", "n_bars", "open0"]
    have = pd.read_parquet(cache) if cache.exists() else _empty(*cols)
    if len(have):
        have["date"] = _ts(have["date"])
    newest = have.groupby("company_id")["date"].max().to_dict() if len(have) else {}
    s0 = pd.Timestamp(start)
    t0, parts, done = time.time(), [], 0
    dirs = sorted(p for p in Path(archive).iterdir() if p.is_dir()) if Path(archive).exists() else []
    for d in dirs:
        since = newest.get(d.name)
        files = []
        for f in sorted(d.glob("*_*.parquet")):
            try:
                end = pd.Timestamp(f.stem.split("_")[1])
            except (ValueError, IndexError):
                continue
            if end >= s0 and (since is None or end >= since):
                files.append(f)
        if not files:
            continue
        m = pd.concat([pd.read_parquet(f, columns=["ts", "open", "volume"]) for f in files], ignore_index=True)
        m = m.drop_duplicates("ts").sort_values("ts")
        if m.empty:
            continue
        m["date"] = _ts(m["ts"]).dt.normalize()
        g = m.groupby("date").agg(min_vol=("volume", "sum"), n_bars=("volume", "size"), open0=("open", "first")).reset_index()
        g = g[g["date"] >= max(s0, since if since is not None else s0)]
        g.insert(0, "company_id", d.name)
        parts.append(g)
        done += 1
        if max_seconds and time.time() - t0 > max_seconds:
            log(f"liquidity: time budget reached after {done} stocks - run again to continue")
            break
    if parts:
        new = pd.concat(parts, ignore_index=True)
        have = (pd.concat([have, new], ignore_index=True) if len(have) else new) \
            .drop_duplicates(["company_id", "date"], keep="last").sort_values(["company_id", "date"])
        cache.parent.mkdir(parents=True, exist_ok=True)
        have.to_parquet(cache, index=False)
        log(f"liquidity: minute-day cache {len(have):,} stock-days, {have['company_id'].nunique()} stocks "
            f"({done} read, {time.time() - t0:.0f}s) -> {cache}")
    return have


def daily_volume(raw_path: Path, start: str = START) -> pd.DataFrame:
    raw = pd.read_parquet(raw_path, columns=["company_id", "datetime", "open", "volume"],
                          filters=[("datetime", ">=", pd.Timestamp(start))])
    raw = raw.rename(columns={"datetime": "date"})
    raw["date"] = _ts(raw["date"])
    return raw[raw["volume"] > 0]


def archive_days(md: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    """company_id, date, volume, outside (= daily volume - 1-minute volume), share, included (auction inside the
    09:15 bar that day, so outside = post-close session only). Days whose daily bar was split-adjusted after the fact
    (NSEDATAENGINE's open differs from the archive's first bar by > 1%) are left out - their volumes do not compare."""
    md = md.astype({"min_vol": float, "n_bars": float})
    raw = raw.astype({"volume": float})
    x = md[md["n_bars"] >= MIN_BARS].merge(raw, on=["company_id", "date"])
    if "open0" in x.columns and "open" in x.columns:
        x = x[((x["open0"] / x["open"]) - 1).abs() <= 0.01]
    x["outside"] = (x["volume"] - x["min_vol"]).clip(lower=0)
    x["share"] = x["outside"] / x["volume"]
    med = x.groupby("date")["share"].median()
    x["included"] = x["date"].map(med) < INCLUDED_MAX
    return x


def recorded_preopen(folder: Path) -> pd.DataFrame:
    """company_id, date, final_qty from the pre-open recorder (last snapshot of each day = the auction result)."""
    parts = []
    for f in sorted(Path(folder).glob("????-??-??.parquet")) if Path(folder).exists() else []:
        s = pd.read_parquet(f, columns=["company_id", "date", "snap", "final_qty"])
        s = s.sort_values("snap").drop_duplicates("company_id", keep="last")
        parts.append(s[["company_id", "date", "final_qty"]])
    if not parts:
        return _empty("company_id", "date", "final_qty")
    out = pd.concat(parts, ignore_index=True)
    out["date"] = _ts(out["date"])
    return out[out["final_qty"] > 0]


def recorded_close(folder: Path) -> pd.DataFrame:
    """company_id, date, v1530 (the day's volume at 15:30) from the at-close snapshots."""
    parts = []
    for f in sorted(Path(folder).glob("????-??-??.parquet")) if Path(folder).exists() else []:
        s = pd.read_parquet(f, columns=["company_id", "datetime", "kind", "volume"])
        s = s[s["kind"] == "stock"].rename(columns={"datetime": "date", "volume": "v1530"})
        parts.append(s[["company_id", "date", "v1530"]])
    if not parts:
        return _empty("company_id", "date", "v1530")
    out = pd.concat(parts, ignore_index=True)
    out["date"] = _ts(out["date"])
    return out[out["v1530"] > 0]


def _cat(frames) -> pd.DataFrame:
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else _empty("company_id", "date", "share")


def _today_weight(y, v_today, v20, default: float) -> float:
    """w in  log(session) = c + b1 log(today's volume) + b2 log(20-day median),  w = b1 / (b1 + b2)."""
    ok = np.isfinite(y) & np.isfinite(v_today) & np.isfinite(v20)
    if ok.sum() < 5000:
        return default
    X = np.column_stack([np.ones(ok.sum()), v_today[ok], v20[ok]])
    b = np.linalg.lstsq(X, y[ok], rcond=None)[0]
    return float(np.clip(b[1] / (b[1] + b[2]), 0.0, 1.0)) if b[1] + b[2] > 0 else default


def _weights(x: pd.DataFrame) -> tuple[float, float]:
    """The two volume weights from the archive days (x: archive_days output)."""
    x = x.sort_values(["company_id", "date"]).copy()
    g = x.groupby("company_id")
    x["v20"] = g["volume"].transform(lambda s: s.shift(1).rolling(20, min_periods=10).median())
    x["out_next"], x["incl_next"] = g["outside"].shift(-1), g["included"].shift(-1)
    lv, l20 = np.log(x["volume"].to_numpy(float)), np.log(x["v20"].to_numpy(float))
    with np.errstate(divide="ignore", invalid="ignore"):
        pc_ok = (x["included"] & (x["outside"] > 0) & (x["share"] <= PC_OBS_MAX)).to_numpy()
        y = np.where(pc_ok, np.log(x["outside"].to_numpy(float)), np.nan)
        w_pc = _today_weight(y, lv, l20, W_PC)
        pre_ok = ((x["incl_next"] == False) & (x["out_next"] > 0)).to_numpy()   # noqa: E712
        y = np.where(pre_ok, np.log(x["out_next"].to_numpy(float)), np.nan)
        w_pre = _today_weight(y, lv, l20, W_PRE)
    return w_pc, w_pre


def _per_stock(obs: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """obs: company_id, date, share -> per-stock shrunk median (share, n, raw_median) and the market-wide median."""
    if obs.empty:
        return pd.DataFrame(columns=["share", "n", "raw_median"]), float("nan")
    obs = obs.assign(share=pd.to_numeric(obs["share"], errors="coerce").astype(float)).dropna(subset=["share"])
    if obs.empty:
        return pd.DataFrame(columns=["share", "n", "raw_median"]), float("nan")
    obs = obs.sort_values("date").groupby("company_id").tail(OBS_DAYS)
    med_all = float(obs["share"].median())
    g = obs.groupby("company_id")["share"].agg(["median", "size"]).astype(float)
    lm = (g["size"] * np.log(g["median"].clip(lower=FLOOR)) + SHRINK_N * np.log(max(med_all, FLOOR))) / (g["size"] + SHRINK_N)
    return pd.DataFrame({"share": np.exp(lm), "n": g["size"].astype(int), "raw_median": g["median"]}), med_all


def build(root: Path, minute_archive: Path, out: Path, log=print, max_seconds: float | None = None) -> pd.DataFrame:
    """The liquidity table: company_id, pc_share (post-close session / daily volume), pre_share (pre-open auction /
    daily volume), n_pc, n_pre (observations behind each), plus the market-wide row ALL. Written to `out`."""
    p = paths(root, minute_archive)
    md = update_minute_days(p["archive"], Path(out).with_name("minute_day_volume.parquet"), max_seconds=max_seconds,
                            log=log)
    raw = daily_volume(p["raw"])
    pc_obs, pre_obs = [], []
    w_pc, w_pre = W_PC, W_PRE
    if len(md):
        x = archive_days(md, raw)
        w_pc, w_pre = _weights(x)
        pc_obs.append(x.loc[x["included"], ["company_id", "date", "share"]])
        out_ex = x.loc[~x["included"], ["company_id", "date", "share"]]
    else:
        out_ex = _empty("company_id", "date", "share")
    rc = recorded_close(p["at_close"]).merge(raw, on=["company_id", "date"])
    rc = rc[rc["volume"] >= rc["v1530"]]
    pc_obs.append(rc.assign(share=(rc["volume"] - rc["v1530"]) / rc["volume"])[["company_id", "date", "share"]])
    pc_cat = _cat(pc_obs)
    pc, pc_all = _per_stock(pc_cat[pc_cat["share"] <= PC_OBS_MAX])
    pc_all = pc_all if np.isfinite(pc_all) else DEFAULT_PC
    pc = pc[pc["raw_median"] <= PC_MAX]                  # lasting source mismatch -> market median (below)
    rp = recorded_preopen(p["preopen"]).merge(raw, on=["company_id", "date"])
    pre_obs.append(rp.assign(share=(rp["final_qty"] / rp["volume"]).clip(upper=1.0))[["company_id", "date", "share"]])
    if len(out_ex):                                      # auction + post-close days: the auction = outside - post-close
        out_ex = out_ex[out_ex["company_id"].isin(pc.index)]          # only stocks whose post-close share is reliable
        pcs = out_ex["company_id"].map(pc["share"])
        pre_obs.append(out_ex.assign(share=(out_ex["share"] - pcs).clip(lower=0)))
    pre_cat = _cat(pre_obs)
    pre, pre_all = _per_stock(pre_cat[pre_cat["share"] <= PRE_OBS_MAX])
    pre_all = pre_all if np.isfinite(pre_all) else DEFAULT_PRE
    pre = pre[pre["raw_median"] <= PRE_MAX]
    t = pc.rename(columns={"share": "pc_share", "n": "n_pc", "raw_median": "pc_raw"}).join(
        pre.rename(columns={"share": "pre_share", "n": "n_pre", "raw_median": "pre_raw"}), how="outer")
    t["pc_share"] = t["pc_share"].fillna(pc_all)
    t["pre_share"] = t["pre_share"].fillna(pre_all)
    t[["n_pc", "n_pre"]] = t[["n_pc", "n_pre"]].fillna(0).astype(int)
    t = t.reset_index().rename(columns={"index": "company_id"})
    t = pd.concat([t, pd.DataFrame([{"company_id": ALL, "pc_share": pc_all, "pre_share": pre_all,
                                     "n_pc": int(t["n_pc"].sum()), "n_pre": int(t["n_pre"].sum()),
                                     "pc_raw": pc_all, "pre_raw": pre_all}])], ignore_index=True)
    t["w_pc"], t["w_pre"] = w_pc, w_pre
    t["built"] = pd.Timestamp.now().normalize()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    t.to_parquet(out, index=False)
    log(f"liquidity table: {len(t) - 1} stocks; market median post-close {100 * pc_all:.3f}% and pre-open auction "
        f"{100 * pre_all:.3f}% of daily volume (weight of today's volume {w_pc:.2f} / {w_pre:.2f}); {int(len(rc))} exact "
        f"post-close and {int(len(rp))} exact auction observations -> {out}")
    return t


def load(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def shares(table: pd.DataFrame, ids) -> tuple[np.ndarray, np.ndarray]:
    """(post-close share, auction share) per company id; the market-wide values for stocks not in the table."""
    t = table.set_index("company_id")
    pc0, pre0 = float(t.loc[ALL, "pc_share"]), float(t.loc[ALL, "pre_share"])
    ids = pd.Series(list(ids))
    return (ids.map(t["pc_share"]).fillna(pc0).to_numpy(float), ids.map(t["pre_share"]).fillna(pre0).to_numpy(float))


def expected_rs(table: pd.DataFrame, ids, tv_today, tv20) -> tuple[np.ndarray, np.ndarray]:
    """Expected rupee size of tonight's post-close session and tomorrow's pre-open auction per stock, from today's
    turnover (known at 15:30) and the 20-day median turnover up to yesterday."""
    pc, pre = shares(table, ids)
    t = table.set_index("company_id")
    w_pc = float(t.loc[ALL, "w_pc"]) if "w_pc" in t.columns else W_PC
    w_pre = float(t.loc[ALL, "w_pre"]) if "w_pre" in t.columns else W_PRE
    tt = np.asarray(tv_today, float)
    t20 = np.asarray(tv20, float)
    t20 = np.where(np.isfinite(t20) & (t20 > 0), t20, tt)
    tt = np.where(np.isfinite(tt) & (tt > 0), tt, t20)
    return pc * tt ** w_pc * t20 ** (1 - w_pc), pre * tt ** w_pre * t20 ** (1 - w_pre)
