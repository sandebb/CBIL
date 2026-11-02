"""Low-latency HTTP transport for broker REST APIs, with an optional static-IP proxy.

- Pooled keep-alive connections: DNS, TCP, TLS (and the proxy tunnel) are set up once; every later order
  goes down the already-open, already-encrypted connection instead of paying several round trips first.
  `warm()` opens it before the first order, and a background ping keeps it from idling out.
- TCP_NODELAY (no Nagle delay on small order packets) and SO_KEEPALIVE on every socket, proxied or not.
- Static IP: SEBI requires API orders to come from a static IP registered with the broker. With `proxy`,
  this broker's REST calls leave through your dedicated proxy IP (e.g. StaticIP.in); market data does not.
  `verify()` asks an IP-echo service through the same proxy and refuses to trade if the address isn't your
  registered `static_ip` (fail closed: the broker would reject those orders anyway).
- Every call has a timeout (the Noren SDK sets none), and nothing is retried automatically: a retried
  order could be a duplicate.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import logging
import socket
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

import numpy as np
import requests
from requests.adapters import HTTPAdapter

from .base import BrokerError

log = logging.getLogger("nsealgo.transport")
SOCKET_OPTIONS = [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1), (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]


class TunedAdapter(HTTPAdapter):
    """Keep-alive pool whose sockets get TCP_NODELAY + SO_KEEPALIVE, including sockets opened via a proxy."""

    def init_poolmanager(self, *args, **kw):
        kw["socket_options"] = SOCKET_OPTIONS
        super().init_poolmanager(*args, **kw)

    def proxy_manager_for(self, proxy, **kw):
        kw.setdefault("socket_options", SOCKET_OPTIONS)
        return super().proxy_manager_for(proxy, **kw)


class BrokerTransport:
    def __init__(self, name: str, base_url: str, *, proxy: str | None = None, static_ip: str | None = None,
                 verify_egress: bool = True, egress_url: str = "https://api.ipify.org",
                 connect_timeout: float = 3.05, read_timeout: float = 10.0, pool: int = 4,
                 keepalive_seconds: float = 45.0):
        self.name, self.base_url, self.host = name, base_url, urlsplit(base_url).netloc
        self.proxy = (proxy or "").strip() or None
        self.static_ip = (static_ip or "").strip() or None
        self.verify_egress, self.egress_url = bool(verify_egress), egress_url
        self.timeout = (float(connect_timeout), float(read_timeout))
        self.keepalive_seconds = float(keepalive_seconds or 0)
        s = requests.Session()
        s.trust_env = False                       # ignore HTTP(S)_PROXY env vars: routing is explicit, per broker
        ad = TunedAdapter(pool_connections=1, pool_maxsize=max(int(pool), 1), max_retries=0)
        s.mount("https://", ad)
        s.mount("http://", ad)
        if self.proxy:
            s.proxies = {"http": self.proxy, "https": self.proxy}
        self.session = s
        self.egress: str | None = None
        self.last_error: str | None = None
        self.lat: dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self._stop, self._ka = threading.Event(), None

    @classmethod
    def from_config(cls, name: str, base_url: str, cfg: dict) -> "BrokerTransport":
        t = cfg.get("transport") or {}
        return cls(name, base_url, proxy=cfg.get("proxy"), static_ip=cfg.get("static_ip"),
                   verify_egress=cfg.get("verify_egress", True),
                   egress_url=cfg.get("egress_url", "https://api.ipify.org"),
                   connect_timeout=t.get("connect_timeout", 3.05), read_timeout=t.get("read_timeout", 10.0),
                   pool=t.get("pool", 4), keepalive_seconds=t.get("keepalive_seconds", 45))

    # ---- requests ------------------------------------------------------------------------------
    def request(self, method: str, url: str, **kw):
        kw.setdefault("timeout", self.timeout)
        t0 = time.perf_counter()
        try:
            return self.session.request(method, url, **kw)
        except Exception as ex:
            self.last_error = f"{type(ex).__name__}: {ex}"
            raise
        finally:
            ep = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1] or "/"
            self.lat[ep].append((time.perf_counter() - t0) * 1000.0)

    def post(self, url: str, data=None, **kw):
        return self.request("POST", url, data=data, **kw)

    def get(self, url: str, **kw):
        return self.request("GET", url, **kw)

    def as_session(self) -> "SessionShim":
        """For SDKs that keep their own requests.Session (e.g. Fyers): put this in its place."""
        return SessionShim(self)

    def stats(self) -> dict:
        """Round-trip latency per endpoint (ms): what an order actually costs on your line."""
        out = {}
        for ep, d in self.lat.items():
            a = np.fromiter(d, float)
            if len(a):
                out[ep] = {"n": int(len(a)), "p50_ms": round(float(np.percentile(a, 50)), 1),
                           "p95_ms": round(float(np.percentile(a, 95)), 1), "max_ms": round(float(a.max()), 1)}
        return out

    # ---- static IP -----------------------------------------------------------------------------
    def egress_ip(self) -> str:
        r = self.session.get(self.egress_url, timeout=self.timeout)
        r.raise_for_status()
        return r.text.strip()

    def verify(self) -> str | None:
        """With a static_ip configured, refuse to trade unless requests really leave from it."""
        if not (self.static_ip and self.verify_egress):
            return None
        via = f"proxy {urlsplit(self.proxy).hostname}" if self.proxy else "this network"
        try:
            ip = self.egress_ip()
        except Exception as ex:
            raise BrokerError(f"{self.name}: can't confirm the static IP through {via} ({ex}); not trading") from ex
        self.egress = ip
        if ip != self.static_ip:
            raise BrokerError(f"{self.name}: requests leave from {ip}, not your registered static IP "
                              f"{self.static_ip}; not trading (check the proxy settings)")
        log.info("%s: orders leave from registered static IP %s via %s", self.name, ip, via)
        return ip

    # ---- connection warmth ---------------------------------------------------------------------
    def warm(self) -> float | None:
        """Open (or refresh) the pooled connection now rather than on the first order; returns ms."""
        t0 = time.perf_counter()
        try:
            self.session.head(self.base_url, timeout=self.timeout, allow_redirects=False)
        except requests.RequestException as ex:
            log.warning("%s: connection warm-up failed: %s", self.name, ex)
            return None
        return (time.perf_counter() - t0) * 1000.0

    def start_keepalive(self) -> None:
        if self.keepalive_seconds <= 0 or (self._ka is not None and self._ka.is_alive()):
            return

        def loop():
            while not self._stop.wait(self.keepalive_seconds):
                self.warm()

        self._ka = threading.Thread(target=loop, name=f"{self.name}-keepalive", daemon=True)
        self._ka.start()

    def close(self) -> None:
        self._stop.set()
        self.session.close()


class SessionShim:
    """requests.Session-shaped front for a BrokerTransport: same calls, now pooled, proxied and timed out."""

    def __init__(self, transport: "BrokerTransport"):
        self.transport = transport

    def request(self, method, url, **kw):
        return self.transport.request(str(method).upper(), url, **kw)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, data=None, **kw):
        return self.request("POST", url, data=data, **kw)

    def put(self, url, data=None, **kw):
        return self.request("PUT", url, data=data, **kw)

    def patch(self, url, data=None, **kw):
        return self.request("PATCH", url, data=data, **kw)

    def delete(self, url, **kw):
        return self.request("DELETE", url, **kw)

    def close(self):
        pass


class SdkRequests:
    """Stands in for the `requests` module inside a broker SDK: calls to a host with a registered transport
    go through it (pooled, proxied, timed out); anything else gets plain `requests` with a timeout."""
    default_timeout = (3.05, 15.0)

    def __init__(self):
        self.routes: dict[str, BrokerTransport] = {}

    def register(self, t: BrokerTransport) -> None:
        self.routes[t.host] = t

    def _route(self, url: str):
        return self.routes.get(urlsplit(url).netloc)

    def post(self, url, data=None, **kw):
        t = self._route(url)
        if t is not None:
            return t.post(url, data=data, **kw)
        kw.setdefault("timeout", self.default_timeout)
        return requests.post(url, data=data, **kw)

    def get(self, url, **kw):
        t = self._route(url)
        if t is not None:
            return t.get(url, **kw)
        kw.setdefault("timeout", self.default_timeout)
        return requests.get(url, **kw)

    def __getattr__(self, name):                    # exceptions, codes, ... keep working inside the SDK
        return getattr(requests, name)


def route_sdk(module, transport: BrokerTransport) -> SdkRequests:
    """Make `module.requests.*` calls to transport.host go through `transport`."""
    cur = getattr(module, "requests", None)
    if not isinstance(cur, SdkRequests):
        cur = SdkRequests()
        module.requests = cur
    cur.register(transport)
    return cur
