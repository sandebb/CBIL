"""Broker adapter contract. The core never imports a broker SDK; it only talks to this interface.

Adapters must: log in (daily token cached at .cache/<broker>/session.json, mode 0600), build a
normalised instrument master, and translate quotes, history, orders and positions to canonical models.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

from collections import deque

import json
import logging
import os
import threading
import time as _t
import webbrowser
from abc import ABC, abstractmethod
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

from ..core.clock import IST, localize_ist
from ..core.models import (AuctionSnapshot, BrokerOrder, BrokerPosition, Instrument, OrderRequest,
                           Quote, Status)
from .symbols import COLS, SymbolMaster


class BrokerError(Exception):
    pass


class OrderUncertain(Exception):
    """The order may have reached the broker without being acknowledged. Deliberately not a BrokerError: the
    OMS marks the order UNKNOWN and confirms it from the order book by its tag, instead of calling it rejected."""


class AuthError(BrokerError):
    pass


class NotSupported(BrokerError):
    pass


def pick(d, *names, default=None):
    """First non-empty field among `names` from a dict or object (SDK field names drift between versions)."""
    if d is None:
        return default
    for n in names:
        v = d.get(n) if isinstance(d, dict) else getattr(d, n, None)
        if v not in (None, ""):
            return v
    return default


def fnum(x, default=None):
    try:
        v = float(x)
        return v if v == v else default
    except (TypeError, ValueError):
        return default


def inum(x, default=0) -> int:
    v = fnum(x, None)
    return int(v) if v is not None else default


def as_dict(o):
    if o is None:
        return {}
    if isinstance(o, dict):
        return o
    if hasattr(o, "to_dict"):
        return o.to_dict()
    return dict(vars(o)) if hasattr(o, "__dict__") else {}


def chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def now_ist() -> datetime:
    return datetime.now(IST)


class BrokerAdapter(ABC):
    name = "base"
    supports_streaming = False
    stream_capacity = 500          # instruments one user can stream at once (override in config: feeds.limits)
    quote_batch = 50
    min_intervals = {"quote": 1.0, "history": 0.35, "order": 0.12, "orders": 0.5}
    api_limits: tuple | None = None   # (calls per second, calls per window) across ALL endpoints, e.g. Fyers
    _window = 60.0                    # seconds of the second limit
    STATUS_MAP: dict = {}

    def __init__(self, cfg: dict | None = None, cache_dir: str | Path = ".cache", **_):
        self.cfg = dict(cfg or {})
        self.cache_dir = Path(cache_dir) / self.name
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = None
        self.master: SymbolMaster | None = None
        self._calls: deque = deque()           # every throttled call, for api_limits
        self._last_call: dict[str, float] = {}
        self._lock = threading.Lock()
        self._order_cache: dict[str, dict] = {}
        self.log = logging.getLogger(f"nsealgo.broker.{self.name}")

    # ---- required ---------------------------------------------------------------------------
    @abstractmethod
    def login(self, interactive: bool = True) -> None: ...

    @abstractmethod
    def _fetch_master(self) -> pd.DataFrame: ...

    @abstractmethod
    def get_quotes(self, keys: list[str]) -> list[Quote]: ...

    @abstractmethod
    def get_history(self, key: str, interval: str, start: date, end: date) -> pd.DataFrame: ...

    @abstractmethod
    def place_order(self, req: OrderRequest, client_id: str) -> str: ...

    @abstractmethod
    def modify_order(self, broker_id: str, *, price=None, trigger=None, qty=None, order_type=None) -> None: ...

    @abstractmethod
    def cancel_order(self, broker_id: str) -> None: ...

    @abstractmethod
    def get_orders(self) -> list[BrokerOrder]: ...

    @abstractmethod
    def get_positions(self) -> list[BrokerPosition]: ...

    # ---- optional ---------------------------------------------------------------------------
    def stream(self, keys: list[str], on_quote) -> None:
        raise NotSupported(f"{self.name} streaming not implemented; polling is used")

    def stop_stream(self) -> None:
        pass

    def auction_snapshot(self, key: str) -> AuctionSnapshot | None:
        """Closing-auction indicative data. NSE disseminates it; wire your broker's field here if exposed."""
        return None

    def get_funds(self) -> dict:
        return {}

    # ---- shared helpers ---------------------------------------------------------------------
    def load_instruments(self, force: bool = False) -> SymbolMaster:
        path = self.cache_dir / f"master_{date.today():%Y%m%d}.csv"
        if path.exists() and not force:
            df = pd.read_csv(path, dtype={"token": str, "broker_symbol": str, "expiry": str})
        else:
            df = self._fetch_master()
            df = df[[c for c in COLS if c in df.columns]]
            df.to_csv(path, index=False)
            for old in self.cache_dir.glob("master_*.csv"):
                if old != path:
                    old.unlink(missing_ok=True)
        self.master = SymbolMaster(df, self.name)
        return self.master

    def instrument(self, key: str) -> Instrument:
        if self.master is None:
            self.load_instruments()
        return self.master.get(key)

    def key_for(self, token=None, symbol=None) -> str | None:
        if self.master is None:
            return None
        return (self.master.key_for_token(token) if token is not None else None) or \
            (self.master.key_for_symbol(symbol) if symbol is not None else None)

    def map_status(self, raw) -> Status:
        return self.STATUS_MAP.get(str(raw).strip().upper(), Status.UNKNOWN)

    def throttle(self, kind: str) -> None:
        with self._lock:
            gap = self.min_intervals.get(kind, 0.0)
            wait = self._last_call.get(kind, 0.0) + gap - _t.monotonic()
            if wait > 0:
                _t.sleep(wait)
            self._budget()
            self._last_call[kind] = _t.monotonic()

    def _budget(self) -> None:
        """Hold the call until it fits the broker's overall limits (per second and per window, all endpoints)."""
        if not self.api_limits:
            return
        per_s, per_w = self.api_limits
        while True:
            now = _t.monotonic()
            while self._calls and now - self._calls[0] >= self._window:
                self._calls.popleft()
            wait = 0.0
            if per_w and len(self._calls) >= per_w:
                wait = self._window - (now - self._calls[0]) + 0.005
            recent = [x for x in self._calls if now - x < 1.0]
            if per_s and len(recent) >= per_s:
                wait = max(wait, 1.0 - (now - recent[0]) + 0.005)
            if wait <= 0:
                break
            _t.sleep(wait)
        self._calls.append(_t.monotonic())

    @staticmethod
    def exch(inst: Instrument) -> str:
        return "NSE"

    @staticmethod
    def ot_value(order_type) -> str:
        return getattr(order_type, "value", order_type)

    # ---- sessions ---------------------------------------------------------------------------
    def session_path(self) -> Path:
        return self.cache_dir / "session.json"

    def load_session(self) -> dict | None:
        p = self.session_path()
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None
        return d if d.get("date") == date.today().isoformat() else None

    def save_session(self, data: dict) -> None:
        p = self.session_path()
        p.write_text(json.dumps({**data, "date": date.today().isoformat()}), encoding="utf-8")
        os.chmod(p, 0o600)

    def oauth_capture(self, login_url: str, param: str, redirect_uri: str | None = None, timeout: int = 300) -> str:
        """Open the broker login page and capture `param` from the redirect (local server or paste)."""
        print(f"\nOpen this URL, log in and approve access:\n  {login_url}\n")
        try:
            webbrowser.open(login_url)
        except Exception:
            pass
        u = urlparse(redirect_uri or "")
        if u.hostname in ("127.0.0.1", "localhost") and u.port:
            box: dict = {}

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):  # noqa: N802
                    box.update({k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()})
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"Login captured. Close this tab and return to the terminal.")

                def log_message(self, *a):
                    pass

            try:
                srv = HTTPServer((u.hostname, u.port), Handler)
                srv.timeout = 1
                t0 = _t.time()
                while param not in box and _t.time() - t0 < timeout:
                    srv.handle_request()
                srv.server_close()
                if param in box:
                    return box[param]
            except OSError as ex:
                self.log.warning("redirect listener unavailable (%s); falling back to paste", ex)
        raw = input(f"Paste the full redirected URL (or just the {param}): ").strip()
        if raw.startswith("http"):
            return parse_qs(urlparse(raw).query).get(param, [""])[0]
        return raw

    # ---- data helpers -----------------------------------------------------------------------
    @staticmethod
    def candles_df(rows, interval: str) -> pd.DataFrame:
        """rows of (ts, open, high, low, close, volume) -> DataFrame indexed by IST timestamp (1m) or date (1d)."""
        df = pd.DataFrame(list(rows), columns=["ts", "open", "high", "low", "close", "volume"])
        if df.empty:
            return df.set_index("ts")
        ts = df["ts"]
        if pd.api.types.is_numeric_dtype(ts):
            unit = "ms" if float(ts.iloc[0]) > 1e11 else "s"
            t = pd.to_datetime(ts, unit=unit, utc=True).dt.tz_convert(IST)
        else:
            t = pd.to_datetime(ts, utc=False, errors="coerce", format="mixed")
            t = pd.Series(localize_ist(t), index=t.index) if t.dt.tz is None else t.dt.tz_convert(IST)
        df["ts"] = t
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["ts", "close"]).sort_values("ts").drop_duplicates("ts", keep="last")
        if interval == "1d":
            df["ts"] = pd.to_datetime(df["ts"].dt.date)
        return df.set_index("ts")


def date_spans(start: date, end: date, days: int):
    """Split [start, end] into consecutive spans of at most `days` calendar days (history API limits)."""
    from datetime import timedelta
    s = start
    while s <= end:
        e = min(end, s + timedelta(days=days - 1))
        yield s, e
        s = e + timedelta(days=1)


def quote_from_depth(key: str, ltp, volume, depth: dict | None, **extra) -> Quote:
    depth = depth or {}
    b = (depth.get("buy") or [{}])[0] or {}
    a = (depth.get("sell") or [{}])[0] or {}
    return Quote(key=key, ts=now_ist(), ltp=float(ltp), volume=fnum(volume, 0.0) or 0.0,
                 bid=fnum(pick(b, "price")) or None, ask=fnum(pick(a, "price")) or None,
                 bid_qty=fnum(pick(b, "quantity")), ask_qty=fnum(pick(a, "quantity")), **extra)


def noren_quote(key: str, d: dict, source: str = "") -> Quote:
    """Quote from a Noren-protocol feed snapshot (Shoonya, Flattrade, Zebu, Alice Blue): tk/tf/dk/df messages."""
    ft = fnum(d.get("ft"))
    return Quote(key=key, ts=now_ist(), ltp=float(d["lp"]), volume=fnum(d.get("v"), 0.0) or 0.0,
                 bid=fnum(d.get("bp1")) or None, ask=fnum(d.get("sp1")) or None, bid_qty=fnum(d.get("bq1")),
                 ask_qty=fnum(d.get("sq1")), oi=fnum(d.get("oi")), upper_circuit=fnum(d.get("uc")),
                 lower_circuit=fnum(d.get("lc")), open=fnum(d.get("o")), high=fnum(d.get("h")), low=fnum(d.get("l")),
                 prev_close=fnum(d.get("c")), exch_ts=datetime.fromtimestamp(ft, IST) if ft else None, source=source)
