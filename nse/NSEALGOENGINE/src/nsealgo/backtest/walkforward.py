"""Robustness checks: parameter perturbation (+/-20%) and in-sample vs out-of-sample splits, in parallel.

A strategy whose edge disappears when a parameter moves 20% was fitted to noise, not to a mechanism.
Every variant is an independent research backtest, so all of them run at once across the cores; the
regime/crowding timelines are computed once and shared.
"""
from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

from ..config import resolve_path
from ..data.history import HistoryStore
from ..parallel.hardware import workers_for
from ..parallel.jobs import variant_job
from ..parallel.pool import process_map
from ..strategies import REGISTRY, build_strategies
from .runner import _parallel_cfg, build_timelines


def perturbation(cfg: dict, strategy: str, start: date, end: date, params: list[str] | None = None,
                 granularity: str = "daily", history=None, master=None, scale: float = 0.2, workers=None) -> dict:
    cls = REGISTRY[strategy]
    base_params = {**cls.default_params, **((cfg.get("strategies") or {}).get(strategy) or {})}
    params = params or [k for k, v in cls.default_params.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    history = history or HistoryStore(resolve_path(cfg, "data_dir") / "history.db")
    out_dir = resolve_path(cfg, "runs_dir") / f"walkforward_{strategy}_{start}_{end}"
    variants = [("base", {})]
    for p in params:
        v = base_params[p]
        for f in (1 - scale, 1 + scale):
            nv = type(v)(round(v * f)) if isinstance(v, int) else v * f
            if nv != v:
                variants.append((f"{p}x{f:.1f}", {p: nv}))
    n = workers_for("cpu", workers if workers is not None else _parallel_cfg(cfg)["workers"], tasks=len(variants))
    from ..core.clock import NSECalendar
    days = NSECalendar(resolve_path(cfg, "holidays_file")).trading_days(start, end)
    tl = build_timelines(cfg, history, build_strategies(cfg, [strategy], granularity), days, master, n)
    specs = []
    for label, over in variants:
        c = copy.deepcopy(cfg)
        c.setdefault("strategies", {})[strategy] = {**base_params, **over, "enabled": True}
        specs.append({"cfg": c, "strategy": strategy, "variant": label, "start": start.isoformat(),
                      "end": end.isoformat(), "granularity": granularity, "history_path": history.path,
                      "master": master.df.reset_index(drop=True) if master is not None else None,
                      "run_dir": str(out_dir / label), "title": f"{strategy} {label}", "timelines": tl})
    from ..system import apply_process_policy, keep_awake, keep_awake_mode
    apply_process_policy(cfg, "main")
    with keep_awake(enabled=keep_awake_mode(cfg) != "off"):
        rows = process_map(variant_job, specs, n, label="parameter variants", policy=cfg.get("system"))
    base = rows[0]
    fragile = [r["variant"] for r in rows[1:] if base["expectancy"] and r["expectancy"] is not None
               and (r["expectancy"] <= 0 < base["expectancy"] or r["expectancy"] < 0.5 * base["expectancy"])]
    result = {"strategy": strategy, "rows": rows, "fragile_variants": fragile, "workers": n,
              "verdict": "robust" if not fragile else f"fragile to {len(fragile)} of {len(rows) - 1} variants"}
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "robustness.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


def split_test(cfg: dict, strategy: str, start: date, split: date, end: date, granularity: str = "daily",
               history=None, master=None) -> dict:
    from .runner import run_backtest
    a = run_backtest(cfg, start=start, end=split, granularity=granularity, names=[strategy], history=history, master=master,
                     run_dir=resolve_path(cfg, "runs_dir") / f"split_{strategy}_in", title=f"{strategy} in-sample")
    b = run_backtest(cfg, start=split, end=end, granularity=granularity, names=[strategy], history=history, master=master,
                     run_dir=resolve_path(cfg, "runs_dir") / f"split_{strategy}_out", title=f"{strategy} out-of-sample")
    return {"in_sample": a["strategies"].get(strategy), "out_of_sample": b["strategies"].get(strategy)}


def parallel_perturbation(cfg: dict, strategy: str, start: date, end: date, params=None, granularity: str = "daily",
                          scale: float = 0.2, max_workers=None, **kw) -> dict:
    """Alias with the signature from the multi-core design note."""
    return perturbation(cfg, strategy, start, end, params, granularity, scale=scale, workers=max_workers, **kw)
