"""
Event study of the order-flow scalp triggers on recorded Fyers TBT depth (scalp plan step 3: "per-trigger event
study vs the cost line"). Reads the full-book parquet files of ${NSE_ROOT}/code/Fyerstbt\\<yyyymmdd> (one row = one book
update holding all 50 levels).

Triggers (per stock, 60 s cool-down, first 10 minutes = warm-up)
  A1  best-level OFI (Cont et al.) summed over the last 10 s, depth-normalised, z-score > 3 (long) / < -3 (short)
  A5  the same with multi-level OFI, levels 1-5
  A5t A5 + "thin opposite side": the side we trade into has less than its usual depth in levels 1-10
  IMB book imbalance of levels 1-5 crossing +/-0.5
  RND random times and sides, same count as A5: the cost line a trigger has to beat
Execution as live: 350 ms latency (p95 data age), buy at the ask / sell at the bid on entry and on exit (the spread
is paid twice), exits after 30 s, 1, 2 and 5 minutes. Net = after spread and NSE intraday charges.
Setup B (absorption) needs traded quantities, which this recording does not have (no ltq / vtt_diff).

    python scripts\\research\\tbt_event_study.py ${NSE_ROOT}/code/Fyerstbt\\20261006
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import sys
from pathlib import Path

import numpy as np
import pandas as pd

LAT_NS = 350_000_000
HORIZONS = (30, 60, 120, 300)
COOLDOWN_S, WARMUP_S, Z = 60, 600, 3.0
K = 10                                   # levels used


def charges_bps(price, value=100_000, brokerage_per_order=5.0):
    """Intraday (MIS) round trip, bps of value. Brokerage: Shoonya, Rs 5 or 0.03% per order (lower). STT 0.025% sell, NSE txn 0.00297% x2, SEBI, stamp 0.003% buy,
    GST 18% on brokerage + txn + SEBI."""
    brk = 2 * min(brokerage_per_order, value * 0.0003) if brokerage_per_order else 0.0
    txn, sebi = 2 * value * 0.0000297, 2 * value * 1e-6
    rs = brk + value * 0.00025 + txn + sebi + value * 0.00003 + 0.18 * (brk + txn + sebi)
    return rs / value * 1e4


def load(day_dir: Path) -> pd.DataFrame:
    files = sorted(day_dir.glob("*.parquet"))
    df = pd.concat([pd.read_parquet(f, columns=["ts", "symbol", "bid_px", "bid_qty", "ask_px", "ask_qty"])
                    for f in files], ignore_index=True)
    df["symbol"] = df["symbol"].astype(str)
    return df


def arrays(g: pd.DataFrame):
    g = g.sort_values("ts")
    t = g["ts"].astype("int64").to_numpy()
    def mat(col, fill):
        out = np.full((len(g), K), fill, dtype=float)
        for i, v in enumerate(g[col].to_numpy()):
            v = np.asarray(v[:K], dtype=float)
            out[i, :len(v)] = v
        return out
    return t, mat("bid_px", np.nan), mat("bid_qty", 0), mat("ask_px", np.nan), mat("ask_qty", 0)


def ofi(bp, bq, ap, aq, levels):
    """Cont-Kukanov-Stoikov order-flow imbalance per update, summed over the first `levels` levels."""
    e = np.zeros(len(bp))
    for k in range(levels):
        b0, b1, qb0, qb1 = bp[:-1, k], bp[1:, k], bq[:-1, k], bq[1:, k]
        a0, a1, qa0, qa1 = ap[:-1, k], ap[1:, k], aq[:-1, k], aq[1:, k]
        eb = np.where(b1 >= b0, qb1, 0) - np.where(b1 <= b0, qb0, 0)
        ea = np.where(a1 <= a0, qa1, 0) - np.where(a1 >= a0, qa0, 0)
        e[1:] += np.nan_to_num(eb - ea)
    return e


def triggers(t, x, thr, side_sign=True):
    """Indices where x crosses above thr (long) or below -thr (short), with the cool-down."""
    out, last = [], -1e30
    t0 = t[0]
    for i in range(1, len(x)):
        if (t[i] - t0) / 1e9 < WARMUP_S or (t[i] - last) / 1e9 < COOLDOWN_S:
            continue
        if x[i] > thr >= x[i - 1]:
            out.append((i, 1)); last = t[i]
        elif x[i] < -thr <= x[i - 1]:
            out.append((i, -1)); last = t[i]
    return out


def outcomes(t, bp0, ap0, trig):
    rows = []
    mid = (bp0 + ap0) / 2
    for i, s in trig:
        j = np.searchsorted(t, t[i] + LAT_NS)
        if j >= len(t) or not (bp0[j] > 0 and ap0[j] > 0):
            continue
        entry = ap0[j] if s > 0 else bp0[j]
        r = {"i": i, "side": s, "t": t[i], "price": entry, "spread_bps": (ap0[j] - bp0[j]) / mid[j] * 1e4}
        for h in HORIZONS:
            k = np.searchsorted(t, t[j] + h * 1_000_000_000)
            if k >= len(t):
                r[f"g{h}"] = r[f"m{h}"] = np.nan
                continue
            exitp = bp0[k] if s > 0 else ap0[k]
            r[f"g{h}"] = s * (exitp / entry - 1) * 1e4                 # after paying the spread twice
            r[f"m{h}"] = s * (mid[k] / mid[i] - 1) * 1e4                 # mid-to-mid from the trigger
        rows.append(r)
    return rows


def study(day_dir: Path) -> str:
    df = load(day_dir)
    rng = np.random.default_rng(7)
    res = {k: [] for k in ("A1", "A5", "A5t", "IMB", "RND")}
    for sym, g in df.groupby("symbol"):
        t, bp, bq, ap, aq = arrays(g)
        ok = (bp[:, 0] > 0) & (ap[:, 0] > bp[:, 0])
        t, bp, bq, ap, aq = t[ok], bp[ok], bq[ok], ap[ok], aq[ok]
        if len(t) < 1000:
            continue
        ts = pd.to_datetime(t)
        depth = pd.Series((bq[:, :5].sum(1) + aq[:, :5].sum(1)) / 2, index=ts).rolling("30min", min_periods=50).median()
        for name, lv in (("A1", 1), ("A5", 5)):
            e = pd.Series(ofi(bp, bq, ap, aq, lv), index=ts)
            w = e.rolling("10s").sum() / depth.where(depth > 0)
            z = ((w - w.rolling("30min", min_periods=200).mean()) / w.rolling("30min", min_periods=200).std()).to_numpy()
            tr = triggers(t, np.nan_to_num(z), Z)
            rows = outcomes(t, bp[:, 0], ap[:, 0], tr)
            for r in rows:
                r["sym"] = sym
            res[name] += rows
            if name == "A5":
                askd = pd.Series(aq.sum(1), index=ts)
                bidd = pd.Series(bq.sum(1), index=ts)
                askm = askd.rolling("30min", min_periods=50).median().to_numpy()
                bidm = bidd.rolling("30min", min_periods=50).median().to_numpy()
                thin = [(i, s) for i, s in tr if (s > 0 and askd.iat[i] < askm[i]) or (s < 0 and bidd.iat[i] < bidm[i])]
                res["A5t"] += [dict(r, sym=sym) for r in outcomes(t, bp[:, 0], ap[:, 0], thin)]
                n = max(len(tr), 1)
                pick = rng.choice(np.arange(len(t)), size=n, replace=False)
                rnd = [(int(i), int(rng.choice([-1, 1]))) for i in sorted(pick) if (t[i] - t[0]) / 1e9 >= WARMUP_S]
                res["RND"] += [dict(r, sym=sym) for r in outcomes(t, bp[:, 0], ap[:, 0], rnd)]
        imb = (bq[:, :5].sum(1) - aq[:, :5].sum(1)) / np.maximum(bq[:, :5].sum(1) + aq[:, :5].sum(1), 1)
        res["IMB"] += [dict(r, sym=sym) for r in outcomes(t, bp[:, 0], ap[:, 0], triggers(t, imb, 0.5))]

    span = (df["ts"].min(), df["ts"].max())
    lines = [f"TBT event study  {day_dir}  {df['symbol'].nunique()} stocks  {span[0]:%Y-%m-%d %H:%M} - {span[1]:%H:%M}",
             f"350 ms latency; enter at the ask/bid, exit at the bid/ask (spread paid twice). bps = 1/100 of 1%.",
             f"intraday charges per round trip on Rs 1 lakh (Shoonya, Rs 5 / order): {charges_bps(1000):.1f} bps "
             f"(Rs 20 / order brokers: {charges_bps(1000, brokerage_per_order=20):.1f} bps)", ""]
    cost0 = charges_bps(1000)
    hdr = f"{'trigger':5s} {'n':>5s} {'spread':>6s} | " + " | ".join(f"{h:>3d}s mid  after-spread  net  win%" for h in HORIZONS)
    lines.append(hdr)
    for name, rows in res.items():
        d = pd.DataFrame(rows)
        if d.empty:
            lines.append(f"{name:5s}     0")
            continue
        parts = []
        for h in HORIZONS:
            g, m = d[f"g{h}"].dropna(), d[f"m{h}"].dropna()
            net = g - cost0
            tstat = net.mean() / (net.std(ddof=1) / np.sqrt(len(net))) if len(net) > 2 else np.nan
            parts.append(f"{m.mean():+7.1f} {g.mean():+11.1f} {net.mean():+6.1f} {100 * (net > 0).mean():4.0f}"
                         + ("*" if tstat > 2 else " "))
        lines.append(f"{name:5s} {len(d):5d} {d['spread_bps'].mean():6.1f} | " + " | ".join(parts))
    lines += ["", "mid = mid-price move in the trade's direction from the trigger (the signal, before any cost)",
              "after-spread = what a trader gets after crossing the spread in and out; net = also after charges",
              "* = net mean more than 2 standard errors above zero",
              "", "triggers per stock (A5): " + ", ".join(f"{k.split(':')[1][:-3]} {v}" for k, v in
                                                       pd.DataFrame(res['A5'])['sym'].value_counts().items())
              if res["A5"] else ""]
    return "\n".join(lines)


if __name__ == "__main__":
    d = Path(sys.argv[1] if len(sys.argv) > 1 else __import__("os").environ.get("NSE_ROOT", r"D:\CBIL") + r"/code\Fyerstbt\20261006")
    text = study(d)
    print(text)
    (d / "event_study.txt").write_text(text, encoding="utf-8")
