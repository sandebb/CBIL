"""Canonical, broker-independent data model. Every adapter translates to and from these types.

Instrument keys are plain strings so they hash, log and persist cleanly:
  NSE:INFY                    cash equity / ETF
  IDX:NIFTY500                index (normalised alphanumeric name)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    SL = "SL"      # stop-limit
    SLM = "SL-M"   # stop-market (brokers convert these to price-protected orders)


class Product(str, Enum):
    MIS = "MIS"    # intraday
    CNC = "CNC"    # delivery


class Status(str, Enum):
    NEW = "NEW"
    OPEN = "OPEN"
    TRIGGER_PENDING = "TRIGGER_PENDING"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


TERMINAL = {Status.FILLED, Status.CANCELLED, Status.REJECTED}

INDEX_ALIASES = {
    "NIFTY": "NIFTY50", "NIFTY50": "NIFTY50", "CNXNIFTY": "NIFTY50", "NIFTY500": "NIFTY500", "CNX500": "NIFTY500",
    "INDIAVIX": "INDIAVIX", "VIX": "INDIAVIX", "BANKNIFTY": "NIFTYBANK", "NIFTYBANK": "NIFTYBANK",
    "NIFTYNEXT50": "NIFTYNEXT50", "NIFTYJR": "NIFTYNEXT50",
}


def norm_index(name: str) -> str:
    s = re.sub(r"[^A-Z0-9]", "", str(name).upper().replace("INDEX", ""))
    return INDEX_ALIASES.get(s, s)


def eq(symbol: str) -> str:
    return f"NSE:{str(symbol).strip().upper()}"


def idx(name: str) -> str:
    return f"IDX:{norm_index(name)}"


def parse_key(key: str) -> dict:
    parts = key.split(":")
    if parts[0] == "IDX":
        return {"exchange": "NSE", "symbol": parts[1], "kind": "IDX", "expiry": None}
    return {"exchange": parts[0], "symbol": parts[-1], "kind": "EQ", "expiry": None}


@dataclass
class Instrument:
    key: str
    exchange: str
    kind: str                 # EQ | IDX (index: data only, never traded)
    symbol: str               # NSE symbol, or the underlying for futures
    broker_symbol: str = ""
    token: str = ""
    lot_size: int = 1
    tick_size: float = 0.05
    expiry: str | None = None
    name: str = ""


def default_instrument(key: str) -> Instrument:
    p = parse_key(key)
    return Instrument(key=key, exchange=p["exchange"], kind=p["kind"], symbol=p["symbol"],
                      broker_symbol=p["symbol"], expiry=p["expiry"])


@dataclass
class Quote:
    key: str
    ts: datetime
    ltp: float
    volume: float = 0.0            # cumulative day volume
    bid: float | None = None
    ask: float | None = None
    bid_qty: float | None = None
    ask_qty: float | None = None
    oi: float | None = None
    upper_circuit: float | None = None
    lower_circuit: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    prev_close: float | None = None
    exch_ts: datetime | None = None    # exchange timestamp when the feed provides one
    source: str = ""                   # which broker feed delivered it
    bids: tuple = ()                   # market depth, best first: ((price, qty), ...) - 5 or 30 levels (Upstox)
    asks: tuple = ()


QUOTE_FIELDS = ("key", "ts", "ltp", "volume", "bid", "ask", "bid_qty", "ask_qty", "oi", "upper_circuit",
                "lower_circuit", "open", "high", "low", "prev_close", "exch_ts", "source", "bids", "asks")


def quote_to_dict(q: "Quote", depth_levels: int | None = None) -> dict:
    """JSON-ready dict of a quote; keep at most `depth_levels` depth levels (None = all, 0 = none)."""
    d = {f: getattr(q, f) for f in QUOTE_FIELDS}
    d["ts"], d["exch_ts"] = q.ts.isoformat(), (q.exch_ts.isoformat() if q.exch_ts else None)
    n = len(q.bids) if depth_levels is None else int(depth_levels)
    d["bids"], d["asks"] = [list(x) for x in q.bids[:n]], [list(x) for x in q.asks[:n]]
    return d


def quote_from_dict(d: dict) -> "Quote":
    d = {k: v for k, v in d.items() if k in QUOTE_FIELDS}
    d["ts"] = datetime.fromisoformat(d["ts"])
    if d.get("exch_ts"):
        d["exch_ts"] = datetime.fromisoformat(d["exch_ts"])
    d["bids"] = tuple(tuple(x) for x in d.get("bids") or ())
    d["asks"] = tuple(tuple(x) for x in d.get("asks") or ())
    return Quote(**d)


@dataclass
class Bar:
    key: str
    ts: datetime                   # bar START time (IST)
    open: float
    high: float
    low: float
    close: float
    volume: float
    ofi: float = 0.0
    interval: str = "1m"           # 1m | 1d


@dataclass
class OrderRequest:
    key: str
    side: Side
    qty: int
    order_type: OrderType = OrderType.LIMIT
    product: Product = Product.MIS
    price: float | None = None
    trigger: float | None = None
    validity: str = "DAY"
    strategy: str = ""
    intent: str = "entry"          # entry | exit | stop
    at_close: bool = False         # paper semantics: execute at the closing-auction price
    meta: dict = field(default_factory=dict)


@dataclass
class Order:
    client_id: str
    req: OrderRequest
    broker_id: str | None = None
    status: Status = Status.NEW
    filled_qty: int = 0
    avg_price: float = 0.0
    message: str = ""
    created: datetime | None = None
    updated: datetime | None = None

    @property
    def open_qty(self) -> int:
        return self.req.qty - self.filled_qty

    @property
    def is_active(self) -> bool:
        return self.status not in TERMINAL


@dataclass
class BrokerOrder:
    broker_id: str
    status: Status
    filled_qty: int
    avg_price: float
    key: str = ""
    side: Side | None = None
    qty: int = 0
    tag: str = ""
    message: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class BrokerPosition:
    key: str
    qty: int
    avg_price: float
    product: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class Fill:
    client_id: str
    key: str
    side: Side
    qty: int
    price: float
    ts: datetime
    strategy: str = ""
    product: Product = Product.MIS
    costs: float = 0.0
    intent: str = ""


@dataclass
class AuctionSnapshot:
    key: str
    ts: datetime
    indicative_price: float
    indicative_qty: float = 0.0
    total_buy: float = 0.0
    total_sell: float = 0.0
    imbalance_qty: float = 0.0
    imbalance_side: str | None = None   # BUY | SELL
