"""NSE transaction-cost model (STT, exchange, SEBI, stamp, GST, brokerage, DP) plus square-root impact.

All rates live in config so you can update them when the Budget or your broker changes them.
"""
from __future__ import annotations

import math


def _v(x) -> str:
    return getattr(x, "value", x)


class CostModel:
    def __init__(self, cfg: dict):
        self.c = cfg
        self.b = cfg.get("brokerage", {})

    def brokerage(self, product: str, kind: str, value: float) -> float:
        b = self.b
        if product == "CNC":
            flat, pct = b.get("delivery_flat", 0.0), b.get("delivery_pct", 0.0)
            if flat and pct:
                return min(flat, pct * value)
            return flat or pct * value
        return min(b.get("intraday_flat", 20.0), b.get("intraday_pct", 0.0003) * value)

    def fill_costs(self, *, product, side, price: float, qty: int, kind: str = "EQ", key: str | None = None) -> float:
        return self.breakdown(product=product, side=side, price=price, qty=qty, kind=kind, key=key)["total"]

    def breakdown(self, *, product, side, price: float, qty: int, kind: str = "EQ", key: str | None = None) -> dict:
        """Every statutory and broker charge of one fill, itemised (rupees)."""
        c, product, buy = self.c, _v(product), _v(side) == "BUY"
        v = abs(price * qty)
        brk = self.brokerage(product, kind, v)
        if product == "CNC":
            stt = c["stt_delivery"] * v
            exch, stamp = c["exch_eq"] * v, (c["stamp_delivery_buy"] * v if buy else 0.0)
        else:
            stt = 0.0 if buy else c["stt_intraday_sell"] * v
            exch, stamp = c["exch_eq"] * v, (c["stamp_intraday_buy"] * v if buy else 0.0)
        sebi = c["sebi"] * v
        gst = c["gst"] * (brk + exch + sebi)
        dp = c.get("dp_charge", 0.0) if (product == "CNC" and not buy) else 0.0
        return {"brokerage": brk, "stt": stt, "exchange": exch, "stamp": stamp, "sebi": sebi, "gst": gst, "dp": dp,
                "total": brk + stt + exch + stamp + sebi + gst + dp}

    def round_trip_fraction(self, product, price: float, qty: int, kind: str = "EQ") -> float:
        tot = self.fill_costs(product=product, side="BUY", price=price, qty=qty, kind=kind) + \
            self.fill_costs(product=product, side="SELL", price=price, qty=qty, kind=kind)
        return tot / max(price * qty, 1e-9)

    def impact_fraction(self, sigma_daily: float | None, order_value: float, adv_value: float | None) -> float:
        """Half-spread + square-root law: Y * sigma_daily * sqrt(order / daily volume)."""
        hs = self.c.get("half_spread_bps", 1.0) / 1e4
        if not adv_value or adv_value <= 0 or not sigma_daily:
            return hs
        return hs + self.c.get("impact_y", 0.7) * sigma_daily * math.sqrt(order_value / adv_value)
