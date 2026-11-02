r"""
Two models (A = the reference, B = the challenger), fold by fold, on the same walk-forward test windows.

    python compare_models.py                                   work\results\phase1  vs  work_alt\results\phase1_alt
    python compare_models.py --a work_alt_close\results\phase1_alt_close --b work_alt_link\results\phase1_alt_link
-> printed and written next to B's run folder: compare_<A run>_vs_<B run>.txt
Lower pinball = better forecast; higher rank IC = better ranking of stocks (what the trading rules use).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HOME = Path(__file__).resolve().parent
KEYS = [("y_g_n.pinball", "gap pinball", -1), ("y_r_n.pinball", "return pinball", -1), ("y_u_n.pinball", "upper wick pinball", -1),
        ("y_d_n.pinball", "lower wick pinball", -1), ("y_v.pinball", "volume pinball", -1), ("y_g.rank_ic", "gap rank IC", 1),
        ("y_r.rank_ic", "return rank IC", 1), ("oc.rank_ic", "open->close rank IC", 1), ("y_r.hit_rate", "direction hit rate", 1)]


def load(folder: Path, model: str) -> dict:
    out = {}
    for p in folder.glob(f"metrics_{model}_f*.json"):
        m = json.loads(p.read_text())
        out[m["fold"]] = m
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default=str(HOME / "work" / "results" / "phase1"))
    ap.add_argument("--b", default=str(HOME / "work_alt" / "results" / "phase1_alt"))
    ap.add_argument("--model", default="blend", help="lgbm | deeplob | blend")
    a = ap.parse_args()
    A, B = load(Path(a.a), a.model), load(Path(a.b), a.model)
    folds = sorted(set(A) & set(B))
    # a fold only counts when both runs tested the same days: a run made later has a longer last fold
    diff = [f for f in folds if (A[f]["test_start"], A[f]["test_end"]) != (B[f]["test_start"], B[f]["test_end"])]
    folds = [f for f in folds if f not in diff]
    if not folds:
        raise SystemExit(f"no common folds for model {a.model}: {a.a} has {len(A)}, {a.b} has {len(B)}")
    lines = [f"A = {a.a}", f"B = {a.b}", f"model {a.model}, {len(folds)} common folds "
             f"({A[folds[0]]['test_start']} .. {A[folds[-1]]['test_end']})"]
    for f in diff:
        lines.append(f"left out {f}: different test days (A {A[f]['test_start']}..{A[f]['test_end']}, "
                     f"B {B[f]['test_start']}..{B[f]['test_end']}) - re-run the older walk-forward to compare it")
    lines += ["",
             f"{'metric':22s} {'A':>10s} {'B':>10s} {'change':>9s} {'B better in':>14s}"]
    for k, label, sign in KEYS:
        va = [A[f].get(k) for f in folds]
        vb = [B[f].get(k) for f in folds]
        pairs = [(x, y) for x, y in zip(va, vb) if x is not None and y is not None and x == x and y == y]   # no NaN
        if not pairs:
            continue
        ma, mb = sum(x for x, _ in pairs) / len(pairs), sum(y for _, y in pairs) / len(pairs)
        better = sum(1 for x, y in pairs if (y - x) * sign > 0)
        ch = (mb / ma - 1) * 100 if sign < 0 and ma else (mb - ma)
        chs = f"{ch:+8.2f}%" if sign < 0 else f"{ch:+9.4f}"
        lines.append(f"{label:22s} {ma:10.4f} {mb:10.4f} {chs:>9s} {better:>6d} of {len(pairs)}")
    lines += ["", "pinball: change in % (negative = B better); rank IC / hit rate: change in absolute terms (positive = B better).",
              "Delivery data starts Aug 2019 and short selling / MTF when their backfill starts - early folds see little of it;",
              "judge mainly the folds from 2024 on (per-fold numbers: metrics_*.json in each folder)."]
    text = "\n".join(lines)
    print(text)
    Path(a.b).mkdir(parents=True, exist_ok=True)
    (Path(a.b).parent / f"compare_{Path(a.a).name}_vs_{Path(a.b).name}.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
