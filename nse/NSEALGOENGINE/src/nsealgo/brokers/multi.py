"""Several broker accounts behind one execution interface (config: broker.execution: multi).

Each stock trades at exactly one account. An allocation table (cache/multi_state.json) assigns every
underlying symbol to an account the first time it is traded and keeps it there; futures follow their
underlying, and every strategy's orders for that stock go to the same account. New symbols go to the
account with the most spare order-rate capacity, unless pinned (config `routing.pins`, or
`nsealgo accounts --pin`).

Every account has its own order-rate budget: `accounts.<name>.orders_per_second`, default 8, at most 10
unless that account's algo is registered (`registered_algo: true`). Throughput therefore adds up across
accounts, with each broker's limit enforced at that broker.

Positions never move: exits, stops and additions go to the account that holds the position. If an
account is unreachable, a NEW entry in a stock with no open position or order there (and not pinned) is
moved to another account, but only after asking the first account, by the order's tag, whether it
received the order, so nothing is sent twice. A broker's rejection (margin, price band) is its answer and
is never re-routed.
"""
from __future__ import annotations

import copy
import json
import logging
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from ..core.models import parse_key
from .base import BrokerError

log = logging.getLogger("nsealgo.multi")
SEP = "|"
DEFAULT_OPS, UNREGISTERED_MAX_OPS = 8.0, 10.0


def unreachable(ex: BaseException | None) -> bool:
    """True when the request may never have reached the broker (network), False for a broker's answer."""
    seen = set()
    while ex is not None and id(ex) not in seen:
        seen.add(id(ex))
        name = type(ex).__name__
        if name == "HTTPError":
            return False                                   # the broker answered with an error status
        if isinstance(ex, (ConnectionError, TimeoutError)) or name in (
                "ConnectionError", "Timeout", "ConnectTimeout", "ReadTimeout", "ProxyError", "SSLError"):
            return True
        ex = ex.__cause__ or ex.__context__
    return False


def account_capacity(spec: dict) -> dict[str, float]:
    """Orders/s budget per account: default 8, capped at 10 unless that account's algo is registered."""
    out = {}
    for n, a in (spec or {}).items():
        ops = float((a or {}).get("orders_per_second", DEFAULT_OPS))
        if ops > UNREGISTERED_MAX_OPS and not (a or {}).get("registered_algo"):
            log.warning("account %s: orders_per_second=%s is above the 10/s limit for unregistered algos; "
                        "capped at 10 (set registered_algo: true once the broker has registered it)", n, ops)
            ops = UNREGISTERED_MAX_OPS
        out[n] = ops
    return out


def symbol_of(key: str) -> str:
    return parse_key(key)["symbol"] if ":" in key else key.strip().upper()


class RateBudget:
    """Token bucket per account, on real time (live) or on an injected clock (simulation, tests)."""

    def __init__(self, rate: float, clock=None):
        self.rate, self.clock = max(float(rate), 0.1), clock
        self.tokens, self.t, self.waited = self.rate, None, 0.0

    def _now(self) -> float:
        return self.clock.now().timestamp() if self.clock is not None else time.monotonic()

    def acquire(self) -> None:
        now = self._now()
        if self.t is not None:
            self.tokens = min(self.rate, self.tokens + (now - self.t) * self.rate)
        self.t = now
        if self.tokens < 1:
            wait = (1 - self.tokens) / self.rate
            self.waited += wait
            (self.clock.sleep if self.clock is not None else time.sleep)(wait)
            self.tokens, self.t = 0.0, self._now()
        else:
            self.tokens -= 1


class AllocationTable:
    """Which account trades which stock (sticky, persisted, capacity-balanced), and which account holds
    each open position (strategy, instrument)."""

    def __init__(self, accounts, capacity: dict | None = None, pins: dict | None = None, path=None):
        self.accounts = list(accounts)
        if not self.accounts:
            raise ValueError("no broker accounts")
        self.capacity = {n: max(float((capacity or {}).get(n, DEFAULT_OPS)), 0.1) for n in self.accounts}
        self.path = Path(path) if path else None
        st = self._load()
        self.symbols: dict[str, str] = {s: a for s, a in st.get("symbols", {}).items() if a in self.capacity}
        self.owners: dict[str, str] = {k: a for k, a in st.get("owners", {}).items() if a in self.capacity}
        self.saved_pins: dict[str, str] = {s: a for s, a in st.get("pins", {}).items() if a in self.capacity}
        self.config_pins = {symbol_of(k): v for k, v in (pins or {}).items() if v in self.capacity}

    @property
    def pins(self) -> dict[str, str]:
        return {**self.saved_pins, **self.config_pins}             # config pins win

    def _load(self) -> dict:
        if self.path and self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("allocation table %s unreadable; starting a new one", self.path)
        return {}

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {"updated": datetime.now().isoformat(timespec="seconds"), "symbols": self.symbols,
                    "owners": self.owners, "pins": self.saved_pins}
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
            tmp.replace(self.path)                                  # atomic: never a half-written table

    def account(self, sym: str) -> str | None:
        return self.symbols.get(sym)

    def idle(self, sym: str) -> bool:
        """No open position or working order for this stock (as tracked by the router)."""
        return not any(symbol_of(k.split(SEP, 1)[1]) == sym for k in self.owners)

    def choose(self, sym: str, avoid=(), healthy=None) -> str:
        pin = self.pins.get(sym)
        if pin and pin not in avoid:
            return pin
        cands = [n for n in self.accounts if n not in avoid and (healthy is None or healthy(n))] \
            or [n for n in self.accounts if n not in avoid]
        if not cands:
            raise BrokerError(f"no broker account available for {sym}")
        load = Counter(self.symbols.values())
        return min(cands, key=lambda n: ((load[n] + 1) / self.capacity[n], self.accounts.index(n)))

    def assign(self, sym: str, acct: str) -> None:
        self.symbols[sym] = acct
        self.save()

    def pin(self, sym: str, acct: str) -> None:
        sym = symbol_of(sym)
        if acct not in self.capacity:
            raise BrokerError(f"unknown account {acct}; accounts: {', '.join(self.accounts)}")
        cur = self.symbols.get(sym)
        if cur and cur != acct and not self.idle(sym):
            raise BrokerError(f"{sym} has an open position or order at {cur}; close it before moving the stock")
        self.saved_pins[sym] = acct
        self.symbols[sym] = acct
        self.save()

    def unpin(self, sym: str) -> None:
        self.saved_pins.pop(symbol_of(sym), None)
        self.save()

    def rebalance(self) -> list[tuple[str, str, str]]:
        """Re-spread idle, unpinned stocks by capacity. Stocks with open positions or pins stay put."""
        pins = self.pins
        fixed = {s: a for s, a in self.symbols.items() if not self.idle(s) or s in pins}
        fixed.update({s: a for s, a in pins.items() if s in self.symbols})
        counts, new = Counter(fixed.values()), dict(fixed)
        for s in sorted(x for x in self.symbols if x not in fixed):
            a = min(self.accounts, key=lambda n: ((counts[n] + 1) / self.capacity[n], self.accounts.index(n)))
            new[s] = a
            counts[a] += 1
        moves = [(s, self.symbols[s], new[s]) for s in sorted(self.symbols) if new[s] != self.symbols[s]]
        self.symbols = new
        self.save()
        return moves

    def summary(self) -> dict[str, list[str]]:
        out = {n: [] for n in self.accounts}
        for s, a in sorted(self.symbols.items()):
            out[a].append(s)
        return out


class MultiBroker:
    name = "multi"

    def __init__(self, accounts: dict, *, capacity: dict | None = None, pins: dict | None = None, state_file=None,
                 retry_seconds: float = 60.0, reassign_idle: bool = True, clock=None, capital: dict | None = None):
        if not accounts:
            raise ValueError("the multi broker needs at least one account")
        self.accounts = dict(accounts)
        self.table = AllocationTable(list(self.accounts), capacity, pins, state_file)
        self.budgets = {n: RateBudget(self.table.capacity[n], clock) for n in self.accounts}
        self.retry_seconds, self.reassign_idle = float(retry_seconds), bool(reassign_idle)
        self.down: dict[str, float] = {}
        self.capital = {n: float(v) for n, v in (capital or {}).items() if v}   # accounts.<name>.capital (Rs)

    def check_budget(self, n: str, req) -> None:
        """An entry may not take an account's open positions above its capital budget (accounts.<name>.capital)."""
        cap = self.capital.get(n)
        if not cap or req.intent != "entry":
            return
        used = sum(abs(p.qty) * float(p.avg_price or 0) for p in self.accounts[n].get_positions())
        value = abs(req.qty) * float(req.price or req.trigger or 0)
        if used + value > cap:
            raise BrokerError(f"account {n} capital budget: Rs {used:,.0f} in use + Rs {value:,.0f} > Rs {cap:,.0f}")

    def max_ops(self) -> float:
        """Combined order-rate budget (the OMS's overall governor); each account enforces its own share."""
        return sum(self.table.capacity.values())

    # ---- health ------------------------------------------------------------------------------------
    def healthy(self, n: str) -> bool:
        t = self.down.get(n)
        return t is None or time.monotonic() >= t

    def mark_down(self, n: str, why: str) -> None:
        self.down[n] = time.monotonic() + self.retry_seconds
        log.warning("account %s unavailable for %.0fs: %s", n, self.retry_seconds, why)

    @staticmethod
    def _okey(strategy: str, key: str) -> str:
        return f"{strategy}{SEP}{key}"

    def release(self, strategy: str, key: str) -> None:
        """Called by the engine when a strategy is flat in an instrument with no working orders."""
        if self.table.owners.pop(self._okey(strategy, key), None) is not None:
            self.table.save()

    # ---- routing -----------------------------------------------------------------------------------
    def route(self, req) -> str:
        own = self.table.owners.get(self._okey(req.strategy, req.key))
        if own in self.accounts:
            return own                                    # the account holding the position gets all its orders
        sym = symbol_of(req.key)
        acct = self.table.account(sym)
        if acct is None:
            acct = self.table.choose(sym, healthy=self.healthy)
            self.table.assign(sym, acct)
            log.info("allocated %s to account %s", sym, acct)
        return acct

    def _move(self, req, sym: str, n: str, why: str) -> str:
        if not (req.intent == "entry" and self.reassign_idle and self.table.idle(sym) and sym not in self.table.pins):
            raise BrokerError(f"{sym} trades at account {n}, which is unavailable ({why}); not moved because it has "
                              f"an open position or order there, is pinned, or this is an exit")
        new = self.table.choose(sym, avoid={n}, healthy=self.healthy)
        if not self.healthy(new):
            raise BrokerError(f"no healthy account to move {sym} to ({why})")
        self.table.assign(sym, new)
        log.warning("moved %s from account %s to %s: %s", sym, n, new, why)
        return new

    def _call(self, n: str, fn, *a, **k):
        self.budgets[n].acquire()                           # this broker's own orders/s limit
        return fn(*a, **k)

    def _find(self, n: str, client_id: str):
        for bo in self.accounts[n].get_orders():
            if bo.tag and (bo.tag == client_id or (len(bo.tag) >= 8 and client_id.startswith(bo.tag))):
                return bo.broker_id
        return None

    def place_order(self, req, client_id: str) -> str:
        n, sym = self.route(req), symbol_of(req.key)
        if not self.healthy(n):
            n = self._move(req, sym, n, "account marked down")
        self.check_budget(n, req)
        try:
            raw = self._call(n, self.accounts[n].place_order, req, client_id)
        except Exception as ex:
            if not unreachable(ex):
                raise                                      # the broker said no: never re-route a rejection
            try:
                raw = self._find(n, client_id)
            except Exception as ex2:
                self.mark_down(n, str(ex2))
                raise BrokerError(f"{n} unreachable and the order's fate is unknown; not re-routed") from ex
            if raw is None:
                self.mark_down(n, str(ex))
                n = self._move(req, sym, n, str(ex))
                raw = self._call(n, self.accounts[n].place_order, req, client_id)
            else:
                log.warning("%s: order %s did reach the broker despite the error; adopting it", n, client_id)
        self.table.owners[self._okey(req.strategy, req.key)] = n
        self.table.save()
        return f"{n}{SEP}{raw}"

    def _split(self, broker_id: str):
        n, _, raw = str(broker_id).partition(SEP)
        if n not in self.accounts:
            raise BrokerError(f"unknown account in order id {broker_id}")
        return n, raw

    def modify_order(self, broker_id: str, **kw) -> None:
        n, raw = self._split(broker_id)
        self._call(n, self.accounts[n].modify_order, raw, **kw)

    def cancel_order(self, broker_id: str) -> None:
        n, raw = self._split(broker_id)
        self._call(n, self.accounts[n].cancel_order, raw)

    # ---- merged views ------------------------------------------------------------------------------
    @property
    def master(self):
        for a in self.accounts.values():
            m = getattr(a, "master", None)
            if m is not None and len(m):
                return m
        return None

    def login(self, interactive: bool = True) -> None:
        ok = 0
        for n, a in self.accounts.items():
            try:
                a.login(interactive)
                ok += 1
            except Exception as ex:
                self.mark_down(n, f"login failed: {ex}")
        if not ok:
            raise BrokerError("no broker account could log in")

    def _first(self, method: str, *args, **kw):
        err = None
        for n, a in self.accounts.items():
            if self.healthy(n):
                try:
                    return getattr(a, method)(*args, **kw)
                except Exception as ex:
                    err = ex
        raise err or BrokerError(f"no healthy account for {method}")

    def instrument(self, key):
        return self._first("instrument", key)

    def get_quotes(self, keys):
        return self._first("get_quotes", keys)

    def get_history(self, *a, **k):
        return self._first("get_history", *a, **k)

    def get_orders(self):
        out = []
        for n, a in self.accounts.items():
            if self.healthy(n):
                try:
                    out += [replace(bo, broker_id=f"{n}{SEP}{bo.broker_id}") for bo in a.get_orders()]
                except Exception as ex:
                    self.mark_down(n, f"order book: {ex}")
        return out

    def get_positions(self):
        agg = {}
        for n, a in self.accounts.items():
            if not self.healthy(n):
                continue
            try:
                ps = a.get_positions()
            except Exception as ex:
                self.mark_down(n, f"positions: {ex}")
                continue
            for p in ps:
                k = (p.key, p.product)
                if k in agg:
                    q, tot = agg[k], agg[k].qty + p.qty
                    avg = (q.avg_price * q.qty + p.avg_price * p.qty) / tot if tot else 0.0
                    agg[k] = replace(q, qty=tot, avg_price=avg)
                else:
                    agg[k] = replace(p)
        return list(agg.values())

    def get_funds(self):
        out: dict = {}
        for n, a in self.accounts.items():
            if self.healthy(n) and hasattr(a, "get_funds"):
                try:
                    for k, v in (a.get_funds() or {}).items():
                        if isinstance(v, (int, float)):
                            out[k] = out.get(k, 0) + v
                except Exception as ex:
                    self.mark_down(n, f"funds: {ex}")
        return out


def state_path(cache_dir) -> Path:
    return Path(cache_dir) / "multi_state.json"


def build_multi(cfg: dict, cache_dir=".cache", **kw) -> MultiBroker:
    from .registry import make_broker
    spec = cfg.get("accounts") or {}
    if not spec:
        raise BrokerError("broker.execution is 'multi' but no accounts are configured (see config/system.yaml)")
    accounts = {}
    for n, a in spec.items():
        c, b = copy.deepcopy(cfg), a["broker"]
        brokers = c.setdefault("broker", {}).setdefault("brokers", {})
        brokers[b] = {**(brokers.get(b) or {}), **(a.get("settings") or {})}
        accounts[n] = make_broker(b, c, Path(cache_dir) / n, **kw)       # separate session cache per account
    r = cfg.get("routing") or {}
    return MultiBroker(accounts, capacity=account_capacity(spec), pins=r.get("pins"), state_file=state_path(cache_dir),
                       retry_seconds=float(r.get("retry_seconds", 60)), reassign_idle=bool(r.get("reassign_idle", True)),
                       clock=kw.get("clock"), capital={n: a.get("capital") for n, a in spec.items()})
