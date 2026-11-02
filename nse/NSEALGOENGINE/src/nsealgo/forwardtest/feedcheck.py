"""Side-by-side live comparison of free broker feeds: which one is fastest, most complete, most stable.

Every listed provider streams the SAME instruments for N minutes (redundancy = number of providers),
the mesh measures each one, and a Markdown + JSON report is written. Run it during market hours.
"""
from __future__ import annotations

import json
import time as _t
from datetime import datetime
from pathlib import Path

from ..config import resolve_path
from ..core.clock import IST
from ..core.notifier import Notifier
from ..data.mesh import TickMesh
from .runner import connect_sources, data_names


def run_feed_check(cfg: dict, brokers: str, keys: list[str], minutes: float, out_dir: str | Path | None = None,
                   interactive: bool = True) -> dict:
    sources = connect_sources(cfg, data_names(brokers), interactive=interactive, notifier=Notifier({}))
    out = Path(out_dir or resolve_path(cfg, "runs_dir") / f"feedcheck_{datetime.now(IST):%Y%m%d_%H%M}")
    out.mkdir(parents=True, exist_ok=True)
    fc = cfg.get("feeds", {})
    mesh = TickMesh(sources, redundancy=len(sources), stale_seconds=float(fc.get("stale_seconds", 20)),
                    limits=fc.get("limits") or {}, raw_path=out / "raw_ticks.jsonl.gz",
                    disagree_bps=float(fc.get("disagree_bps", 10)))
    mesh.subscribe(keys)
    end = _t.time() + minutes * 60
    try:
        while _t.time() < end:
            mesh.poll(datetime.now(IST))
            _t.sleep(0.2)
    finally:
        rep = mesh.report()
        mesh.close()
    (out / "feedcheck.json").write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    (out / "feedcheck.md").write_text(to_markdown(rep), encoding="utf-8")
    rep["out_dir"] = str(out)
    return rep


def to_markdown(r: dict) -> str:
    lines = [f"# Feed check: {r['instruments']} instruments for {r['seconds']} s", "",
             "First share = how often this feed delivered a new print before the others. Lag = how far behind the "
             "fastest feed it delivered the same print. Latency = receipt time minus the exchange timestamp "
             "(only for feeds that send one; depends on your clock being NTP-synced).", "",
             "| Feed | Instruments | Msgs/s | First share | Lag p50 / p95 (s) | Latency p50 / p95 (s) | Duplicates | "
             "Stale | Stalls | Price disagreements |", "|---|---|---|---|---|---|---|---|---|---|"]
    for s in r["sources"]:
        lines.append(f"| {s['source']} | {s['instruments']}/{s['cap']} | {s['msgs_per_s']} | {s['first_share']:.0%} | "
                     f"{s['lag_p50_s']} / {s['lag_p95_s']} | {s['latency_p50_s']} / {s['latency_p95_s']} | "
                     f"{s['duplicates']} | {s['stale_rejected']} | {s['stalls']} | {s['disagreements']} |")
    lines += ["", f"Cross-feed price checks: {r['cross_checks']:,}; disagreement rate {r['disagreement_rate']:.3%}.",
              f"Instruments with no tick at all: {len(r['silent_instruments'])}"
              + (f" ({', '.join(r['silent_instruments'][:15])})" if r["silent_instruments"] else "") + ".",
              f"Instruments needing REST polling: {len(r['rest_polled'])}."]
    if r["events"]:
        lines += ["", "Events:", *[f"- {e}" for e in r["events"]]]
    return "\n".join(lines) + "\n"
