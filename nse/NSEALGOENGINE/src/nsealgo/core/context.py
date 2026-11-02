"""StrategyContext: the only API strategies see. Keeps strategies broker-, mode- and time-agnostic."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import logging
import math
from datetime import datetime

import numpy as np
import pandas as pd

from .indicators import expected_shortfall, gap_quantile
from .clock import hm
from .models import Order, OrderRequest, Product

log = logging.getLogger("nsealgo.strategy")


class Context:
    def __init__(self, engine):
        self.e = engine

    # ---- time, mode, environment ---------------------------------------------------------------
    @property
    def now(self) -> datetime:
        return self.e.clock.now()

    @property
    def today(self):
        return self.e.day

    @property
    def mode(self) -> str:
        return self.e.mode

    @property
    def granularity(self) -> str:
        return self.e.granularity

    @property
    def calendar(self):
        return self.e.calendar

    @property
    def events(self):
        return self.e.events

    @property
    def crowding(self):
        return self.e.crowding

    @property
    def regime(self) -> dict:
        return self.e.regime.last

    def volume_fraction(self) -> float:
        """Share of a day's volume typically done by 15:10 (1.0 when the whole daily bar is known)."""
        if self.e.granularity == "daily":
            return 1.0
        return float(self.e.cfg["session"].get("volume_fraction_1510", 0.85))

    # ---- market data ---------------------------------------------------------------------------
    def instrument(self, key: str):
        return self.e.instrument(key)

    def quote(self, key: str):
        return self.e.quotes.get(key)

    def last(self, key: str) -> float | None:
        return self.e.last_price(key)

    def has_depth(self, key: str) -> bool:
        q = self.quote(key)
        return bool(q and q.bid_qty is not None and q.ask_qty is not None)

    def book(self, key: str):
        """Deep order-book signals (core/book.py) for an instrument streaming depth, else None."""
        return self.e.book.state(key, self.now)

    def ofi(self, key: str, seconds: int = 60) -> float | None:
        return self.e.bars.ofi(key, self.now, seconds) if self.has_depth(key) else None

    def bars_df(self, key: str) -> pd.DataFrame:
        return self.e.bars_df(key)

    def vwap(self, key: str, k: float = 2.0) -> tuple[float | None, float | None, float | None]:
        acc = self.e.vwap_acc.get(key)
        if not acc or acc[1] <= 0:
            return None, None, None
        vw = acc[0] / acc[1]
        sd = math.sqrt(max(acc[2] / acc[1] - vw * vw, 0.0))
        return vw, vw + k * sd, vw - k * sd

    def day_volume(self, key: str) -> float:
        q = self.quote(key)
        if q and q.volume:
            return float(q.volume)
        b = self.e.day_bars.get(key)
        if b is not None:
            return float(b.volume)
        return float(sum(b.volume for b in self.e.today_bars.get(key, [])))

    def today_bar(self, key: str) -> dict | None:
        return self.e.today_bar(key)

    def snapshot(self, keys) -> None:
        """Make sure today's provisional bar is available for keys (on-demand quotes in live mode)."""
        self.e.snapshot(list(keys))

    def daily(self, key: str, with_today: bool = False, tail: int | None = None) -> pd.DataFrame:
        """Daily bars up to the previous session (+ today's provisional bar). Pass `tail` (rows) whenever the
        computation only needs recent history: rolling indicators on a tail are identical to the full-history
        values (EMAs converge within a few hundred rows) and far cheaper inside a daily loop."""
        d = self.e.daily_df(key)
        if tail:
            d = d.iloc[-tail:]
        if not with_today:
            return d
        tb = self.today_bar(key)
        if not tb:
            return d
        row = pd.DataFrame([tb], index=pd.DatetimeIndex([pd.Timestamp(self.today)], name="date"))
        return pd.concat([d, row[["open", "high", "low", "close", "volume"]]])

    def minute_history(self, key: str, days: int) -> pd.DataFrame:
        return self.e.minute_hist(key, days)

    def panel(self, keys, field: str = "close", tail: int | None = None, with_today: bool = True) -> pd.DataFrame:
        """Dates x instruments matrix (one field) for vectorised cross-sectional screens."""
        return self.e.panel(keys, field, tail, with_today)

    def bars5(self, key: str) -> list:
        """Completed 5-minute bars today, each with the session VWAP and sigma at its close (incremental)."""
        return self.e.bars5.get(key, [])

    def pmap(self, fn, items) -> list:
        """Map over items with the engine's I/O thread pool (live/forward); sequential inside backtest workers."""
        from ..parallel.pool import thread_map
        items = list(items)
        return thread_map(fn, items, self.e.io_threads if len(items) > 8 else 1)

    def can_enter(self) -> bool:
        return self.e.can_enter()

    def auction(self, key: str):
        try:
            return self.e.data.auction_snapshot(key) if self.e.mode in ("forward", "live") else None
        except Exception:
            return None

    def universe(self, name) -> list[str]:
        return self.e.universe_keys(name)

    def sector(self, key: str) -> str | None:
        return self.e.universe.sector(key)

    def is_cas(self, key: str) -> bool:
        return self.e.is_cas(key)

    # ---- portfolio & risk ----------------------------------------------------------------------
    def position(self, strategy: str, key: str):
        return self.e.portfolio.get(strategy, key)

    def positions(self, strategy: str):
        return self.e.portfolio.positions(strategy)

    def posture(self, strategy: str) -> tuple[bool, float, bool]:
        s = self.e.by_name[strategy]
        return self.e.regime.posture(s.horizon, s.style)

    def fragility(self) -> dict:
        """Today's market-fragility state (core.fragility): ar, ar_shift, tree_length, fragile."""
        return self.e.fragility

    def _mult(self, strategy: str, mult: float) -> float:
        s = self.e.by_name[strategy]
        allowed, pm, _ = self.posture(strategy)
        if not allowed:
            return 0.0
        if s.horizon == "swing" and self.e.fragility.get("fragile"):
            action = (self.e.cfg.get("fragility") or {}).get("action", "report")
            pm *= {"halve": 0.5, "block": 0.0}.get(action, 1.0)
        return mult * pm * self.e.crowding.global_multiplier(self.today) * (0.5 if s.probation else 1.0)

    def risk_amount(self, strategy: str, mult: float = 1.0) -> float:
        return self.e.risk.risk_amount(strategy, self._mult(strategy, mult))

    def size(self, strategy: str, key: str, entry: float, stop: float | None, mult: float = 1.0) -> int:
        s = self.e.by_name[strategy]
        m = self._mult(strategy, mult)
        if m <= 0 or not entry or stop is None:
            return 0
        d = self.daily(key, tail=760)
        adv = float((d["close"] * d["volume"]).iloc[-20:].mean()) if len(d) >= 5 else None
        gq = gap_quantile(d) if s.horizon == "swing" else None
        q = self.quote(key)
        touch = None
        if s.horizon == "intraday" and q is not None:
            touch = q.ask_qty if stop < entry else q.bid_qty           # long: the offer we buy from; short: the bid
        qty = self.e.risk.size(strategy=strategy, horizon=s.horizon, entry=entry, stop=stop,
                               lot=self.instrument(key).lot_size, mult=m, adv_value=adv, gap_q=gq,
                               avail_cash=self.available_cash(), touch_qty=touch)
        if qty > 0 and s.horizon == "swing":
            qty = self._es_limit(key, qty, entry)
        return qty

    def available_cash(self) -> float | None:
        """Cash / margin the order broker says is free (live only; refreshed every 60 s); None = unknown."""
        if self.e.mode != "live":
            return None
        t = getattr(self, "_cash_at", None)
        if t is not None and (self.now - t).total_seconds() < 60:
            return self._cash
        self._cash_at, self._cash = self.now, None
        fn = getattr(getattr(self.e, "execution", None), "get_funds", None)
        if fn is not None:
            try:
                v = (fn() or {}).get("available")
                self._cash = float(v) if v is not None else None
            except Exception as ex:
                log.warning("funds: %s", ex)
        return self._cash

    def _es_limit(self, key: str, qty: int, price: float) -> int:
        """Scale down so portfolio 97.5% expected shortfall (historical, 1-day) stays under es_cap x equity."""
        rc = self.e.cfg["risk"]
        book = {p.key: p.qty for p in self.e.portfolio.positions() if p.product == "CNC"}
        book[key] = book.get(key, 0) + qty
        rets = {}
        for k in book:
            d = self.daily(k, tail=int(rc.get("es_lookback", 500)) + 1)["close"]
            if len(d) > 60:
                rets[k] = d.pct_change()
        if key not in rets:
            return qty
        R = pd.DataFrame(rets).dropna()
        if len(R) < 60:
            return qty
        val = np.array([book[k] * (self.last(k) or price) for k in R.columns])
        es = expected_shortfall(R.to_numpy() @ val, float(rc.get("es_alpha", 0.975)))
        cap = float(rc.get("es_cap", 0.03)) * self.e.risk.base_equity()
        if es <= cap or es <= 0:
            return qty
        lot = self.instrument(key).lot_size
        return int(max(0, math.floor(qty * cap / es)) // lot * lot)

    def stats(self, strategy: str):
        return self.e.risk.stat(strategy)

    def trades(self, strategy: str) -> pd.DataFrame:
        return self.e.storage.trades_df(strategy)

    def record_result(self, strategy: str, r: float, pnl: float, entry: str | None = None) -> None:
        self.e.risk.record_result(strategy, r, pnl, self.now, entry)

    def disable(self, strategy: str, reason: str) -> None:
        self.e.disable(strategy, reason)

    # ---- orders --------------------------------------------------------------------------------
    def intraday_entry_block(self, req: OrderRequest) -> str | None:
        """Intraday (MIS) entries only between session.intraday_entry_start (09:30: the first 15 minutes swing too
        much) and session.no_new_intraday. Delivery (CNC) entries - swing trades, including ORB taken as a swing
        trade - are exempt; exits and stops are never blocked."""
        if req.intent != "entry" or req.product != Product.MIS:
            return None
        s = self.e.cfg.get("session") or {}
        t = self.now.timetz().replace(tzinfo=None) if self.now.tzinfo else self.now.time()
        a, b = hm(s.get("intraday_entry_start", "09:30")), hm(s.get("no_new_intraday", "14:30"))
        if t < a:
            return f"before {a:%H:%M}"
        if t >= b:
            return f"after {b:%H:%M}"
        return None

    def submit(self, req: OrderRequest) -> Order | None:
        why = self.intraday_entry_block(req)
        if why:
            seen = getattr(self, "_blocked_logged", set())
            k = (req.strategy, req.key, why, self.now.date())
            if k not in seen:
                seen.add(k)
                self._blocked_logged = seen
                log.info("[%s] intraday entry %s blocked: %s (intraday time rule)", req.strategy, req.key, why)
            return None
        self.e.ensure_subscribed(req.key)
        order = self.e.oms.submit(req)
        q = self.e.quotes.get(req.key)
        self.e.journal.on_submit(self.now, req, order, ref_price=q.ltp if q is not None and q.ltp else req.price)
        return order

    def cancel(self, client_id: str | None) -> bool:
        return self.e.oms.cancel(client_id)

    def modify(self, client_id: str | None, **kw) -> bool:
        return self.e.oms.modify(client_id, **kw)

    def order(self, client_id: str | None) -> Order | None:
        return self.e.oms.orders.get(client_id or "")

    def active_orders(self, strategy: str, key: str | None = None, intent: str | None = None) -> list[Order]:
        return self.e.oms.active(strategy, key, intent)

    # ---- state & logging -----------------------------------------------------------------------
    def state(self, strategy: str) -> dict:
        return self.e.strategy_state(strategy)

    def log(self, strategy: str, msg: str, level: str = "info") -> None:
        getattr(log, level, log.info)("[%s] %s", strategy, msg)
        if level in ("warning", "error"):
            self.e.storage.log_event(self.now, level.upper(), strategy, msg)
