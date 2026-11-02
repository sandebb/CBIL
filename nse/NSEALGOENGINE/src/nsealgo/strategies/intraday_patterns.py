"""Documented intraday price patterns (MIS, flat by the intraday exit).

- compression_breakout: trade a break of yesterday's range after a volatility contraction.
  * setups: NR7 (narrowest range of 7 days), inside day, or NR4 plus inside day (Crabel, 'Day Trading with
    Short Term Price Patterns and Opening Range Breakout', 1990); or a narrow Central Pivot Range, meaning
    yesterday's CPR width is in the narrowest 20% of the last 20 days (Ochoa, 'Secrets of a Pivot Boss',
    2010; widely taught in Indian forums).
  * entry: a 1-minute close beyond yesterday's high (long) or low (short) between `start` and `cutoff`.
  * stop: the middle of yesterday's range, or the opposite side (stop_mode).
  * Crabel's original entries were stop orders at a fixed stretch from the open; bar-close confirmation
    is used here so paper and live fills agree.
- gap_fade: fade small opening gaps in liquid stocks back toward the previous close, a retail staple with
  mixed published evidence.
  * entry: at 09:30, if the gap is between min_gap and max_gap and not already filled, trade toward the
    previous close.
  * stop: beyond the first 15 minutes' extreme.
  * target: the previous close; time exit at 13:00. Skipped on results days.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

from datetime import timedelta

import numpy as np

from ..core.clock import hm
from ..core.indicators import cpr_width
from ..core.models import Product, Side
from .base import Strategy


def compression_setup(d, setup: str, cpr_pct: float = 0.2) -> bool:
    """Is YESTERDAY (the last row of d) a compression day of the given kind?"""
    if len(d) < 22:
        return False
    rng = (d["high"] - d["low"]).to_numpy(float)
    inside = bool(d["high"].iloc[-1] <= d["high"].iloc[-2] and d["low"].iloc[-1] >= d["low"].iloc[-2])
    if setup == "nr7":
        return bool(rng[-1] <= rng[-7:].min())
    if setup == "inside":
        return inside
    if setup == "nr4_inside":
        return inside and bool(rng[-1] <= rng[-4:].min())
    if setup == "narrow_cpr":
        w = cpr_width(d).to_numpy(float)[-20:]
        return bool(w[-1] <= np.quantile(w, cpr_pct))
    raise ValueError(f"unknown setup {setup}")


class CompressionBreakout(Strategy):
    name = "compression_breakout"
    horizon = "intraday"
    style = "liquidity_taking"
    prior = (0.42, 1.6)
    enabled_by_default = False
    default_params = {"universe": "fno", "setup": "nr7", "cpr_pct": 0.2, "top_n": 10, "start": "09:20",
                      "cutoff": "13:30", "max_positions": 4, "stop_mode": "mid", "min_adv": 1e8}

    def data_needs(self, ctx):
        return {"daily": ctx.universe(self.p["universe"]), "daily_days": 60}

    def on_session_start(self, ctx):
        picks = {}
        for key in ctx.universe(self.p["universe"]):
            d = ctx.daily(key, tail=40)
            if len(d) < 22 or float((d["close"] * d["volume"]).iloc[-20:].mean()) < float(self.p["min_adv"]):
                continue
            if compression_setup(d, self.p["setup"], float(self.p["cpr_pct"])):
                r = d.iloc[-1]
                picks[key] = {"hi": float(r["high"]), "lo": float(r["low"]), "done": False,
                              "adv": float((d["close"] * d["volume"]).iloc[-20:].mean())}
        top = sorted(picks, key=lambda k: -picks[k]["adv"])[: int(self.p["top_n"])]
        ctx.state(self.name)["picks"] = {k: picks[k] for k in top}

    def live_keys(self, ctx):
        return list(ctx.state(self.name).get("picks", {}))

    def on_bar(self, ctx, bar):
        pk = ctx.state(self.name).get("picks", {}).get(bar.key)
        t = (bar.ts + timedelta(minutes=1)).time()
        if not pk or pk["done"] or not (hm(self.p["start"]) <= t <= hm(self.p["cutoff"])):
            return
        if ctx.position(self.name, bar.key) or ctx.active_orders(self.name, bar.key):
            return
        if len(ctx.positions(self.name)) >= int(self.p["max_positions"]) or not ctx.posture(self.name)[0]:
            return
        mid = (pk["hi"] + pk["lo"]) / 2
        if bar.close > pk["hi"]:
            side, stop = Side.BUY, (mid if self.p["stop_mode"] == "mid" else pk["lo"])
        elif bar.close < pk["lo"]:
            side, stop = Side.SELL, (mid if self.p["stop_mode"] == "mid" else pk["hi"])
        else:
            return
        pk["done"] = True                                        # one attempt per name per day
        px = float(bar.close)
        self.enter(ctx, bar.key, side, ctx.size(self.name, bar.key, px, stop), product=Product.MIS, stop=stop,
                   entry_ref=px, meta={"setup": self.p["setup"]})


class GapFade(Strategy):
    name = "gap_fade"
    horizon = "intraday"
    style = "liquidity_providing"
    prior = (0.55, 0.9)
    enabled_by_default = False
    default_params = {"universe": "nifty50", "symbols": [], "min_gap": 0.003, "max_gap": 0.012,
                      "entry_time": "09:30", "exit_time": "13:00", "stop_buffer": 0.001}

    def _keys(self, ctx):
        return ctx.universe(list(self.p["symbols"]) if self.p.get("symbols") else self.p["universe"])

    def data_needs(self, ctx):
        return {"daily": self._keys(ctx), "daily_days": 30}

    def live_keys(self, ctx):
        return self._keys(ctx)

    def timers(self):
        return [self.p["entry_time"], self.p["exit_time"]]

    def on_timer(self, ctx, t):
        st = ctx.state(self.name)
        if t == self.p["exit_time"]:
            for p in list(ctx.positions(self.name)):
                self.exit(ctx, p.key, "time exit")
            return
        if t != self.p["entry_time"] or not ctx.posture(self.name)[0]:
            return
        st["targets"] = {}
        for key in self._keys(ctx):
            if ctx.events.had_results_within(key.split(":")[-1], ctx.today, 0):
                continue
            bars, d = ctx.bars_df(key), ctx.daily(key, tail=5)
            if len(bars) < 10 or not len(d):
                continue
            prev, opn, last = float(d["close"].iloc[-1]), float(bars["open"].iloc[0]), float(bars["close"].iloc[-1])
            gap = opn / prev - 1
            if not float(self.p["min_gap"]) <= abs(gap) <= float(self.p["max_gap"]):
                continue
            buf = float(self.p["stop_buffer"])
            if gap > 0 and last > prev:
                side, stop = Side.SELL, float(bars["high"].max()) * (1 + buf)
            elif gap < 0 and last < prev:
                side, stop = Side.BUY, float(bars["low"].min()) * (1 - buf)
            else:
                continue                                         # already filled
            if self.enter(ctx, key, side, ctx.size(self.name, key, last, stop), product=Product.MIS, stop=stop,
                          entry_ref=last, meta={"gap": round(gap, 4)}):
                st["targets"][key] = prev

    def on_bar(self, ctx, bar):
        tgt = ctx.state(self.name).get("targets", {}).get(bar.key)
        pos = ctx.position(self.name, bar.key)
        if tgt is None or pos is None:
            return
        if (pos.qty < 0 and bar.low <= tgt) or (pos.qty > 0 and bar.high >= tgt):
            self.exit(ctx, bar.key, "gap filled")
