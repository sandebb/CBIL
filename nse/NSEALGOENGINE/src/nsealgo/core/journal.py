"""Every trading signal's life, for the trading desk's strategy windows.

A signal is the moment a strategy decides to enter or exit (Context.submit). The record then follows its order: when
the OMS sent it, its status, and its fills (time, average price). Entry and exit fills of the same strategy and scrip
are paired into trades with their % profit or loss; an open trade is marked to the latest price. A signal whose order
never filled (blocked, rejected, cancelled) keeps the price at the signal, so the desk can show what it would have
made.

Saved as it happens (config `journal`): every signal, fill and status change is appended to
<run_dir>/journal/<date>.jsonl, and the minute bars of every scrip with a signal go to <date>.bars.json (every five
minutes and at the end of the day). A restart on the same day reloads the file first; `nsealgo desk --review` charts
any saved day without a session.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def _iso(ts):
    return ts.isoformat(timespec="seconds") if isinstance(ts, datetime) else ts


def _dt(x):
    try:
        return datetime.fromisoformat(x) if isinstance(x, str) and x else None
    except ValueError:
        return None


@dataclass
class SignalRec:
    id: int
    strategy: str
    key: str
    side: str                       # BUY / SELL
    intent: str                     # entry / exit
    qty: int
    signal_ts: datetime
    ref_price: float | None         # last price when the strategy decided
    order_price: float | None       # the limit sent (None for market)
    stop: float | None = None
    reason: str = ""
    client_id: str | None = None
    order_ts: datetime | None = None
    status: str = "BLOCKED"         # BLOCKED = risk or OMS refused it before sending
    fill_ts: datetime | None = None
    fill_price: float | None = None
    filled_qty: int = 0

    def as_dict(self) -> dict:
        return {"id": self.id, "strategy": self.strategy, "key": self.key, "side": self.side, "intent": self.intent,
                "qty": self.qty, "signal_ts": _iso(self.signal_ts), "ref_price": self.ref_price,
                "order_price": self.order_price, "stop": self.stop, "reason": self.reason, "client_id": self.client_id,
                "order_ts": _iso(self.order_ts), "status": self.status, "fill_ts": _iso(self.fill_ts),
                "fill_price": None if self.fill_price is None else round(self.fill_price, 4), "filled_qty": self.filled_qty}


class SignalJournal:
    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.recs: list[SignalRec] = []
        self.by_cid: dict[str, SignalRec] = {}
        self.path: Path | None = None
        self._loading = False

    # ---- on disk -------------------------------------------------------------------------------
    def open_day(self, folder, day) -> Path:
        """Save this day to <folder>/<day>.jsonl, after reloading whatever an earlier run of the same day wrote."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / f"{day}.jsonl"
        self.reset()
        if p.exists():
            self._replay(p)
        else:
            p.touch()
        self.path = p
        return p

    @classmethod
    def read(cls, path) -> "SignalJournal":
        j = cls()
        j._replay(Path(path))
        return j

    def _write(self, event: dict) -> None:
        if self.path is None or self._loading:
            return
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, default=str) + "\n")

    def _replay(self, p: Path) -> None:
        self._loading = True
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    ev = json.loads(line) if line.strip() else None
                except ValueError:
                    continue                              # a half-written last line after a crash
                if not ev:
                    continue
                kind = ev.get("e")
                if kind == "signal":
                    rec = SignalRec(id=ev["id"], strategy=ev["strategy"], key=ev["key"], side=ev["side"],
                                    intent=ev["intent"], qty=ev["qty"], signal_ts=_dt(ev["signal_ts"]),
                                    ref_price=ev.get("ref_price"), order_price=ev.get("order_price"),
                                    stop=ev.get("stop"), reason=ev.get("reason", ""), client_id=ev.get("client_id"),
                                    order_ts=_dt(ev.get("order_ts")), status=ev.get("status", "BLOCKED"))
                    self.recs.append(rec)
                    if rec.client_id:
                        self.by_cid[rec.client_id] = rec
                elif kind == "fill":
                    self.on_fill(ev["cid"], _dt(ev["ts"]), ev["price"], ev["qty"])
                elif kind == "status" and ev.get("cid") in self.by_cid:
                    self.by_cid[ev["cid"]].status = ev["status"]
        finally:
            self._loading = False

    def save_bars(self, bars_by_key: dict) -> None:
        """Minute bars of every scrip with a signal today, for charting the day later."""
        if self.path is None:
            return
        data = {}
        for k in {r.key for r in self.recs}:
            data[k] = [[b.ts.timestamp(), b.open, b.high, b.low, b.close, b.volume] for b in list(bars_by_key.get(k, []))]
        p = self.path.with_suffix(".bars.json")
        tmp = self.path.with_suffix(".bars.tmp")
        with self._lock:
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(p)

    def on_submit(self, now: datetime, req, order, ref_price: float | None = None) -> SignalRec:
        meta = req.meta or {}
        rec = SignalRec(id=len(self.recs) + 1, strategy=req.strategy, key=req.key, side=req.side.name,
                        intent=req.intent, qty=int(req.qty), signal_ts=now, ref_price=ref_price, order_price=req.price,
                        stop=meta.get("stop"), reason=str(meta.get("reason", "") or ""))
        if order is not None:
            rec.client_id, rec.order_ts, rec.status = order.client_id, order.created or now, order.status.name
            self.by_cid[order.client_id] = rec
        self.recs.append(rec)
        self._write({"e": "signal", **rec.as_dict()})
        return rec

    def on_fill(self, client_id: str, ts: datetime, price: float, qty: int) -> None:
        rec = self.by_cid.get(client_id)
        if rec is None or qty <= 0:
            return
        total = rec.filled_qty + int(qty)
        rec.fill_price = ((rec.fill_price or 0.0) * rec.filled_qty + float(price) * int(qty)) / total
        rec.filled_qty = total
        rec.fill_ts = rec.fill_ts or ts
        self._write({"e": "fill", "cid": client_id, "ts": _iso(ts), "price": float(price), "qty": int(qty)})

    def sync(self, orders: dict) -> None:
        for cid, rec in list(self.by_cid.items()):
            o = orders.get(cid)
            if o is not None and o.status.name != rec.status:
                rec.status = o.status.name
                self._write({"e": "status", "cid": cid, "status": rec.status})

    def signals(self, strategy: str | None = None) -> list[SignalRec]:
        return [r for r in self.recs if strategy is None or r.strategy == strategy]

    def trades(self, strategy: str | None = None, marks: dict | None = None) -> list[dict]:
        """Entry fill paired with the next exit fill of the same strategy and scrip (scale-ins: the first entry)."""
        out, open_ = [], {}
        for r in self.signals(strategy):
            k = (r.strategy, r.key)
            if r.intent == "entry" and r.filled_qty > 0:
                open_.setdefault(k, r)
            elif r.intent == "exit" and r.filled_qty > 0 and k in open_:
                out.append(self._trade(open_.pop(k), r))
        out += [self._trade(e, None, (marks or {}).get(e.key)) for e in open_.values()]
        return sorted(out, key=lambda t: t["entry_fill_ts"] or "")

    @staticmethod
    def _trade(e: SignalRec, x: SignalRec | None, mark: float | None = None) -> dict:
        sign = 1 if e.side == "BUY" else -1
        out_px = x.fill_price if x is not None else mark
        pct = (out_px / e.fill_price - 1) * 100 * sign if out_px and e.fill_price else None
        return {"key": e.key, "strategy": e.strategy, "direction": "long" if sign > 0 else "short", "qty": e.filled_qty,
                "entry_signal_ts": _iso(e.signal_ts), "entry_order_ts": _iso(e.order_ts), "entry_fill_ts": _iso(e.fill_ts),
                "entry_price": e.fill_price, "stop": e.stop,
                "exit_signal_ts": _iso(x.signal_ts) if x else None, "exit_order_ts": _iso(x.order_ts) if x else None,
                "exit_fill_ts": _iso(x.fill_ts) if x else None, "exit_price": x.fill_price if x else None,
                "exit_reason": x.reason if x else "", "mark": None if x else mark,
                "pct": None if pct is None else round(pct, 3), "status": "closed" if x else "open"}


def summarize(trades: list[dict]) -> dict:
    closed = [t["pct"] for t in trades if t["status"] == "closed" and t["pct"] is not None]
    wins = [p for p in closed if p > 0]
    return {"trades": len(closed), "open": sum(t["status"] == "open" for t in trades), "wins": len(wins),
            "win_rate": round(100 * len(wins) / len(closed), 1) if closed else None,
            "avg_pct": round(sum(closed) / len(closed), 3) if closed else None,
            "total_pct": round(sum(closed), 3) if closed else 0.0,
            "best_pct": max(closed) if closed else None, "worst_pct": min(closed) if closed else None}


def strategy_view(journal: SignalJournal, name: str, marks: dict, horizon: str = "") -> dict:
    """Signals (with what unfilled entries would have made), trades and summary for one strategy."""
    signals = []
    for r in journal.signals(name):
        d = r.as_dict()
        if r.intent == "entry" and not r.filled_qty and r.ref_price and marks.get(r.key):
            d["would_pct"] = round((marks[r.key] / r.ref_price - 1) * 100 * (1 if r.side == "BUY" else -1), 3)
        signals.append(d)
    trades = journal.trades(name, marks)
    return {"name": name, "horizon": horizon, "signals": signals, "trades": trades, "summary": summarize(trades)}
