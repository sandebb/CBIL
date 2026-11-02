"""Which broker places which live orders, and dormant brokers.

    broker:
      execution: paper                       # forward tests always trade against the paper exchange
      orders: {intraday: fyers, delivery: fyers}   # live: MIS orders / CNC orders (delivery defaults to intraday)
      brokers:
        shoonya: {..., orders: false}        # dormant: kept configured, never sent an order

`nsealgo live --broker X` sends every order to X. `broker.execution: multi` keeps the multi-account router.
Configs without `broker.orders` send all live orders to `broker.execution`, as before. A dormant broker can still
log in and supply data, but resolving it as an order broker is an error, so it can't place orders by accident.
"""
from __future__ import annotations

from dataclasses import replace

from .base import BrokerError

SEP = "|"
PRODUCT_NAMES = {"MIS": "intraday", "CNC": "delivery"}


def dormant(cfg: dict, name: str) -> bool:
    b = ((cfg.get("broker") or {}).get("brokers") or {}).get(name) or {}
    return b.get("orders", True) is False


def order_brokers(cfg: dict, override: str | None = None) -> dict[str, str]:
    """{"MIS": broker, "CNC": broker} for live orders; raises if unset, paper, or dormant."""
    b = cfg.get("broker") or {}
    if override:
        m = {"MIS": override, "CNC": override}
    elif b.get("execution") == "multi":
        m = {"MIS": "multi", "CNC": "multi"}
    elif b.get("orders"):
        o = b["orders"]
        if not o.get("intraday"):
            raise BrokerError("broker.orders.intraday is not set: name the broker for intraday orders")
        m = {"MIS": str(o["intraday"]), "CNC": str(o.get("delivery") or o["intraday"])}
    else:
        ex = b.get("execution")
        if not ex or ex == "paper":
            raise BrokerError("no live order broker: set broker.orders.intraday (and .delivery), or pass --broker")
        m = {"MIS": ex, "CNC": ex}
    names = set(m.values())
    if "multi" in names:
        names = {a.get("broker") for a in (cfg.get("accounts") or {}).values()}
    for prod, name in m.items():
        if name == "paper":
            raise BrokerError(f"paper isn't a live order broker ({PRODUCT_NAMES[prod]} orders)")
    for name in names:
        if name and dormant(cfg, name):
            raise BrokerError(f"{name} order placement is dormant (broker.brokers.{name}.orders: false): choose another "
                              f"order broker, or set orders: true to use it")
    return m


class ProductRouter:
    """Live orders split by product: intraday (MIS) to one broker, delivery (CNC) to another. Order ids carry the
    broker's name, so modifications and cancellations go back to the broker that holds the order."""
    name = "routed"

    def __init__(self, by_product: dict):
        self.by_product = dict(by_product)
        self.adapters = list(dict.fromkeys(self.by_product.values()))       # intraday broker first

    def __getattr__(self, item):                          # anything else: the intraday broker
        if item in ("by_product", "adapters"):
            raise AttributeError(item)
        return getattr(self.adapters[0], item)

    def _for(self, product):
        return self.by_product.get(str(getattr(product, "value", product)), self.adapters[0])

    def _split(self, broker_id: str):
        n, _, raw = str(broker_id).partition(SEP)
        a = next((x for x in self.adapters if x.name == n), None)
        if a is None:
            raise BrokerError(f"unknown broker in order id {broker_id}")
        return a, raw

    def place_order(self, req, client_id: str) -> str:
        a = self._for(req.product)
        return f"{a.name}{SEP}{a.place_order(req, client_id)}"

    def modify_order(self, broker_id: str, **kw) -> None:
        a, raw = self._split(broker_id)
        a.modify_order(raw, **kw)

    def cancel_order(self, broker_id: str) -> None:
        a, raw = self._split(broker_id)
        a.cancel_order(raw)

    def get_orders(self):
        return [replace(o, broker_id=f"{a.name}{SEP}{o.broker_id}") for a in self.adapters for o in a.get_orders()]

    def get_positions(self):
        agg = {}
        for a in self.adapters:
            for p in a.get_positions():
                k = (p.key, p.product)
                if k in agg:
                    q, tot = agg[k], agg[k].qty + p.qty
                    agg[k] = replace(q, qty=tot, avg_price=(q.avg_price * q.qty + p.avg_price * p.qty) / tot if tot else 0.0)
                else:
                    agg[k] = p
        return list(agg.values())

    def get_funds(self):
        out: dict = {}
        for a in self.adapters:
            for k, v in ((a.get_funds() if hasattr(a, "get_funds") else None) or {}).items():
                if isinstance(v, (int, float)):
                    out[k] = out.get(k, 0) + v
        return out

    def login(self, interactive: bool = True) -> None:
        for a in self.adapters:
            a.login(interactive)


def build_execution(cfg: dict, sources, cache_dir, *, override: str | None = None, login=None):
    """The live execution object: one adapter, a ProductRouter, or the multi-account router. Adapters already
    logged in as data sources are reused; others are created and passed to `login`."""
    from .registry import make_broker
    m = order_brokers(cfg, override)
    names = list(dict.fromkeys(m.values()))
    made = {}
    for n in names:
        a = next((s for s in sources if getattr(s, "name", "") == n), None)
        if a is None:
            a = make_broker(n, cfg, cache_dir)
            if login is not None:
                login(a)
        made[n] = a
    if len(made) == 1:
        return made[names[0]]
    return ProductRouter({p: made[n] for p, n in m.items()})


def describe(cfg: dict, override: str | None = None) -> str:
    m = order_brokers(cfg, override)
    if m["MIS"] == m["CNC"]:
        return f"all live orders -> {m['MIS']}"
    return f"intraday (MIS) orders -> {m['MIS']}, delivery (CNC) orders -> {m['CNC']}"
