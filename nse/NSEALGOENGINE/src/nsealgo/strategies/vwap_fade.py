"""3) Quiet-day VWAP fade (probation; half size). Liquidity-providing, so calm regime only.

After 10:15, when a 5-minute bar closes back inside the 2-sigma VWAP band after closing outside it,
fade toward VWAP — but only if the day is quiet (volume so far below its usual pace) and flow is not
toxic (VPIN below its 80th percentile). Stop beyond the extreme, target VWAP, 60-minute time stop.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

from datetime import datetime, timedelta

import pandas as pd

from ..core.clock import hm
from ..core.indicators import cum_volume_profile, vpin
from ..core.models import Product, Side
from ..core.stats import intraday_variance_ratio
from .base import Strategy


class QuietDayVWAPFade(Strategy):
    name = "vwap_fade"
    horizon = "intraday"
    style = "liquidity_providing"
    probation = True
    prior = (0.55, 1.0)
    default_params = {"universe": "nifty50", "start": "10:15", "cutoff": "14:30", "band_k": 2.0,
                      "time_stop_min": 60, "rvol_max": 1.0, "lookback": 14, "vpin_pct": 0.8, "vpin_window": 50,
                      "buckets_per_day": 50, "max_positions": 3, "stop_ticks": 2,
                      # variance-ratio gate (off by default): fade only names whose 5-minute returns have
                      # mean-reverted over the last vr_days sessions (VR z* <= vr_z_max)
                      "reversion_filter": False, "vr_q": 6, "vr_days": 10, "vr_z_max": -1.0}

    def _keys(self, ctx):
        return ctx.universe(self.p["universe"])

    def data_needs(self, ctx):
        k = self._keys(ctx)
        return {"daily": k, "daily_days": 40, "minute": k, "minute_days": int(self.p["lookback"]) + 1}

    def live_keys(self, ctx):
        return list(ctx.state(self.name).get("prof", {}))

    def on_session_start(self, ctx):
        st = ctx.state(self.name)
        st.clear()
        st.update({"prof": {}, "thr": {}, "bucket": {}, "deadline": {}, "stops": {}})
        lb = int(self.p["lookback"])
        for key in self._keys(ctx):
            m, d = ctx.minute_history(key, lb), ctx.daily(key, tail=30)
            if m.empty or len(d) < 20:
                continue
            if self.p["reversion_filter"]:
                _, z = intraday_variance_ratio(ctx.minute_history(key, int(self.p["vr_days"])), int(self.p["vr_q"]))
                if not z <= float(self.p["vr_z_max"]):
                    continue
            bucket = float(d["volume"].iloc[-20:].mean()) / float(self.p["buckets_per_day"])
            v = vpin(m, bucket, int(self.p["vpin_window"]))
            st["prof"][key] = {str(k): float(x) for k, x in cum_volume_profile(m, lb).items()}
            st["bucket"][key] = bucket
            st["thr"][key] = float(v.quantile(self.p["vpin_pct"])) if len(v) > 20 else None

    def on_bar(self, ctx, bar):
        key, t_end = bar.key, bar.ts + timedelta(minutes=1)
        st = ctx.state(self.name)
        pos = ctx.position(self.name, key)
        if pos is not None:
            self._manage(ctx, key, pos, t_end)
            return
        if t_end.minute % 5 or not (hm(self.p["start"]) <= t_end.time() <= hm(self.p["cutoff"])):
            return
        if key not in st["prof"] or not ctx.posture(self.name)[0]:
            return
        if len(ctx.positions(self.name)) + len(ctx.active_orders(self.name, intent="entry")) >= int(self.p["max_positions"]):
            return
        five = ctx.bars5(key)            # incremental 5-minute bars carrying the VWAP band snapshot at each close
        if len(five) < 3 or len(ctx.e.today_bars.get(key, ())) < 30:
            return
        k = float(self.p["band_k"])
        prev, cur = five[-2], five[-1]
        if prev.close > prev.vwap + k * prev.sd and cur.close < cur.vwap + k * cur.sd:
            side = Side.SELL
        elif prev.close < prev.vwap - k * prev.sd and cur.close > cur.vwap - k * cur.sd:
            side = Side.BUY
        else:
            return
        exp = st["prof"][key].get(str(bar.ts.hour * 60 + bar.ts.minute))
        if not exp or ctx.day_volume(key) / exp >= float(self.p["rvol_max"]):
            return   # not a quiet day
        thr = st["thr"].get(key)
        if thr is not None:
            bars = ctx.bars_df(key)
            hist = pd.concat([ctx.minute_history(key, 1)[["close", "volume"]], bars[["close", "volume"]]])
            v = vpin(hist, st["bucket"][key], int(self.p["vpin_window"]))
            if len(v) and float(v.iloc[-1]) >= thr:
                return   # toxic flow: informed traders may be on the other side
        tick = self.tick(ctx, key)
        last = ctx.last(key)
        if side == Side.SELL:
            stop = max(prev.high, cur.high) + int(self.p["stop_ticks"]) * tick
        else:
            stop = min(prev.low, cur.low) - int(self.p["stop_ticks"]) * tick
        if self.enter(ctx, key, side, ctx.size(self.name, key, last, stop), product=Product.MIS, stop=stop, entry_ref=last):
            st["deadline"][key] = (t_end + timedelta(minutes=int(self.p["time_stop_min"]))).isoformat()

    def _manage(self, ctx, key, pos, t_end):
        vw, last = ctx.vwap(key)[0], ctx.last(key)
        dl = ctx.state(self.name)["deadline"].get(key)
        if vw and last and ((pos.qty > 0 and last >= vw) or (pos.qty < 0 and last <= vw)):
            self.exit(ctx, key, "target VWAP")
        elif dl and t_end >= datetime.fromisoformat(dl):
            self.exit(ctx, key, "time stop")
