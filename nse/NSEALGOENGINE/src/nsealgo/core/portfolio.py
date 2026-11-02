"""Position book per (strategy, instrument) with round-trip trade records and R-multiples."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .models import Fill, Side


@dataclass
class PosState:
    strategy: str
    key: str
    product: str
    qty: int = 0
    avg: float = 0.0
    entry_ts: str = ""
    risk_ps: float = 0.0        # initial risk per share (entry - stop) for R-multiples
    costs: float = 0.0
    realized: float = 0.0
    entry_qty: int = 0
    exit_notional: float = 0.0
    exit_qty: int = 0
    meta: dict = field(default_factory=dict)


@dataclass
class Trade:
    strategy: str
    key: str
    direction: int
    qty: int
    entry_ts: str
    exit_ts: str
    entry_price: float
    exit_price: float
    pnl_gross: float
    costs: float
    pnl_net: float
    r_multiple: float
    product: str
    meta: dict = field(default_factory=dict)


class Portfolio:
    def __init__(self, capital: float):
        self.capital = float(capital)
        self.realized = 0.0
        self.costs = 0.0
        self.pos: dict[tuple[str, str], PosState] = {}
        self.marks: dict[str, float] = {}

    def mark(self, key: str, price: float | None) -> None:
        if price and price > 0:
            self.marks[key] = float(price)

    def unrealized(self, product: str | None = None) -> float:
        return sum(p.qty * (self.marks.get(p.key, p.avg) - p.avg) for p in self.pos.values()
                   if product is None or p.product == product)

    @property
    def equity(self) -> float:
        return self.capital + self.realized - self.costs + self.unrealized()

    def get(self, strategy: str, key: str) -> PosState | None:
        p = self.pos.get((strategy, key))
        return p if p and p.qty != 0 else None

    def positions(self, strategy: str | None = None, product: str | None = None) -> list[PosState]:
        return [p for p in self.pos.values() if p.qty != 0 and (strategy is None or p.strategy == strategy)
                and (product is None or p.product == product)]

    def net_by_key(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.pos.values():
            out[p.key] = out.get(p.key, 0) + p.qty
        return {k: v for k, v in out.items() if v}

    def gross_exposure(self, product: str | None = None) -> float:
        return sum(abs(p.qty) * self.marks.get(p.key, p.avg) for p in self.positions(product=product))

    def on_fill(self, f: Fill, meta: dict | None = None) -> Trade | None:
        meta = meta or {}
        k = (f.strategy, f.key)
        product = getattr(f.product, "value", f.product)
        q = f.qty * (1 if f.side == Side.BUY else -1)
        self.costs += f.costs
        p = self.pos.get(k)
        if p is None or p.qty == 0:
            p = PosState(f.strategy, f.key, product, entry_ts=f.ts.isoformat(),
                         risk_ps=float(meta.get("risk_ps") or 0.0), meta={x: y for x, y in meta.items() if x != "risk_ps"})
            self.pos[k] = p
        p.costs += f.costs
        if p.qty == 0 or (p.qty > 0) == (q > 0):
            new = p.qty + q
            p.avg = (p.avg * abs(p.qty) + f.price * abs(q)) / abs(new)
            p.qty = new
            p.entry_qty = max(p.entry_qty, abs(new))
            if not p.risk_ps and meta.get("risk_ps"):
                p.risk_ps = float(meta["risk_ps"])
            return None
        direction = 1 if p.qty > 0 else -1
        close_q = min(abs(q), abs(p.qty))
        pnl = (f.price - p.avg) * close_q * direction
        p.realized += pnl
        self.realized += pnl
        p.exit_notional += f.price * close_q
        p.exit_qty += close_q
        p.qty -= direction * close_q
        if p.qty != 0:
            return None
        net = p.realized - p.costs
        denom = p.risk_ps * p.entry_qty
        trade = Trade(p.strategy, p.key, direction, p.exit_qty, p.entry_ts, f.ts.isoformat(), round(p.avg, 4),
                      round(p.exit_notional / max(p.exit_qty, 1), 4), round(p.realized, 2), round(p.costs, 2),
                      round(net, 2), (net / denom) if denom > 0 else math.nan, p.product, dict(p.meta))
        del self.pos[k]
        leftover = abs(q) - close_q
        if leftover > 0:   # position flipped: open the remainder fresh
            self.pos[k] = PosState(f.strategy, f.key, product, qty=leftover * (1 if q > 0 else -1), avg=f.price,
                                   entry_ts=f.ts.isoformat(), entry_qty=leftover, risk_ps=float(meta.get("risk_ps") or 0))
        return trade

    def snapshot(self) -> dict:
        return {"realized": self.realized, "costs": self.costs, "marks": self.marks,
                "positions": [asdict(p) for p in self.pos.values() if p.qty != 0]}

    def restore(self, snap: dict | None) -> None:
        if not snap:
            return
        self.realized = float(snap.get("realized", 0.0))
        self.costs = float(snap.get("costs", 0.0))
        self.marks.update(snap.get("marks", {}))
        for d in snap.get("positions", []):
            p = PosState(**d)
            self.pos[(p.strategy, p.key)] = p
