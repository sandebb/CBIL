"""Configuration: YAML + built-in defaults + ${ENV:-default} interpolation + .env support."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import os
import re
from copy import deepcopy
from pathlib import Path

import yaml

_ENV = re.compile(r"\$\{([A-Za-z0-9_]+)(?::-([^}]*))?\}")

_CODE_ALGO = Path(__file__).resolve().parents[2]
_ROOT_BASE = Path(os.environ.get('NSE_ROOT') or _CODE_ALGO.parents[1]).resolve()
_DATA_BASE = _ROOT_BASE / 'data'

DEFAULTS: dict = {
    "capital": 1_000_000,
    "paths": {
        "data_dir": str(_DATA_BASE / "NSEALGOENGINE" / "data"), "runs_dir": str(_DATA_BASE / "NSEALGOENGINE" / "runs"), "cache_dir": str(_DATA_BASE / "NSEALGOENGINE" / "cache"), "recordings_dir": str(_DATA_BASE / "NSEALGOENGINE" / "recordings"),
        "universe_dir": str(_CODE_ALGO / "config" / "universes"), "tbt_dir": str(_DATA_BASE / "NSEDATAENGINE" / "tbt"), "events_dir": str(_CODE_ALGO / "config" / "events"),
        "holidays_file": str(_CODE_ALGO / "config" / "nse_holidays.csv"),
    },
    "broker": {"execution": "paper", "data": "paper", "brokers": {}},
    "session": {
        "pre_market": "08:50", "open": "09:15", "intraday_entry_start": "09:30", "no_new_intraday": "14:30",
        "intraday_exit_cas": "15:05", "safety_flatten": "15:12", "intraday_exit_noncas": "15:20",
        "swing_eval": "15:10", "cas_start": "15:15", "cas_order_start": "15:20", "close": "15:30",
        "eod": "15:40", "poll_seconds": 1.0, "volume_fraction_1510": 0.85,
    },
    "risk": {
        "intraday_cap": 0.005, "swing_cap": 0.01, "kelly_fraction": 0.25, "prior_weight": 20,
        "min_trades_to_disable": 30, "daily_loss_limit": 0.03, "max_consecutive_losses": 3,
        "adv_cap": 0.0025, "gap_multiple": 2.0, "es_cap": 0.03, "es_alpha": 0.975, "es_lookback": 500,
        "max_swing_positions": 12, "max_per_sector": 2, "max_position_frac": 0.2,
        "intraday_gross_leverage": 3.0, "max_orders_per_day": 300, "max_order_value": 2_500_000,
        "limit_band_pct": 0.01, "min_risk_pct": 0.001,
        "books": {"intraday": 0.5, "swing": 0.5}, "swing_daily_loss_limit": 0.02, "depth_share": 0.25,
        "mis_margin_frac": 0.2,
    },
    "oms": {"orders_per_second": 8, "sync_seconds": 2.0, "reconcile_seconds": 60, "halt_on_mismatch": True,
            "protect_market_orders": True, "market_protection_pct": 0.005, "stop_limit_pct": 0.01,
            "registered_algo": False, "exit_chase_seconds": 5.0, "exit_chase_step": 0.002, "exit_chase_max": 0.02, "exit_chase_alert_after": 5,
            "entry_ttl_seconds": 90.0, "min_partial_fraction": 0.0},
    "costs": {
        "stt_intraday_sell": 0.00025, "stt_delivery": 0.001,
        "exch_eq": 0.0000307, "sebi": 0.000001,
        "stamp_intraday_buy": 0.00003, "stamp_delivery_buy": 0.00015,
        "gst": 0.18, "dp_charge": 15.93, "impact_y": 0.7, "half_spread_bps": 1.0,
        "brokerage": {"intraday_flat": 20.0, "intraday_pct": 0.0003, "delivery_flat": 0.0,
                      "delivery_pct": 0.0},
    },
    "paper": {"slippage_bps": 2.0, "participation": 0.25, "cas_start": "15:15"},
    "regime": {
        "mode": "both", "n_states": 3, "min_obs": 750, "refit_every": 5, "restarts": 3,
        "hysteresis_prob": 0.7, "hysteresis_days": 2, "seed": 7, "default_state": "turbulent",
        "index_key": "IDX:NIFTY500", "vix_key": "IDX:INDIAVIX",
    },
    "crowding": {
        "comomentum_pct": 0.8, "comomentum_throttle": 0.5, "dd_breaker_count": 3,
        "dd_breaker_mult": 0.5, "dd_cooldown_sessions": 10, "round_levels": [100, 50, 10], "round_zone_ticks": 3,
    },
    "gates": {"min_trades": 100, "min_sessions": 20, "max_dd_multiple": 1.5, "ci_alpha": 0.10},
    "compute": {"device": "auto", "gpu_dtype": "float32"},
    "monitor": {"enabled": True, "port": 0},
    "journal": {"save": True, "modes": ["forward", "live", "replay"]},
    "book": {"levels": 50, "decay": 0.5, "imbalance_levels": 5, "window_seconds": 30.0, "history": 600,
             "absorb_window_seconds": 60.0, "max_age_seconds": 5.0, "min_levels": 2},
    "feeds": {"redundancy": 2, "stale_seconds": 20, "record_raw": False, "disagree_bps": 10, "limits": {},
              "record_depth": 5, "depth_focus_seconds": 60,
              "depth_overlay": {"source": "none", "max_age": 2.0, "tbt_max_symbols": 15, "tbt_connections": 3,
                                "tbt_per_connection": 5, "upstox_connections": 5, "upstox_per_connection": 50,
                                "upstox_max_symbols": 250, "verify_seconds": 60},
              "watch": "none"},
    "system": {"keep_awake": "session", "disable_power_throttling": True, "worker_priority": "below_normal",
               "live_priority": "above_normal"},
    "fragility": {"enabled": True, "universe": "nifty50", "window": 60, "ar_frac": 0.2, "short": 15, "long": 252,
                  "threshold": 1.0, "action": "report", "min_names": 10},
    "parallel": {"workers": "auto", "use_smt": False, "split": "auto", "warmup_sessions": 120,
                 "runoff_sessions": 90, "min_block_daily": 250, "min_block_minute": 1, "io_threads": "auto",
                 "history_threads": 4},
    "notifier": {"telegram_token": "", "telegram_chat_id": ""},
    "strategies": {},
}


def load_dotenv(path: str | Path) -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _interp(v):
    if isinstance(v, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), v)
    if isinstance(v, dict):
        return {k: _interp(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_interp(x) for x in v]
    return v


def deep_merge(base: dict, over: dict | None) -> dict:
    out = deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = deepcopy(v)
    return out


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    user: dict = {}
    root = Path.cwd()
    if path:
        p = Path(path).resolve()
        root = p.parent.parent if p.parent.name == "config" else p.parent
        load_dotenv(root / ".env")
        user = yaml.safe_load(p.read_text(encoding="utf-8-sig")) or {}
    cfg = deep_merge(DEFAULTS, user)
    if overrides:
        cfg = deep_merge(cfg, overrides)
    cfg = _interp(cfg)
    cfg["_root"] = str(root)
    return cfg


def resolve_path(cfg: dict, key: str) -> Path:
    p = Path(cfg["paths"][key])
    return p if p.is_absolute() else Path(cfg.get("_root", ".")) / p
