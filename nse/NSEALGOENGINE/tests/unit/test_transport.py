"""Broker transport against a real local HTTP server and a real local forwarding proxy."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
import http.client
import http.server
import socketserver
import sys
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests

from nsealgo.brokers.base import BrokerError
from nsealgo.brokers.transport import BrokerTransport, SdkRequests, route_sdk


class Srv(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


class Target(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, body=b"ok", code=200):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.server.ports.add(self.client_address[1])
        self._send(b"", 404)

    def do_GET(self):
        self.server.ports.add(self.client_address[1])
        self._send(self.server.ip.encode() if self.path.endswith("/ip") else b"ok")

    def do_POST(self):
        self.server.ports.add(self.client_address[1])
        self.server.posts += 1
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path.endswith("/drop"):
            self.close_connection = True                 # hang up without answering
            return
        self._send(b'{"stat":"Ok"}')


class Proxy(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _relay(self):
        self.server.hits += 1
        u = urlsplit(self.path)                          # proxies receive absolute URLs
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))) if self.command == "POST" else None
        c = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
        c.request(self.command, u.path or "/", body=body)
        r = c.getresponse()
        data = r.read()
        self.send_response(r.status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)
        c.close()

    do_GET = do_POST = do_HEAD = _relay


def _start(handler, **attrs):
    s = Srv(("127.0.0.1", 0), handler)
    for k, v in attrs.items():
        setattr(s, k, v)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


@pytest.fixture()
def target():
    s = _start(Target, ports=set(), posts=0, ip="203.0.113.7")
    yield s, f"http://127.0.0.1:{s.server_address[1]}/NorenWClientTP/"
    s.shutdown()


def test_orders_reuse_one_warm_connection(target):
    srv, base = target
    t = BrokerTransport("shoonya", base, keepalive_seconds=0)
    assert t.warm() is not None
    for _ in range(5):
        assert t.post(base + "PlaceOrder", data={"jData": "{}"}).status_code == 200
    assert len(srv.ports) == 1                           # one TCP connection for warm-up + 5 orders
    assert t.stats()["PlaceOrder"]["n"] == 5


def test_requests_really_go_through_the_proxy(target):
    srv, base = target
    px = _start(Proxy, hits=0)
    t = BrokerTransport("shoonya", base, proxy=f"http://127.0.0.1:{px.server_address[1]}", static_ip="203.0.113.7",
                        egress_url=base + "ip", keepalive_seconds=0)
    assert t.verify() == "203.0.113.7"                   # the static-IP check itself runs through the proxy
    t.post(base + "PlaceOrder", data={"jData": "{}"})
    assert px.hits == 2
    px.shutdown()


def test_static_ip_check_fails_closed(target):
    srv, base = target
    t = BrokerTransport("shoonya", base, static_ip="203.0.113.7", egress_url=base + "ip", keepalive_seconds=0)
    assert t.verify() == "203.0.113.7"
    srv.ip = "198.51.100.9"                              # proxy misconfigured: traffic leaves from another IP
    with pytest.raises(BrokerError, match="not your registered static IP"):
        t.verify()
    dead = BrokerTransport("shoonya", base, static_ip="203.0.113.7", egress_url="http://127.0.0.1:9/ip",
                           connect_timeout=0.5, keepalive_seconds=0)
    with pytest.raises(BrokerError, match="can't confirm"):
        dead.verify()
    assert BrokerTransport("shoonya", base, keepalive_seconds=0).verify() is None   # no static_ip: no check


def test_orders_are_never_retried(target):
    srv, base = target
    t = BrokerTransport("shoonya", base, keepalive_seconds=0)
    with pytest.raises(requests.ConnectionError):
        t.post(base + "drop", data={"jData": "{}"})
    assert srv.posts == 1                                # a retried order could be a duplicate


def test_routing_is_explicit_and_per_broker():
    t = BrokerTransport("shoonya", "https://api.shoonya.com/NorenWClientTP/", proxy="http://u:p@proxy.example:3128")
    assert t.session.proxies == {"http": "http://u:p@proxy.example:3128", "https": "http://u:p@proxy.example:3128"}
    assert t.session.trust_env is False                  # system proxy variables can't hijack order traffic
    assert t.session.get_adapter("https://api.shoonya.com/").max_retries.total == 0


def test_sdk_router_sends_only_broker_calls_through_the_transport(target, monkeypatch):
    srv, base = target
    t = BrokerTransport("shoonya", base, keepalive_seconds=0)
    mod = SimpleNamespace(requests=requests)
    r = route_sdk(mod, t)
    assert isinstance(mod.requests, SdkRequests) and route_sdk(mod, t) is r
    assert mod.requests.post(base + "PlaceOrder", data="jData={}").status_code == 200
    assert t.stats()["PlaceOrder"]["n"] == 1
    seen = {}
    monkeypatch.setattr(requests, "post", lambda url, data=None, **kw: seen.update(url=url, timeout=kw.get("timeout")))
    mod.requests.post("https://elsewhere.example/x", data="a")
    assert seen["url"] == "https://elsewhere.example/x" and seen["timeout"] is not None
    assert mod.requests.exceptions is requests.exceptions


def test_shoonya_login_refuses_the_wrong_ip_and_routes_the_sdk(target, tmp_path):
    from nsealgo.brokers.noren import NorenAdapter
    import NorenRestApiPy.NorenApi as sdk
    srv, base = target
    cfg = {"host": base, "user_id": "U1", "password": "x", "session_token": "tok", "static_ip": "203.0.113.7",
           "egress_url": base + "ip", "transport": {"keepalive_seconds": 0}}
    real = sdk.requests
    try:
        srv.ip = "198.51.100.9"
        with pytest.raises(BrokerError, match="not your registered static IP"):
            NorenAdapter(dict(cfg), tmp_path).login(interactive=False)
        srv.ip = "203.0.113.7"
        a = NorenAdapter(dict(cfg), tmp_path)
        a.login(interactive=False)
        assert isinstance(sdk.requests, SdkRequests) and a.transport.host in sdk.requests.routes
    finally:
        sdk.requests = real
    assert "NorenRestApiPy.NorenApi" in sys.modules
