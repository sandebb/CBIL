"""Read-only views of a running engine for the trading desk. Built on the HTTP server's thread while the engine runs
on its own, so every collection is copied first and a read that collides with an update is simply retried."""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime

ALERTS: deque = deque(maxlen=300)              # recent warnings and errors, newest last


class AlertHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            ALERTS.append({"ts": datetime.fromtimestamp(record.created).isoformat(timespec="seconds"),
                           "level": record.levelname, "source": record.name.replace("nsealgo.", ""),
                           "message": record.getMessage()[:300]})
        except Exception:
            pass


def _num(x, nd: int = 2):
    try:
        return None if x is None else round(float(x), nd)
    except (TypeError, ValueError):
        return None


def _call(x):
    return x() if callable(x) else x


def _copy(d) -> list:
    for _ in range(5):
        try:
            return list(d.items()) if hasattr(d, "items") else list(d)
        except RuntimeError:                  # changed size while the engine thread wrote to it
            continue
    return []


class EngineSnapshot:
    def __init__(self, engine):
        self.e = engine

    # ---- helpers -------------------------------------------------------------------------------
    def _overlay(self):
        obj, seen = getattr(self.e, "feed", None), set()
        while obj is not None and id(obj) not in seen:
            seen.add(id(obj))
            if hasattr(obj, "tiers"):
                return obj
            obj = getattr(obj, "inner", None)
        return None

    def _signals(self, key: str, now):
        book = getattr(self.e, "book", None)
        s = book.state(key, now) if book is not None else None
        if s is None:
            return None
        return {"imbalance": _num(s.imbalance, 3), "ofi_z": _num(s.ofi_z), "microprice_bps": _num(s.microprice_bps),
                "pressure": _num(s.pressure, 3), "absorb_ask": _num(s.absorb_ask), "absorb_bid": _num(s.absorb_bid),
                "spread_bps": _num(s.spread_bps), "levels": s.levels}

    # ---- views ---------------------------------------------------------------------------------
    def status(self) -> dict:
        e, now = self.e, self.e.clock.now()
        pf = e.portfolio
        quotes = dict(_copy(e.quotes))
        positions = []
        for p in pf.positions():
            if not p.qty:
                continue
            ltp = getattr(quotes.get(p.key), "ltp", None)
            positions.append({"strategy": p.strategy, "key": p.key, "product": p.product, "qty": p.qty,
                              "avg": _num(p.avg), "ltp": _num(ltp),
                              "unrealized": _num((ltp - p.avg) * p.qty) if ltp else None, "since": p.entry_ts})
        orders = sorted((o for _, o in _copy(e.oms.orders)), key=lambda o: o.updated or o.created or now, reverse=True)
        order_rows = [{"time": (o.updated or o.created or now).strftime("%H:%M:%S"), "strategy": o.req.strategy,
                       "key": o.req.key, "side": o.req.side.name, "qty": o.req.qty, "price": _num(o.req.price),
                       "product": getattr(o.req.product, "value", str(o.req.product)), "intent": o.req.intent,
                       "status": o.status.name, "filled": o.filled_qty, "avg": _num(o.avg_price),
                       "message": o.message} for o in orders[:150]]
        active = {s.name for s in (e.active() if hasattr(e, "active") else [])}
        strategies = []
        for st in e.strategies:
            mine = [p for p in positions if p["strategy"] == st.name]
            strategies.append({"name": st.name, "horizon": getattr(st, "horizon", ""), "active": st.name in active,
                               "positions": len(mine),
                               "unrealized": _num(sum(p["unrealized"] or 0 for p in mine)),
                               "open_orders": len(e.oms.active(st.name)) if hasattr(e.oms, "active") else 0})
        kill = getattr(e, "run_dir", None)
        return {"mode": e.mode, "now": now.isoformat(timespec="seconds"), "capital": _num(pf.capital),
                "equity": _num(_call(pf.equity)), "realized": _num(pf.realized), "costs": _num(pf.costs),
                "unrealized": _num(pf.unrealized()), "exposure": _num(pf.gross_exposure()),
                "regime": getattr(getattr(e, "regime", None), "state", None),
                "kill": bool(kill and (kill / "KILL").exists()), "positions": positions, "orders": order_rows,
                "strategies": strategies, "alerts": list(ALERTS)[-100:], "instruments": len(quotes)}

    def tiers(self) -> list[dict]:
        ov = self._overlay()
        if ov is not None:
            return ov.tiers()
        deep = sorted(((len(q.bids), k) for k, q in _copy(self.e.quotes) if q.bids), reverse=True)[:15]
        if not deep:
            return []
        keys = [k for _, k in deep]
        return [{"name": "Deepest books", "levels": deep[0][0], "groups": [keys[i:i + 5] for i in range(0, len(keys), 5)]}]

    def books(self, keys) -> dict:
        e, now = self.e, self.e.clock.now()
        ov = self._overlay()
        out = {}
        for k in keys:
            q = e.quotes.get(k)
            if q is None:
                continue
            tot = ov.totals(k) if ov is not None and hasattr(ov, "totals") else None
            out[k] = {"ts": q.ts.isoformat(timespec="milliseconds"), "ltp": q.ltp, "prev_close": q.prev_close,
                      "volume": q.volume, "bids": [list(x) for x in q.bids], "asks": [list(x) for x in q.asks],
                      "tbq": tot[0] if tot else None, "tsq": tot[1] if tot else None,
                      "signals": self._signals(k, now)}
        return out

    def watch(self) -> list[dict]:
        e, now = self.e, self.e.clock.now()
        rows = []
        for k, q in _copy(e.quotes):
            pc = q.prev_close or 0
            rows.append({"key": k, "ltp": q.ltp, "chg": _num((q.ltp / pc - 1) * 100) if pc and q.ltp else None,
                         "volume": q.volume, "bid": q.bid, "ask": q.ask,
                         "spread_bps": _num((q.ask - q.bid) / q.ltp * 1e4) if q.ltp and q.ask and q.bid else None,
                         "levels": min(len(q.bids), len(q.asks)), "signals": self._signals(k, now)})
        return rows

    def strategy(self, name: str) -> dict:
        from ..core.journal import SignalJournal, strategy_view
        j = getattr(self.e, "journal", None) or SignalJournal()
        st = next((s for s in self.e.strategies if s.name == name), None)
        j.sync(dict(_copy(self.e.oms.orders)))
        marks = {k: q.ltp for k, q in _copy(self.e.quotes) if q.ltp}
        return strategy_view(j, name, marks, getattr(st, "horizon", ""))

    def series(self, key: str, kind: str = "intraday") -> dict:
        """Price bars for a scrip's chart: today's minute bars, or ~6 months of daily bars for swing strategies."""
        e = self.e
        bars = []
        if kind == "daily":
            try:
                df = e.history.daily(key).tail(130)
                bars = [[ts.timestamp(), r.open, r.high, r.low, r.close, r.volume] for ts, r in df.iterrows()]
            except Exception:
                bars = []
        else:
            for b in list(getattr(e, "today_bars", {}).get(key, [])):
                bars.append([b.ts.timestamp(), b.open, b.high, b.low, b.close, b.volume])
        q = e.quotes.get(key)
        return {"key": key, "kind": kind, "bars": bars, "ltp": q.ltp if q is not None else None,
                "now": e.clock.now().timestamp()}

    def kill(self) -> dict:
        p = self.e.run_dir / "KILL"
        p.write_text(datetime.now().isoformat(), encoding="utf-8")
        logging.getLogger("nsealgo.desk").warning("kill switch set from the trading desk")
        return {"kill": True, "path": str(p)}
