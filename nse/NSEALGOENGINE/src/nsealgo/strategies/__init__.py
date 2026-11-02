"""Strategy registry. Enable/disable and override parameters under `strategies:` in config/system.yaml."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

from .base import Strategy
from .forced_flow import ForcedFlowHarvest
from .momentum import MomentumLeaders
from .orb import StocksInPlayORB
from .pead import EarningsGapDrift
from .pullback import PanicPullback
from .resid_reversion import ResidualReversion
from .skew_premium import LotterySkewPremium
from .vwap_fade import QuietDayVWAPFade

REGISTRY: dict[str, type[Strategy]] = {c.name: c for c in (
    StocksInPlayORB, QuietDayVWAPFade, MomentumLeaders, EarningsGapDrift, PanicPullback, ForcedFlowHarvest,
    ResidualReversion, LotterySkewPremium)}


def build_strategies(cfg: dict, names: list[str] | None = None, granularity: str | None = None) -> list[Strategy]:
    out = []
    for name, cls in REGISTRY.items():
        sc = dict(((cfg.get("strategies") or {}).get(name)) or {})
        if names is not None and name not in names:
            continue
        if names is None and not sc.get("enabled", cls.enabled_by_default):
            continue
        if granularity == "daily" and cls.horizon == "intraday":
            continue
        sc.pop("enabled", None)
        out.append(cls(sc))
    return out

# documented strategies from books, papers and forums (docs/05-reference/STRATEGY_CATALOGUE.md): research sleeves, off by default
from .factors import High52Momentum, LowVolatility, ResidualMomentum  # noqa: E402
from .intraday_patterns import CompressionBreakout, GapFade  # noqa: E402
from .timing import SupertrendTrend, TurtleBreakout  # noqa: E402

REGISTRY.update({c.name: c for c in (TurtleBreakout, SupertrendTrend, High52Momentum, LowVolatility, ResidualMomentum,
                                     CompressionBreakout, GapFade)})

# quote-driven microstructure strategies (docs/03-architecture/MICROSTRUCTURE.md): paper testing, off by default
from .microstructure import InventoryMarketMaker, OrderFlowScalper  # noqa: E402

REGISTRY.update({c.name: c for c in (OrderFlowScalper, InventoryMarketMaker)})
