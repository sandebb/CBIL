"""Where the desk gets its data: a running session's monitor (MonitorClient) or synthetic data (DemoClient)."""
from __future__ import annotations

import json
import math
import random
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # localhost: never via a system proxy


class MonitorClient:
    demo = False

    def __init__(self, url: str, token: str, timeout: float = 1.5):
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout

    @classmethod
    def from_run_dir(cls, run_dir) -> "MonitorClient":
        info = json.loads((Path(run_dir) / "monitor.json").read_text(encoding="utf-8"))
        return cls(info["url"], info["token"])

    def _req(self, path: str, method: str = "GET"):
        r = urllib.request.Request(self.url + path, method=method, headers={"X-Nsealgo-Token": self.token},
                                   data=b"" if method == "POST" else None)
        with NO_PROXY.open(r, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def status(self):
        return self._req("/api/status")

    def tiers(self):
        return self._req("/api/tiers")

    def books(self, keys):
        return self._req("/api/books?keys=" + ",".join(keys)) if keys else {}

    def watch(self):
        return self._req("/api/watch")

    def kill(self):
        return self._req("/api/kill", "POST")

    def strategy(self, name: str):
        return self._req("/api/strategy?name=" + urllib.parse.quote(name))

    def series(self, key: str, kind: str = "intraday"):
        return self._req(f"/api/series?key={urllib.parse.quote(key)}&kind={kind}")


class ReviewClient:
    """A saved session day (runs/<mode>/journal/<date>.jsonl and .bars.json) for the strategy windows, with no
    session running. Depth, market watch and the kill switch need a live session."""
    demo, review = False, True

    def __init__(self, run_dir, day: str | None = None, history_path=None):
        self.label = Path(run_dir).name
        self.folder = Path(run_dir) / "journal"
        self.history_path = history_path
        self._days = sorted(p.stem for p in self.folder.glob("*.jsonl")) if self.folder.exists() else []
        if not self._days:
            raise FileNotFoundError(f"no saved session days in {self.folder}")
        self.set_day(day or self._days[-1])

    def days(self) -> list[str]:
        return list(self._days)

    def set_day(self, day: str) -> None:
        from ..core.journal import SignalJournal
        if day not in self._days:
            raise ValueError(f"{day} isn't saved; saved days: {', '.join(self._days)}")
        self.day = day
        self.j = SignalJournal.read(self.folder / f"{day}.jsonl")
        bp = self.folder / f"{day}.bars.json"
        self.bars = json.loads(bp.read_text(encoding="utf-8")) if bp.exists() else {}

    def _marks(self) -> dict:
        return {k: b[-1][4] for k, b in self.bars.items() if b}

    @staticmethod
    def _horizon(name: str) -> str:
        try:
            from ..strategies import REGISTRY
            return getattr(REGISTRY.get(name), "horizon", "") or ""
        except Exception:
            return ""

    def strategy(self, name: str):
        from ..core.journal import strategy_view
        return strategy_view(self.j, name, self._marks(), self._horizon(name))

    def series(self, key: str, kind: str = "intraday"):
        if kind == "daily" and self.history_path:
            try:
                from ..data.history import HistoryStore
                df = HistoryStore(self.history_path).daily(key)
                bars = [[ts.timestamp(), r.open, r.high, r.low, r.close, r.volume] for ts, r in df.iterrows()]
                if bars:
                    return {"key": key, "kind": "daily", "bars": bars, "ltp": bars[-1][4]}
            except Exception:
                pass
        bars = self.bars.get(key, [])
        return {"key": key, "kind": "intraday", "bars": bars, "ltp": bars[-1][4] if bars else None}

    def status(self):
        trades = self.j.trades(None, self._marks())
        sign = {"long": 1, "short": -1}
        realized = sum((t["exit_price"] - t["entry_price"]) * t["qty"] * sign[t["direction"]] for t in trades
                       if t["status"] == "closed" and t["exit_price"] and t["entry_price"])
        positions = [{"strategy": t["strategy"], "key": t["key"], "product": "", "qty": t["qty"] * sign[t["direction"]],
                      "avg": t["entry_price"], "ltp": t["mark"],
                      "unrealized": round((t["mark"] - t["entry_price"]) * t["qty"] * sign[t["direction"]], 2) if t["mark"] else None,
                      "since": (t["entry_fill_ts"] or "")[11:19]} for t in trades if t["status"] == "open"]
        orders = [{"time": r.signal_ts.strftime("%H:%M:%S") if r.signal_ts else "", "strategy": r.strategy, "key": r.key,
                   "side": r.side, "qty": r.qty, "price": r.order_price or r.ref_price, "product": "", "intent": r.intent,
                   "status": r.status, "filled": r.filled_qty, "avg": r.fill_price or 0.0, "message": r.reason}
                  for r in reversed(self.j.recs)]
        names = sorted({r.strategy for r in self.j.recs})
        strategies = [{"name": n, "horizon": self._horizon(n), "active": True,
                       "positions": sum(p["strategy"] == n for p in positions),
                       "unrealized": round(sum(p["unrealized"] or 0 for p in positions if p["strategy"] == n), 2),
                       "open_orders": 0} for n in names]
        return {"mode": "review", "day": self.day, "label": self.label, "now": f"{self.day}T15:30:00", "capital": None,
                "equity": None, "realized": round(realized, 2), "costs": 0.0,
                "unrealized": round(sum(p["unrealized"] or 0 for p in positions), 2), "exposure": None, "regime": None,
                "kill": False, "positions": positions, "orders": orders, "strategies": strategies, "alerts": [],
                "instruments": len(self.bars)}

    def tiers(self):
        return []

    def books(self, keys):
        return {}

    def watch(self):
        return []

    def kill(self):
        raise RuntimeError("review mode: there is no running session to stop")


TBT = ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "BHARTIARTL", "ITC", "LT", "AXISBANK",
       "KOTAKBANK", "MARUTI", "SUNPHARMA", "TITAN", "BAJFINANCE"]
D30 = ["HINDUNILVR", "NTPC", "POWERGRID", "TATASTEEL", "JSWSTEEL", "ONGC", "COALINDIA", "ADANIPORTS", "WIPRO", "HCLTECH"]
REST = ["TECHM", "ULTRACEMCO", "NESTLEIND", "ASIANPAINT", "GRASIM", "CIPLA", "DRREDDY", "EICHERMOT", "HEROMOTOCO",
        "BRITANNIA", "DIVISLAB", "APOLLOHOSP", "TATAMOTORS", "M&M", "BAJAJFINSV", "INDUSINDBK", "SBILIFE", "HDFCLIFE",
        "TATACONSUM", "BPCL", "HINDALCO", "UPL", "SHREECEM", "PIDILITIND", "DABUR", "GODREJCP", "HAVELLS", "SIEMENS",
        "DLF", "AMBUJACEM", "BANKBARODA", "PNB", "CANBK", "IOC", "GAIL"]


class DemoClient:
    """Synthetic but realistic data for every window: random-walk prices, 50/30/5-level books with order counts,
    occasional large resting orders, and a small portfolio. Clearly labelled DEMO."""
    demo = True

    def __init__(self, seed: int = 7):
        self.seed = seed
        self.rng = random.Random(seed)
        self.keys = [f"NSE:{s}" for s in TBT + D30 + REST]
        self.levels = {k: (50 if i < len(TBT) else 30 if i < len(TBT) + len(D30) else 5) for i, k in enumerate(self.keys)}
        self.px = {k: round(self.rng.uniform(150, 4000), 2) for k in self.keys}
        self.prev = {k: round(v * (1 + self.rng.uniform(-0.015, 0.015)), 2) for k, v in self.px.items()}
        self.vol = {k: self.rng.randint(200_000, 6_000_000) for k in self.keys}
        self.ofi = {k: 0.0 for k in self.keys}
        self.wall = {k: (self.rng.random() < 0.25, self.rng.randint(3, 25), self.rng.choice(["bid", "ask"]))
                     for k in self.keys}
        self.killed = False
        self._series, self._strat = {}, {}

    # ---- strategy windows: price series and a day of signals that agree with them ---------------
    def series(self, key: str, kind: str = "intraday"):
        if (key, kind) in self._series:
            return self._series[(key, kind)]
        rng = random.Random(zlib.crc32(f"{key}|{kind}|{self.seed}".encode()))
        today = datetime.now(IST).replace(hour=9, minute=15, second=0, microsecond=0)
        px, bars = self.prev.get(key, 1000.0), []
        if kind == "daily":
            day = today - timedelta(days=200)
            while len(bars) < 130:
                day += timedelta(days=1)
                if day.weekday() < 5:
                    o = px
                    px = round(px * math.exp(rng.gauss(0.0004, 0.015)), 2)
                    bars.append([day.timestamp(), o, round(max(o, px) * (1 + abs(rng.gauss(0, 0.006))), 2),
                                 round(min(o, px) * (1 - abs(rng.gauss(0, 0.006))), 2), px, rng.randint(10**5, 10**7)])
        else:
            for m in range(375):
                o = px
                px = round(px * math.exp(rng.gauss(0, 0.0009)), 2)
                bars.append([(today + timedelta(minutes=m)).timestamp(), o, round(max(o, px) * (1 + abs(rng.gauss(0, 0.0004))), 2),
                             round(min(o, px) * (1 - abs(rng.gauss(0, 0.0004))), 2), px, rng.randint(2000, 90000)])
        out = {"key": key, "kind": kind, "bars": bars, "ltp": bars[-1][4], "now": bars[-1][0] + 60}
        self._series[(key, kind)] = out
        return out

    def strategy(self, name: str):
        if name in self._strat:
            return self._strat[name]
        from ..core.journal import summarize
        swing = name in ("momentum", "pullback", "pead")
        kind, step = ("daily", 86400) if swing else ("intraday", 60)
        rng = random.Random(zlib.crc32(f"{name}|{self.seed}".encode()))
        iso = lambda x: datetime.fromtimestamp(x, IST).isoformat(timespec="seconds")   # noqa: E731
        signals, trades = [], []
        picks = rng.sample(self.keys[:40], 8)
        for n, key in enumerate(picks):
            bars = self.series(key, kind)["bars"]
            i = rng.randint(12, len(bars) - (25 if swing else 80))
            long = True if swing else rng.random() < 0.6
            sign = 1 if long else -1
            t_sig = bars[i][0] + (6 * 3600 if swing else rng.randint(5, 50))
            ref = bars[i][4]
            limit = round(ref * (1 + sign * 0.0004), 2)
            t_ord = t_sig + rng.uniform(0.3, 1.5)
            unfilled = n == len(picks) - 1
            j = next((k for k in range(i + 1, len(bars)) if bars[k][3] <= limit <= bars[k][2]), None)
            base = {"strategy": name, "key": key, "qty": rng.choice([25, 50, 100, 150]), "stop": round(limit * (1 - sign * 0.006), 2)}
            if unfilled or j is None:                     # an entry that never filled: what it would have made
                last = bars[-1][4]
                signals.append({**base, "id": len(signals) + 1, "side": "BUY" if long else "SELL", "intent": "entry",
                                "signal_ts": iso(t_sig), "ref_price": ref, "order_price": round(limit * (1 - sign * 0.003), 2),
                                "reason": "", "order_ts": iso(t_ord), "status": "CANCELLED", "fill_ts": None,
                                "fill_price": None, "filled_qty": 0, "would_pct": round((last / ref - 1) * 100 * sign, 3)})
                continue
            t_fill = max(bars[j][0], t_ord) + rng.uniform(0.5, 30)
            still_open = n == len(picks) - 2
            x = min(j + rng.randint(3, 12) if swing else j + rng.randint(8, 70), len(bars) - 1)
            if n % 3 == 1:                                # demo realism: some trades lose (an adverse move)
                x = next((k for k in range(j + 2, min(len(bars), j + 90))
                          if (bars[k][4] / limit - 1) * sign < -(0.012 if swing else 0.0025)), x)
            exit_px = bars[x][4]
            t_xsig = bars[x][0] + (6 * 3600 if swing else rng.randint(5, 40))
            pct = round((exit_px / limit - 1) * 100 * sign, 3)
            signals.append({**base, "id": len(signals) + 1, "side": "BUY" if long else "SELL", "intent": "entry",
                            "signal_ts": iso(t_sig), "ref_price": ref, "order_price": limit, "reason": "",
                            "order_ts": iso(t_ord), "status": "FILLED", "fill_ts": iso(t_fill), "fill_price": limit,
                            "filled_qty": base["qty"]})
            tr = {"key": key, "strategy": name, "direction": "long" if long else "short", "qty": base["qty"],
                  "entry_signal_ts": iso(t_sig), "entry_order_ts": iso(t_ord), "entry_fill_ts": iso(t_fill),
                  "entry_price": limit, "stop": base["stop"], "exit_signal_ts": None, "exit_order_ts": None,
                  "exit_fill_ts": None, "exit_price": None, "exit_reason": "", "mark": None, "pct": None, "status": "open"}
            if still_open:
                tr["mark"] = bars[-1][4]
                tr["pct"] = round((tr["mark"] / limit - 1) * 100 * sign, 3)
            else:
                reason = "target" if pct > 0 else "stop" if pct < -0.4 else "time"
                signals.append({**base, "id": len(signals) + 1, "side": "SELL" if long else "BUY", "intent": "exit",
                                "signal_ts": iso(t_xsig), "ref_price": exit_px, "order_price": exit_px, "reason": reason,
                                "order_ts": iso(t_xsig + 0.4), "status": "FILLED", "fill_ts": iso(t_xsig + 1.2),
                                "fill_price": exit_px, "filled_qty": base["qty"]})
                tr.update(exit_signal_ts=iso(t_xsig), exit_order_ts=iso(t_xsig + 0.4), exit_fill_ts=iso(t_xsig + 1.2),
                          exit_price=exit_px, exit_reason=reason, pct=pct, status="closed")
            trades.append(tr)
        signals.sort(key=lambda s: s["signal_ts"])
        for k, s in enumerate(signals, 1):
            s["id"] = k
        out = {"name": name, "horizon": "swing" if swing else "intraday", "signals": signals,
               "trades": sorted(trades, key=lambda t: t["entry_fill_ts"]), "summary": summarize(trades)}
        self._strat[name] = out
        return out

    def _tick(self, k: str) -> float:
        self.px[k] = max(1.0, round(self.px[k] * math.exp(self.rng.gauss(0, 0.0006)), 2))
        self.vol[k] += self.rng.randint(0, 4000)
        self.ofi[k] = max(-4.0, min(4.0, 0.85 * self.ofi[k] + self.rng.gauss(0, 0.6)))
        return self.px[k]

    def _book(self, k: str):
        mid, n, tick = self._tick(k), self.levels[k], 0.05
        best_bid = math.floor(mid / tick) * tick
        has_wall, at, side = self.wall[k]
        bids, asks = [], []
        for i in range(n):
            for book, px in ((bids, best_bid - i * tick), (asks, best_bid + (i + 1) * tick)):
                q = int(self.rng.lognormvariate(5.6, 0.9)) + 1
                if has_wall and i == at and ((book is bids) == (side == "bid")):
                    q *= 25                                        # a large resting order
                book.append([round(px, 2), q, max(1, q // self.rng.randint(40, 220))])
        return bids, asks

    def _signals(self, k, bids, asks):
        wb = sum(b[1] * 0.5 ** i for i, b in enumerate(bids[:5]))
        wa = sum(a[1] * 0.5 ** i for i, a in enumerate(asks[:5]))
        has_wall, _, side = self.wall[k]
        ice = round(self.rng.uniform(3.2, 6.0), 2) if has_wall and self.rng.random() < 0.6 else round(self.rng.uniform(0, 1.2), 2)
        return {"imbalance": round((wb - wa) / (wb + wa), 3), "ofi_z": round(self.ofi[k], 2),
                "microprice_bps": round(self.rng.gauss(0, 0.6), 2), "pressure": round(math.log((wb + 1) / (wa + 1)), 3),
                "absorb_ask": ice if side == "ask" else 0.0, "absorb_bid": ice if side == "bid" else 0.0,
                "spread_bps": round((asks[0][0] - bids[0][0]) / bids[0][0] * 1e4, 2), "levels": len(bids)}

    def tiers(self):
        t, d = self.keys[:15], self.keys[15:25]
        return [{"name": "Fyers TBT (demo)", "levels": 50, "groups": [t[0:5], t[5:10], t[10:15]]},
                {"name": "Upstox D30 (demo)", "levels": 30, "groups": [d]}]

    def books(self, keys):
        out, now = {}, datetime.now(IST)
        for k in keys:
            if k not in self.px:
                continue
            bids, asks = self._book(k)
            out[k] = {"ts": now.isoformat(timespec="milliseconds"), "ltp": self.px[k], "prev_close": self.prev[k],
                      "volume": self.vol[k], "bids": bids, "asks": asks,
                      "tbq": sum(b[1] for b in bids) * self.rng.randint(3, 8) if self.levels[k] == 50 else None,
                      "tsq": sum(a[1] for a in asks) * self.rng.randint(3, 8) if self.levels[k] == 50 else None,
                      "signals": self._signals(k, bids, asks)}
        return out

    def watch(self):
        rows = []
        for k, b in self.books(self.keys).items():
            rows.append({"key": k, "ltp": b["ltp"], "chg": round((b["ltp"] / b["prev_close"] - 1) * 100, 2),
                         "volume": b["volume"], "bid": b["bids"][0][0], "ask": b["asks"][0][0],
                         "spread_bps": b["signals"]["spread_bps"], "levels": self.levels[k], "signals": b["signals"]})
        return rows

    def status(self):
        now = datetime.now(IST)
        held = [("orb", "NSE:RELIANCE", 120), ("vwap_fade", "NSE:ICICIBANK", -200), ("momentum", "NSE:TITAN", 35)]
        positions = []
        for strat, k, q in held:
            avg = self.prev[k] * (1 + self.rng.uniform(-0.004, 0.004))
            positions.append({"strategy": strat, "key": k, "product": "CNC" if strat == "momentum" else "MIS", "qty": q,
                              "avg": round(avg, 2), "ltp": self.px[k], "unrealized": round((self.px[k] - avg) * q, 2),
                              "since": "09:31:07"})
        unreal = sum(p["unrealized"] for p in positions)
        orders = [{"time": (now - timedelta(minutes=m)).strftime("%H:%M:%S"), "strategy": s, "key": k, "side": side,
                   "qty": q, "price": round(self.px[k], 2), "product": "MIS", "intent": intent, "status": st,
                   "filled": q if st == "FILLED" else 0, "avg": round(self.px[k], 2) if st == "FILLED" else 0.0, "message": ""}
                  for m, s, k, side, q, intent, st in [(1, "orb", "NSE:RELIANCE", "BUY", 120, "entry", "FILLED"),
                                                        (3, "vwap_fade", "NSE:ICICIBANK", "SELL", 200, "entry", "FILLED"),
                                                        (4, "orb", "NSE:SBIN", "BUY", 300, "entry", "OPEN"),
                                                        (9, "scalper", "NSE:ITC", "BUY", 500, "entry", "CANCELLED")]]
        strategies = [{"name": n, "horizon": h, "active": a, "positions": sum(1 for p in positions if p["strategy"] == n),
                       "unrealized": round(sum(p["unrealized"] for p in positions if p["strategy"] == n), 2),
                       "open_orders": sum(1 for o in orders if o["strategy"] == n and o["status"] == "OPEN")}
                      for n, h, a in [("orb", "intraday", True), ("vwap_fade", "intraday", True),
                                      ("compression_breakout", "intraday", True), ("scalper", "intraday", False),
                                      ("momentum", "swing", True), ("pullback", "swing", True), ("pead", "swing", False)]]
        alerts = [{"ts": (now - timedelta(minutes=7)).isoformat(timespec="seconds"), "level": "WARNING", "source": "fyers_tbt",
                   "message": "demo: TBT connection 2 reconnected and resubscribed"},
                  {"ts": (now - timedelta(minutes=2)).isoformat(timespec="seconds"), "level": "WARNING", "source": "book",
                   "message": "demo: iceberg on the ask in NSE:SBIN held through 4.1x its displayed size"}]
        return {"mode": "demo", "now": now.isoformat(timespec="seconds"), "capital": 1_000_000.0,
                "equity": round(1_000_000 + 5240.5 + unreal, 2), "realized": 5240.5, "costs": 812.4,
                "unrealized": round(unreal, 2), "exposure": round(sum(abs(p["qty"]) * p["ltp"] for p in positions), 2),
                "regime": "neutral", "kill": self.killed, "positions": positions, "orders": orders,
                "strategies": strategies, "alerts": alerts, "instruments": len(self.keys)}

    def kill(self):
        self.killed = True
        return {"kill": True}
