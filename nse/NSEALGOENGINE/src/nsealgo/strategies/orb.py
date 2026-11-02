"""1) Stocks-in-Play opening-range breakout (Zarattini, Barbon & Aziz 2024, adapted to NSE F&O stocks).

Rank F&O stocks by relative volume of the first 5 minutes; trade the direction of the 5-minute candle
with a stop-limit beyond the opening range. With live depth data the entry is synthetic and must be
confirmed by order-flow imbalance (OFI); otherwise an exchange stop-limit is used (works when the
process lags and in replay). Stops sit a fraction of ATR away and off round numbers. Flat by 15:05.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from ..core.clock import hm
from ..core.indicators import atr, window_volume
from ..core.models import OrderType, Product, Side
from ..core.stats import intraday_variance_ratio
from .base import Strategy


class StocksInPlayORB(Strategy):
    name = "orb"
    horizon = "intraday"
    style = "liquidity_taking"
    prior = (0.35, 2.5)
    default_params = {"universe": "fno", "max_watch": 250, "or_minutes": 5, "top_n": 8, "min_rvol": 1.0,
                      "rvol_lookback": 14, "min_atr_pct": 0.02, "stop_atr_frac": 0.15, "entry_buffer_atr": 0.05,
                      "ofi_confirm": True, "ofi_seconds": 60, "book_min_ofi_z": 0.0,
                      "book_max_absorb": 3.0, "doji_frac": 0.1, "max_positions": 6,
                      "limit_slip": 0.003, "cutoff": "14:30",
                      # variance-ratio gate (off by default; see docs/04-design/STRATEGY_RESEARCH.md): trade only names whose
                      # 5-minute returns have trended over the last vr_days sessions (VR z* >= vr_z_min)
                      "trend_filter": False, "vr_q": 6, "vr_days": 10, "vr_z_min": 1.0}

    @property
    def scan_at(self) -> str:
        return (datetime(2000, 1, 1, 9, 15) + timedelta(minutes=int(self.p["or_minutes"]))).strftime("%H:%M")

    def _universe(self, ctx):
        return ctx.universe(self.p["universe"])[: int(self.p["max_watch"])]

    def data_needs(self, ctx):
        u = self._universe(ctx)
        return {"daily": u, "daily_days": 60, "minute": u, "minute_days": int(self.p["rvol_lookback"]) + 1}

    def live_keys(self, ctx):
        return list(ctx.state(self.name).get("pre", {}))

    def timers(self):
        return [self.scan_at, self.p["cutoff"]]

    def on_session_start(self, ctx):
        st = ctx.state(self.name)
        st.clear()
        st.update({"pre": {}, "picks": {}, "stops": {}})
        lb = int(self.p["rvol_lookback"])
        for key in self._universe(ctx):
            d = ctx.daily(key, tail=40)
            if len(d) < 20:
                continue
            a, pc = float(atr(d, 14).iloc[-1]), float(d["close"].iloc[-1])
            if not (a > 0 and pc > 0) or a / pc < self.p["min_atr_pct"]:
                continue
            base = window_volume(ctx.minute_history(key, lb), time(9, 15), hm(self.scan_at), lb)
            if base and base > 0 and self.p["trend_filter"]:
                _, z = intraday_variance_ratio(ctx.minute_history(key, int(self.p["vr_days"])), int(self.p["vr_q"]))
                if not z >= float(self.p["vr_z_min"]):          # NaN (too little data) also excludes
                    continue
            if base and base > 0:
                st["pre"][key] = {"atr": a, "base": base}

    def on_timer(self, ctx, t):
        if t == self.scan_at:
            self._scan(ctx)
        elif t == self.p["cutoff"]:
            for o in ctx.active_orders(self.name, intent="entry"):
                ctx.cancel(o.client_id)

    def _scan(self, ctx):
        st = ctx.state(self.name)
        allowed, _, _ = ctx.posture(self.name)
        if not allowed:
            ctx.log(self.name, f"regime {ctx.regime['state']}: no entries")
            return
        n = int(self.p["or_minutes"])
        cands = []
        for key, pre in st["pre"].items():
            bars = ctx.bars_df(key)
            if len(bars) < 1:
                continue
            orb = bars.iloc[:n]
            o, h, l, c = orb["open"].iloc[0], orb["high"].max(), orb["low"].min(), orb["close"].iloc[-1]
            rvol = ctx.day_volume(key) / pre["base"]
            rng = h - l
            if rvol < self.p["min_rvol"] or rng <= 0 or abs(c - o) < self.p["doji_frac"] * rng:
                continue
            cands.append((rvol, key, Side.BUY if c > o else Side.SELL, float(h), float(l)))
        cands.sort(key=lambda x: -x[0])
        for rvol, key, side, h, l in cands[: int(self.p["top_n"])]:
            a, d = st["pre"][key]["atr"], side.sign
            trig = self.round_level(ctx, key, (h if d > 0 else l) + d * self.p["entry_buffer_atr"] * a, d)
            stop = self.round_level(ctx, key, trig - d * self.p["stop_atr_frac"] * a, -d)
            st["picks"][key] = {"side": side.value, "trigger": trig, "stop": stop, "rvol": round(rvol, 2), "done": False}
            if not (self.p["ofi_confirm"] and ctx.has_depth(key)):
                self._place_stop_entry(ctx, key)
        ctx.log(self.name, f"{len(cands)} stocks in play; picks: {', '.join(st['picks']) or 'none'}")

    def _open_count(self, ctx) -> int:
        return len(ctx.positions(self.name)) + len(ctx.active_orders(self.name, intent="entry"))

    def _place_stop_entry(self, ctx, key):
        pk = ctx.state(self.name)["picks"][key]
        if pk["done"] or self._open_count(ctx) >= int(self.p["max_positions"]):
            return
        side, trig, stop = Side(pk["side"]), pk["trigger"], pk["stop"]
        qty = ctx.size(self.name, key, trig, stop)
        lim = trig * (1 + self.p["limit_slip"]) if side == Side.BUY else trig * (1 - self.p["limit_slip"])
        if self.enter(ctx, key, side, qty, product=Product.MIS, stop=stop, entry_ref=trig,
                      order_type=OrderType.SL, price=lim, trigger=trig):
            pk["done"] = True

    def _book_ok(self, ctx, key: str, side) -> bool | None:
        """Deep-book check for a breakout: multi-level flow must point the same way and no iceberg may sit
        against it. None when the instrument has no fresh depth (the top-of-book check applies instead)."""
        b = ctx.book(key) if hasattr(ctx, "book") else None
        if b is None:
            return None
        against = b.absorb_ask if side == Side.BUY else b.absorb_bid
        return b.ofi_z * side.sign > float(self.p["book_min_ofi_z"]) and against < float(self.p["book_max_absorb"])

    def on_quote(self, ctx, q):
        pk = ctx.state(self.name).get("picks", {}).get(q.key)
        if not pk or pk["done"] or ctx.now.time() >= hm(self.p["cutoff"]):
            return
        side = Side(pk["side"])
        if not (q.ltp >= pk["trigger"] if side == Side.BUY else q.ltp <= pk["trigger"]):
            return
        meta = None
        if self.p.get("ofi_confirm", True):
            ok = self._book_ok(ctx, q.key, side)
            if ok is False:
                if not pk.get("book_blocked"):
                    pk["book_blocked"] = True
                    st = ctx.state(self.name)
                    st["book_blocked"] = st.get("book_blocked", 0) + 1
                return   # the deep book disagrees (flow against the breakout, or hidden size in the way): wait
            if ok is None:
                ofi = ctx.ofi(q.key, int(self.p["ofi_seconds"]))
                if ofi is not None and ofi * side.sign <= 0:
                    return   # breakout without supporting order flow: wait
            else:
                b = ctx.book(q.key)
                meta = {"ofi_z": round(b.ofi_z, 2), "absorb": round(b.absorb_ask if side == Side.BUY else b.absorb_bid, 2)}
        if self._open_count(ctx) >= int(self.p["max_positions"]):
            return
        qty = ctx.size(self.name, q.key, q.ltp, pk["stop"])
        if self.enter(ctx, q.key, side, qty, product=Product.MIS, stop=pk["stop"], entry_ref=q.ltp, meta=meta):
            pk["done"] = True
