"""Order management: rate limiting, tagging, placement, modification, fill sync and reconciliation.

Every order carries a <=20-char alphanumeric client id sent as the broker 'tag' so an order whose
placement call timed out can still be matched later instead of being blindly re-sent.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from .indicators import round_tick
from .models import Fill, Order, OrderRequest, OrderType, Product, Status, Side

log = logging.getLogger("nsealgo.oms")


class TokenBucket:
    def __init__(self, rate: float):
        self.rate = max(float(rate), 0.1)
        self.tokens = self.rate
        self.t: datetime | None = None

    def acquire(self, clock) -> None:
        now = clock.now()
        if self.t is not None:
            self.tokens = min(self.rate, self.tokens + (now - self.t).total_seconds() * self.rate)
        self.t = now
        if self.tokens < 1:
            clock.sleep((1 - self.tokens) / self.rate)
            self.tokens, self.t = 0.0, clock.now()
        else:
            self.tokens -= 1


class OrderManager:
    def __init__(self, broker, risk, portfolio, storage, clock, cfg: dict, costs, instrument, last_price, notifier=None,
                 quote=None):
        from ..brokers.base import BrokerError
        self._BrokerError = BrokerError
        self.broker, self.risk, self.portfolio, self.storage, self.clock = broker, risk, portfolio, storage, clock
        self.cfg, self.costs, self.instrument, self.last_price, self.notifier = cfg, costs, instrument, last_price, notifier
        self.quote = quote or (lambda key: None)
        self.orders: dict[str, Order] = {}
        self.by_broker: dict[str, str] = {}
        self.listeners: list = []        # fn(fill, order)
        self.trade_listeners: list = []  # fn(trade)
        ops = float(cfg.get("orders_per_second", 8))
        per_account = getattr(broker, "max_ops", None)
        if callable(per_account):
            ops = float(per_account())                    # multi-broker: sum of the accounts' own budgets
        elif ops > 10 and not cfg.get("registered_algo"):
            log.warning("oms.orders_per_second=%s is above the 10 orders/s limit per broker for unregistered "
                        "algos; capped at 10. Once your broker has registered the algo, set "
                        "oms.registered_algo: true", ops)
            ops = 10.0
        self.bucket = TokenBucket(ops)
        self._last_sync: datetime | None = None
        self._seq = 0
        self._blocked_seen: set = set()

    def new_client_id(self, strategy: str) -> str:
        self._seq += 1
        p = (re.sub(r"[^A-Za-z0-9]", "", strategy)[:4] or "ALGO").upper()
        return f"{p}{self.clock.now():%m%d%H%M%S}{self._seq % 1000:03d}"[:20]

    def _protect(self, req: OrderRequest, tick: float) -> None:
        """Exchanges reject unprotected MARKET / SL-M algo orders (from 1 Apr 2026): send them as marketable
        LIMIT / stop-LIMIT orders inside a protection band instead, for every broker."""
        if req.at_close or not self.cfg.get("protect_market_orders", True):
            return
        buy = req.side == Side.BUY
        if req.order_type == OrderType.MARKET:
            q, last = self.quote(req.key), self.last_price(req.key)
            ref = ((q.ask if buy else q.bid) if q is not None else None) or last
            if not ref:
                return                      # no price reference: leave it to the broker's own protection
            band = float(self.cfg.get("market_protection_pct", 0.005))
            req.price = round_tick(ref * (1 + band if buy else 1 - band), tick, "up" if buy else "down")
            req.order_type = OrderType.LIMIT
        elif req.order_type == OrderType.SLM and req.trigger:
            band = float(self.cfg.get("stop_limit_pct", 0.01))
            req.price = round_tick(req.trigger * (1 + band if buy else 1 - band), tick, "up" if buy else "down")
            req.order_type = OrderType.SL

    def _self_trade(self, req: OrderRequest) -> Order | None:
        """A resting LIMIT order of ours on the other side that this order could trade against (a self-trade:
        exchanges prevent and regulators frown on them). Untriggered stop orders are not in the book."""
        if req.price is None:
            return None
        for o in self.active(key=req.key):
            r = o.req
            if r.side != req.side and r.order_type == OrderType.LIMIT and r.price is not None and (
                    (req.side == Side.BUY and req.price >= r.price) or (req.side == Side.SELL and req.price <= r.price)):
                return o
        return None

    def submit(self, req: OrderRequest) -> Order | None:
        inst = self.instrument(req.key)
        tick = inst.tick_size or 0.05
        self._protect(req, tick)
        clash = self._self_trade(req)
        if clash is not None:
            log.warning("blocked %s %s %s @ %s: would trade against our own resting %s order %s", req.strategy,
                        req.side.value, req.key, req.price, clash.req.strategy, clash.client_id)
            return None
        if req.price is not None:
            req.price = round_tick(req.price, tick)
        if req.trigger is not None:
            req.trigger = round_tick(req.trigger, tick)
        req.qty = int(req.qty)
        if req.qty <= 0:
            return None
        now = self.clock.now()
        ok, why = self.risk.check(req, now, self.last_price(req.key))
        if not ok:
            tag = (now.date(), req.strategy, why.split(":")[0])
            if tag not in self._blocked_seen:   # log each block reason once per strategy per day
                self._blocked_seen.add(tag)
                log.info("blocked %s %s %s x%s: %s", req.strategy, req.side.value, req.key, req.qty, why)
                self.storage.log_event(now, "INFO", "risk", f"blocked {req.strategy} {req.key}: {why}")
            return None
        self.bucket.acquire(self.clock)
        cid = self.new_client_id(req.strategy)
        o = Order(client_id=cid, req=req, created=now, updated=now)
        self.orders[cid] = o
        try:
            bid = self.broker.place_order(req, cid)
            o.broker_id, o.status = str(bid), Status.OPEN
            self.by_broker[o.broker_id] = cid
        except self._BrokerError as ex:
            o.status, o.message = Status.REJECTED, str(ex)[:200]
            log.warning("order rejected %s %s: %s", req.strategy, req.key, ex)
            self._notify(f"Order rejected {req.strategy} {req.key}: {ex}")
        except Exception as ex:  # timeout / network: outcome unknown, reconcile by tag on next sync
            o.status, o.message = Status.UNKNOWN, f"placement uncertain: {ex}"[:200]
            log.error("order placement uncertain %s: %s", cid, ex)
        self.risk.on_order_submitted(req)
        self.storage.upsert_order(o)
        return o

    def cancel(self, cid: str | None) -> bool:
        o = self.orders.get(cid or "")
        if not o or not o.is_active:
            return False
        if not o.broker_id:
            o.status = Status.CANCELLED
            return True
        try:
            if not self.clock.is_sim:
                self.bucket.acquire(self.clock)            # modifies and cancels count as order messages
            self.broker.cancel_order(o.broker_id)
            return True
        except Exception as ex:
            log.warning("cancel %s failed: %s", cid, ex)
            return False

    def modify(self, cid: str | None, *, price=None, trigger=None, qty=None, order_type: OrderType | None = None) -> bool:
        o = self.orders.get(cid or "")
        if not o or not o.is_active or not o.broker_id:
            return False
        tick = self.instrument(o.req.key).tick_size or 0.05
        price = round_tick(price, tick) if price is not None else None
        trigger = round_tick(trigger, tick) if trigger is not None else None
        try:
            if not self.clock.is_sim:
                self.bucket.acquire(self.clock)            # modifies and cancels count as order messages
            self.broker.modify_order(o.broker_id, price=price, trigger=trigger, qty=qty, order_type=order_type)
        except Exception as ex:
            log.warning("modify %s failed: %s", cid, ex)
            return False
        if price is not None:
            o.req.price = price
        if trigger is not None:
            o.req.trigger = trigger
        if qty:
            o.req.qty = int(qty)
        if order_type is not None:
            o.req.order_type = order_type
            if order_type in (OrderType.LIMIT, OrderType.MARKET):
                o.req.trigger = None
        o.updated = self.clock.now()
        self.storage.upsert_order(o)
        return True

    def chase_exits(self, now: datetime) -> int:
        """Re-price exit limits that the market ran away from (the protective stop was converted into the exit,
        so an unfilled exit would leave the position unprotected). Aggression grows each attempt, capped."""
        wait = float(self.cfg.get("exit_chase_seconds", 5.0))
        step, cap = float(self.cfg.get("exit_chase_step", 0.002)), float(self.cfg.get("exit_chase_max", 0.02))
        n = 0
        for o in self.active(intent="exit"):
            r = o.req
            if r.order_type != OrderType.LIMIT or r.at_close or not o.broker_id or r.price is None:
                continue
            if o.updated is not None and (now - o.updated).total_seconds() < wait:
                continue
            sell = r.side == Side.SELL
            q, last = self.quote(r.key), self.last_price(r.key)
            ref = ((q.bid if sell else q.ask) if q is not None else None) or last
            if not ref:
                continue
            k = int(r.meta.get("chase", 0)) + 1
            a = min(cap, step * (k + 1))
            tick = self.instrument(r.key).tick_size or 0.05
            px = round_tick(ref * (1 - a) if sell else ref * (1 + a), tick, "down" if sell else "up")
            if (sell and px >= r.price) or (not sell and px <= r.price):
                o.updated = now             # still marketable: give it time
                continue
            if self.modify(o.client_id, price=px):
                r.meta["chase"] = k
                n += 1
                log.info("chasing exit %s %s: attempt %d -> %.2f", r.strategy, r.key, k, px)
                if k == int(self.cfg.get("exit_chase_alert_after", 5)):
                    self._notify(f"Exit {r.strategy} {r.key} still unfilled after {k} re-prices")
        return n

    def expire_entries(self, now: datetime) -> list[Order]:
        """Intraday (MIS) LIMIT entries still open after oms.entry_ttl_seconds: cancel what is left. A partly filled
        entry keeps the shares it got (its stop already covers them - strategies re-protect on every fill); the
        partly filled orders are returned so the engine can drop fills below oms.min_partial_fraction."""
        ttl = float(self.cfg.get("entry_ttl_seconds", 90.0))
        if ttl <= 0:
            return []
        out = []
        for o in self.active(intent="entry"):
            r = o.req
            if r.product != Product.MIS or r.order_type != OrderType.LIMIT or r.at_close or o.created is None:
                continue
            if (now - o.created).total_seconds() < ttl or r.meta.get("ttl_cancelled"):
                continue
            if self.cancel(o.client_id):
                r.meta["ttl_cancelled"] = True
                if o.filled_qty > 0:
                    log.info("entry %s %s partly filled %d/%d after %.0fs - rest cancelled", r.strategy, r.key,
                             o.filled_qty, r.qty, ttl)
                    self._notify(f"Partial entry {r.strategy} {r.key}: {o.filled_qty} of {r.qty} filled, rest cancelled")
                    out.append(o)
                else:
                    log.info("entry %s %s unfilled after %.0fs - cancelled", r.strategy, r.key, ttl)
        return out

    def active(self, strategy: str | None = None, key: str | None = None, intent: str | None = None) -> list[Order]:
        return [o for o in self.orders.values() if o.is_active and (strategy is None or o.req.strategy == strategy)
                and (key is None or o.req.key == key) and (intent is None or o.req.intent == intent)]

    def cancel_all(self, strategy: str | None = None, product=None) -> None:
        for o in self.active(strategy):
            if product is None or o.req.product == product:
                self.cancel(o.client_id)

    def sync(self, now: datetime, force: bool = False) -> list:
        if not force and self._last_sync and (now - self._last_sync).total_seconds() < self.cfg.get("sync_seconds", 2.0):
            return []
        self._last_sync = now
        active = [o for o in self.orders.values() if o.is_active]
        if not active:
            return []
        try:
            book = self.broker.get_orders()
        except Exception as ex:
            log.warning("order book fetch failed: %s", ex)
            return []
        by_id = {b.broker_id: b for b in book}
        by_tag = {b.tag: b for b in book if b.tag}
        fills = []
        for o in active:
            b = by_id.get(o.broker_id) if o.broker_id else None
            if b is None:
                b = by_tag.get(o.client_id)
                if b is not None:
                    o.broker_id = b.broker_id
                    self.by_broker[b.broker_id] = o.client_id
            if b is None:
                continue
            inc = int(b.filled_qty) - o.filled_qty
            if inc > 0:
                if b.avg_price:
                    px = (b.avg_price * b.filled_qty - o.avg_price * o.filled_qty) / inc
                else:
                    px = self.last_price(o.req.key) or o.req.price or 0.0
                o.avg_price = b.avg_price or px
                o.filled_qty = int(b.filled_qty)
                f = Fill(o.client_id, o.req.key, o.req.side, inc, float(px), now, o.req.strategy, o.req.product,
                         intent=o.req.intent)
                f.costs = self.costs.fill_costs(product=o.req.product, side=o.req.side, price=f.price, qty=inc,
                                                kind=self.instrument(o.req.key).kind, key=o.req.key)
                fills.append((f, o))
            st = b.status
            if st == Status.OPEN and 0 < b.filled_qty < o.req.qty:
                st = Status.PARTIAL
            if st != Status.UNKNOWN:
                o.status = st
            if b.message:
                o.message = str(b.message)[:200]
            o.updated = now
            self.storage.upsert_order(o)
        for f, o in fills:
            self.storage.add_fill(f)
            trade = self.portfolio.on_fill(f, o.req.meta)
            if trade is not None:
                self.storage.add_trade(trade)
                self.risk.on_trade(trade)
                for cb in self.trade_listeners:
                    cb(trade)
            for cb in self.listeners:
                cb(f, o)
        return fills

    def expire_day_orders(self) -> None:
        for o in self.orders.values():
            if o.is_active and o.req.validity == "DAY":
                o.status, o.message = Status.CANCELLED, "expired at end of day"
                self.storage.upsert_order(o)
        self.orders = {k: v for k, v in self.orders.items() if v.is_active}
        self.by_broker = {b: c for b, c in self.by_broker.items() if c in self.orders}

    def reconcile(self, now: datetime) -> list[str]:
        """Compare broker net positions with the internal book; returns human-readable mismatches."""
        try:
            broker = {}
            for p in self.broker.get_positions():
                broker[p.key] = broker.get(p.key, 0) + int(p.qty)
        except Exception as ex:
            log.warning("position fetch failed: %s", ex)
            return []
        mine = self.portfolio.net_by_key()
        issues = [f"{k}: broker {broker.get(k, 0)} vs system {mine.get(k, 0)}"
                  for k in set(broker) | set(mine) if broker.get(k, 0) != mine.get(k, 0)]
        for msg in issues:
            self.storage.log_event(now, "ERROR", "reconcile", msg)
        return issues

    def _notify(self, text: str) -> None:
        if self.notifier:
            self.notifier.send(text)
