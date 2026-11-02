"""Quotes -> 1-minute bars, with Cont–Kukanov–Stoikov order-flow imbalance (OFI) tracked per bar."""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta

from .models import Bar, Quote


class BarBuilder:
    def __init__(self) -> None:
        self._cur: dict[str, Bar] = {}
        self._vol: dict[str, float] = {}
        self._prev: dict[str, Quote] = {}
        self._ofi: dict[str, deque] = defaultdict(deque)

    @staticmethod
    def ofi_increment(prev: Quote | None, q: Quote) -> float:
        """e = [bid-queue change] - [ask-queue change] at the best quotes (Cont, Kukanov & Stoikov 2014)."""
        if prev is None or None in (prev.bid, prev.ask, prev.bid_qty, prev.ask_qty, q.bid, q.ask, q.bid_qty, q.ask_qty):
            return 0.0
        eb = (q.bid_qty if q.bid >= prev.bid else 0.0) - (prev.bid_qty if q.bid <= prev.bid else 0.0)
        ea = (q.ask_qty if q.ask <= prev.ask else 0.0) - (prev.ask_qty if q.ask >= prev.ask else 0.0)
        return float(eb - ea)

    def on_quote(self, q: Quote) -> list[Bar]:
        out: list[Bar] = []
        minute = q.ts.replace(second=0, microsecond=0)
        cur = self._cur.get(q.key)
        if cur is not None and minute > cur.ts:
            out.append(cur)
            cur = None
        last_vol = self._vol.get(q.key)
        if last_vol is None:   # first print of the day carries the pre-open auction volume
            dv = q.volume if (minute.hour, minute.minute) == (9, 15) else 0.0
        else:
            dv = max(q.volume - last_vol, 0.0)
        self._vol[q.key] = q.volume
        e = 0.0
        if None not in (q.bid, q.ask, q.bid_qty, q.ask_qty):   # OFI only between consecutive depth snapshots
            e = self.ofi_increment(self._prev.get(q.key), q)
            self._prev[q.key] = q
        if cur is None:
            cur = Bar(q.key, minute, q.ltp, q.ltp, q.ltp, q.ltp, dv, e)
        else:
            cur.high = max(cur.high, q.ltp)
            cur.low = min(cur.low, q.ltp)
            cur.close = q.ltp
            cur.volume += dv
            cur.ofi += e
        self._cur[q.key] = cur
        dq = self._ofi[q.key]
        dq.append((q.ts, e))
        while dq and (q.ts - dq[0][0]).total_seconds() > 600:
            dq.popleft()
        return out

    def flush(self, now: datetime) -> list[Bar]:
        done = [b for b in self._cur.values() if b.ts + timedelta(minutes=1) <= now]
        for b in done:
            del self._cur[b.key]
        return done

    def ofi(self, key: str, now: datetime, seconds: int = 60) -> float:
        return float(sum(e for ts, e in self._ofi.get(key, ()) if (now - ts).total_seconds() <= seconds))
