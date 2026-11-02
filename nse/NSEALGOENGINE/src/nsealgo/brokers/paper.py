"""Paper broker: a conservative simulated NSE exchange used for backtests, replays and forward tests.

Matching rules (deliberately pessimistic):
- Orders are matched only against market data that arrives AFTER placement (no same-bar fills).
- Marketable orders fill at the next bar open / current touch plus slippage; resting limits need
  the price to trade THROUGH the limit by a tick (queue-position conservatism).
- Stop-limits trigger intrabar; a gap beyond the limit leaves the order resting, as on the exchange.
- Fills are capped at `participation` x bar volume, the remainder stays open.
- Orders placed after 15:15 in CAS stocks (or flagged at_close) execute only at the closing price.
- CNC sells beyond holdings are rejected (no delivery short selling).
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import dataclasses
from datetime import date, datetime

import pandas as pd

from ..core.clock import RealClock, hm
from ..core.indicators import round_tick
from ..core.models import (TERMINAL, Bar, BrokerOrder, BrokerPosition, Instrument, OrderRequest, OrderType, Product,
                           Quote, Side, Status, default_instrument)
from .base import BrokerAdapter, NotSupported
from .symbols import COLS, SymbolMaster, nse_tick


class PaperBroker(BrokerAdapter):
    name = "paper"

    def __init__(self, cfg: dict | None = None, cache_dir=".cache", *, clock=None, mode: str = "minute",
                 master: SymbolMaster | None = None, **_):
        super().__init__(cfg or {}, cache_dir)
        self.clock = clock or RealClock()
        self.mode = mode                      # quote | minute | daily
        self.master = master
        self.slip = float(self.cfg.get("slippage_bps", 2.0)) / 1e4
        self.participation = float(self.cfg.get("participation", 0.25))
        self.cas_start = hm(self.cfg.get("cas_start", "15:15"))
        self.cas_keys: set[str] | None = None  # None = every equity is treated as CAS-eligible
        self.orders: dict[str, dict] = {}
        self.pos: dict[tuple[str, str], list] = {}
        self.last: dict[str, float] = {}
        self.quotes: dict[str, Quote] = {}
        self._seq = 0
        self._day_bar_seen = False
        self.daily_reveal = hm((cfg or {}).get("daily_reveal", "15:10")) if isinstance(cfg, dict) else hm("15:10")
        self._cas_swept = False
        self._inst: dict[str, Instrument] = {}

    # ---- adapter contract ---------------------------------------------------------------------
    def login(self, interactive: bool = True) -> None:
        return None

    def _fetch_master(self) -> pd.DataFrame:
        return pd.DataFrame(columns=COLS)

    def instrument(self, key: str) -> Instrument:
        if key in self._inst:
            return self._inst[key]
        inst = None
        if self.master is not None and key in self.master:
            inst = self.master.get(key)
        if inst is None:
            inst = default_instrument(key)
            px = self.last.get(key)
            if px:
                inst.tick_size = nse_tick(px) if inst.kind == "EQ" else 0.05
        self._inst[key] = inst
        return inst

    def get_quotes(self, keys):
        return [self.quotes[k] for k in keys if k in self.quotes]

    def get_history(self, key, interval, start, end):
        raise NotSupported("paper broker has no history; use the HistoryStore")

    def place_order(self, req: OrderRequest, client_id: str) -> str:
        self._seq += 1
        bid = f"P{self._seq:07d}"
        now = self.clock.now()
        req = dataclasses.replace(req, meta=dict(req.meta))
        status, msg = (Status.TRIGGER_PENDING if req.order_type in (OrderType.SL, OrderType.SLM) else Status.OPEN), ""
        if req.qty <= 0:
            status, msg = Status.REJECTED, "quantity must be positive"
        elif req.product == Product.CNC and req.side == Side.SELL and self.pos.get((req.key, "CNC"), [0, 0])[0] < req.qty:
            status, msg = Status.REJECTED, "CNC sell exceeds holdings (delivery short selling not allowed)"
        elif req.order_type in (OrderType.LIMIT, OrderType.SL) and not req.price:
            status, msg = Status.REJECTED, "limit price required"
        elif req.order_type in (OrderType.SL, OrderType.SLM) and not req.trigger:
            status, msg = Status.REJECTED, "trigger price required"
        at_close = self._closing_only(req, now)
        self.orders[bid] = {"id": bid, "req": req, "tag": client_id, "status": status, "filled": 0, "avg": 0.0,
                            "msg": msg, "triggered": False, "ts": now, "at_close": at_close, "fresh": True}
        return bid

    def _closing_only(self, req: OrderRequest, now: datetime) -> bool:
        """Orders that can only execute at the closing price: flagged at_close, placed after the day bar is
        known (daily mode), or placed in a CAS stock after 15:15 (no continuous trading then)."""
        inst = self.instrument(req.key)
        return bool(req.at_close) or (self.mode == "daily" and self._day_bar_seen) or (
            self.mode != "daily" and inst.kind == "EQ" and now.time() >= self.cas_start and self._is_cas(req.key))

    def modify_order(self, broker_id: str, *, price=None, trigger=None, qty=None, order_type=None) -> None:
        o = self.orders.get(broker_id)
        if not o or o["status"] in TERMINAL:
            from .base import BrokerError
            raise BrokerError("order not open")
        o["at_close"] = o["at_close"] or self._closing_only(o["req"], self.clock.now())
        r = o["req"]
        if price is not None:
            r.price = price
        if trigger is not None:
            r.trigger = trigger
        if qty:
            r.qty = int(qty)
        if order_type is not None:
            r.order_type = order_type
            if order_type in (OrderType.LIMIT, OrderType.MARKET):
                r.trigger = None
                o["status"] = Status.OPEN if o["filled"] == 0 else Status.PARTIAL
                o["fresh"] = True

    def cancel_order(self, broker_id: str) -> None:
        o = self.orders.get(broker_id)
        if o and o["status"] not in TERMINAL:
            o["status"], o["msg"] = Status.CANCELLED, "cancelled"

    def get_orders(self):
        return [BrokerOrder(o["id"], o["status"], o["filled"], o["avg"], o["req"].key, o["req"].side, o["req"].qty,
                            o["tag"], o["msg"]) for o in self.orders.values()]

    def get_positions(self):
        out = []
        for (key, product), (qty, avg) in self.pos.items():
            if qty:
                out.append(BrokerPosition(key, qty, avg, product))
        return out

    # ---- simulation hooks called by the engine --------------------------------------------------
    def seed_positions(self, positions) -> None:
        """Restore simulated holdings from the persisted portfolio (forward tests survive restarts)."""
        for p in positions:
            cur = self.pos.get((p.key, p.product), [0, 0.0])
            self.pos[(p.key, p.product)] = [cur[0] + p.qty, p.avg]

    def day_start(self, day: date) -> None:
        self._day_bar_seen = False
        self._cas_swept = False
        self.orders = {k: o for k, o in self.orders.items() if o["status"] not in TERMINAL}

    def _is_cas(self, key: str) -> bool:
        return True if self.cas_keys is None else key in self.cas_keys

    def on_market(self, quotes: list[Quote], bars: list[Bar], now: datetime) -> None:
        for q in quotes:
            self.quotes[q.key] = q
            self.last[q.key] = q.ltp
        for b in bars:
            self.last[b.key] = b.close
        if (bars and bars[0].interval == "1d") or (self.mode == "daily" and now.time() >= self.daily_reveal):
            # daily replay reveals the whole day at 15:10 whether or not any symbol was subscribed: a strategy that
            # screens a universe without subscribing must still get closing-price fills, not orders that expire
            self._day_bar_seen = True
        if self.mode != "daily" and not self._cas_swept and now.time() >= self.cas_start:
            self._cas_swept = True   # NSE cancels stop-loss orders when a stock enters the closing auction
            for o in self.orders.values():
                r = o["req"]
                if (o["status"] == Status.TRIGGER_PENDING and self.instrument(r.key).kind == "EQ"
                        and self._is_cas(r.key)):
                    o["status"], o["msg"] = Status.CANCELLED, "stop-loss cancelled at closing-auction start"
        bar_by = {b.key: b for b in bars}
        q_by = {q.key: q for q in quotes}
        for o in list(self.orders.values()):
            if o["status"] in TERMINAL or o["at_close"]:
                continue
            k = o["req"].key
            if k in bar_by:
                self._match_bar(o, bar_by[k])
            elif k in q_by:
                self._match_quote(o, q_by[k])

    def on_close(self, closes: dict[str, float], now: datetime) -> None:
        for o in self.orders.values():
            if o["status"] in TERMINAL or not o["at_close"]:
                continue
            r, px = o["req"], closes.get(o["req"].key)
            if px is None:
                o["status"], o["msg"] = Status.CANCELLED, "no closing price"
                continue
            buy = r.side == Side.BUY
            ok = r.order_type == OrderType.MARKET or (r.price is not None and (px <= r.price if buy else px >= r.price))
            if r.order_type in (OrderType.SL, OrderType.SLM):
                ok = False
            if ok:
                self._fill(o, px, None, slip=False)
            else:
                o["status"], o["msg"] = Status.CANCELLED, "unmatched in closing auction"

    def end_of_day(self, now: datetime) -> None:
        for o in self.orders.values():
            if o["status"] not in TERMINAL and o["req"].validity == "DAY":
                o["status"], o["msg"] = Status.CANCELLED, "expired"
        for (key, product), (qty, _avg) in self.pos.items():
            if product == "MIS" and qty:
                self.log.error("MIS position %s x%s still open at EOD: the broker would auto square-off", key, qty)

    # ---- matching ----------------------------------------------------------------------------
    def _match_bar(self, o: dict, b: Bar) -> None:
        r = o["req"]
        buy = r.side == Side.BUY
        tick = self.instrument(r.key).tick_size or 0.05
        fresh = o.pop("fresh", False)
        if r.order_type in (OrderType.SL, OrderType.SLM) and not o["triggered"]:
            if not (b.high >= r.trigger if buy else b.low <= r.trigger):
                return
            o["triggered"], o["status"] = True, Status.OPEN
            px = max(r.trigger, b.open) if buy else min(r.trigger, b.open)
            if r.order_type == OrderType.SLM:
                return self._fill(o, px, b.volume)
            if (buy and px <= r.price) or (not buy and px >= r.price):
                return self._fill(o, px, b.volume, cap=r.price)
            if (buy and b.low <= r.price - tick) or (not buy and b.high >= r.price + tick):
                return self._fill(o, r.price, b.volume, slip=False)
            return
        if r.order_type == OrderType.MARKET:
            return self._fill(o, b.open, b.volume)
        lim = r.price
        if fresh and ((buy and b.open <= lim) or (not buy and b.open >= lim)):
            return self._fill(o, b.open, b.volume, cap=lim)
        if (buy and b.low <= lim - tick) or (not buy and b.high >= lim + tick):
            return self._fill(o, lim, b.volume, slip=False)

    def _match_quote(self, o: dict, q: Quote) -> None:
        r = o["req"]
        buy = r.side == Side.BUY
        tick = self.instrument(r.key).tick_size or 0.05
        o.pop("fresh", None)
        if r.order_type in (OrderType.SL, OrderType.SLM) and not o["triggered"]:
            if not (q.ltp >= r.trigger if buy else q.ltp <= r.trigger):
                return
            o["triggered"], o["status"] = True, Status.OPEN
            if r.order_type == OrderType.SLM:
                return self._fill(o, (q.ask if buy else q.bid) or q.ltp, None)
        if r.order_type == OrderType.MARKET:
            return self._fill(o, (q.ask if buy else q.bid) or q.ltp, None)
        lim = r.price
        touch = q.ask if buy else q.bid
        if touch and ((buy and touch <= lim) or (not buy and touch >= lim)):
            return self._fill(o, touch, None, slip=False)
        if (buy and q.ltp <= lim - tick) or (not buy and q.ltp >= lim + tick):
            return self._fill(o, lim, None, slip=False)

    def _fill(self, o: dict, px: float, bar_volume: float | None, cap: float | None = None, slip: bool = True) -> None:
        r = o["req"]
        buy = r.side == Side.BUY
        inst = self.instrument(r.key)
        remaining = r.qty - o["filled"]
        q = remaining
        if bar_volume is not None:
            if bar_volume <= 0:
                return
            q = min(remaining, max(1, int(self.participation * bar_volume)))
        if slip:
            px = px * (1 + self.slip) if buy else px * (1 - self.slip)
        if cap is not None:
            px = min(px, cap) if buy else max(px, cap)
        px = round_tick(px, inst.tick_size or 0.05, "up" if buy else "down")
        o["avg"] = (o["avg"] * o["filled"] + px * q) / (o["filled"] + q)
        o["filled"] += q
        o["status"] = Status.FILLED if o["filled"] >= r.qty else Status.PARTIAL
        prod = r.product.value
        cur = self.pos.get((r.key, prod), [0, 0.0])
        signed = q if buy else -q
        new = cur[0] + signed
        if cur[0] == 0 or (cur[0] > 0) == (signed > 0):
            avg = (cur[1] * abs(cur[0]) + px * q) / abs(new) if new else 0.0
        else:
            avg = cur[1] if new and (new > 0) == (cur[0] > 0) else (px if new else 0.0)
        self.pos[(r.key, prod)] = [new, avg]
