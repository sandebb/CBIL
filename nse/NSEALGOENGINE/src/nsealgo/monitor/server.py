"""Serve EngineSnapshot views as JSON on 127.0.0.1 (never another interface), guarded by a random per-session token.
The address and token go to <run_dir>/monitor.json, which `nsealgo desk` reads. Read-only, except POST /api/kill,
which sets the same kill switch as `nsealgo kill`."""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .snapshot import AlertHandler, EngineSnapshot

log = logging.getLogger("nsealgo.monitor")
TOKEN_HEADER = "X-Nsealgo-Token"


class MonitorServer:
    def __init__(self, snapshot, port: int = 0, token: str | None = None):
        self.snap, self.token = snapshot, token or secrets.token_hex(16)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", int(port)), _handler(self))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = None

    def start(self) -> "MonitorServer":
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="monitor", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _handler(srv: MonitorServer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, code: int, obj) -> None:
            body = json.dumps(obj, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self) -> bool:
            if secrets.compare_digest(self.headers.get(TOKEN_HEADER, ""), srv.token):
                return True
            self._reply(403, {"error": "bad or missing token"})
            return False

        def do_GET(self):
            if not self._authorised():
                return
            u = urlsplit(self.path)
            try:
                if u.path == "/api/status":
                    return self._reply(200, srv.snap.status())
                if u.path == "/api/tiers":
                    return self._reply(200, srv.snap.tiers())
                if u.path == "/api/books":
                    keys = [k for k in (parse_qs(u.query).get("keys", [""])[0]).split(",") if k]
                    return self._reply(200, srv.snap.books(keys[:60]))
                if u.path == "/api/watch":
                    return self._reply(200, srv.snap.watch())
                if u.path == "/api/strategy":
                    return self._reply(200, srv.snap.strategy(parse_qs(u.query).get("name", [""])[0]))
                if u.path == "/api/series":
                    q = parse_qs(u.query)
                    return self._reply(200, srv.snap.series(q.get("key", [""])[0], q.get("kind", ["intraday"])[0]))
                self._reply(404, {"error": "not found"})
            except Exception as ex:
                log.warning("monitor %s failed: %s", u.path, ex)
                self._reply(500, {"error": str(ex)[:200]})

        def do_POST(self):
            if not self._authorised():
                return
            if urlsplit(self.path).path == "/api/kill":
                return self._reply(200, srv.snap.kill())
            self._reply(404, {"error": "not found"})

    return Handler


def start_monitor(engine, cfg: dict, run_dir) -> MonitorServer | None:
    """Start the desk's monitoring service for a live or forward session (config `monitor`)."""
    m = cfg.get("monitor") or {}
    if not m.get("enabled", True):
        return None
    try:
        srv = MonitorServer(EngineSnapshot(engine), port=int(m.get("port", 0))).start()
    except OSError as ex:
        log.warning("trading desk monitor not started: %s", ex)
        return None
    root = logging.getLogger("nsealgo")
    if not any(isinstance(h, AlertHandler) for h in root.handlers):
        h = AlertHandler(level=logging.WARNING)
        root.addHandler(h)
    info = run_dir / "monitor.json"
    info.write_text(json.dumps({"url": srv.url, "token": srv.token, "pid": os.getpid()}), encoding="utf-8")
    log.info("trading desk monitor on %s (nsealgo desk)", srv.url)
    return srv
