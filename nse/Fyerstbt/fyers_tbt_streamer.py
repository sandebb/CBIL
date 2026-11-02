#!/usr/bin/env python3
r"""
Fyers TBT recorder: 50-level market depth for 15 NSE stocks over 3 websockets (5 stocks each),
one Parquet file per minute in ${NSE_ROOT}/code\Fyerstbt\<yyyymmdd>\.

    python ${NSE_ROOT}/code\Fyerstbt\fyers_tbt_streamer.py              stream; stops by itself at 15:31 IST
    python ${NSE_ROOT}/code\Fyerstbt\fyers_tbt_streamer.py --dry-run    show symbols, token and socket only
    python ${NSE_ROOT}/code\Fyerstbt\fyers_tbt_streamer.py --symbols SBIN TCS INFY ...   your own list

Symbols   the 15 best signals in NSEDATAENGINE's scan for today, signals_for_<yyyymmdd>.csv (written by last
          evening's run): regime-allowed first, then highest score, then most liquid (turnover rank).
          The ticker of each comes from the Fyers symbol master NSEUIENGINE keeps (state\NSE_CM.csv).
Token     NSEUIENGINE's fyers_token.py: the token the 08:00 NSEUIENGINE-FyersToken task issues, read from the
          registry. This script never asks for a token and never saves one.
Protocol  as in Fyers' official SDK (fyers-apiv3 3.1.18, FyersWebsocket/tbt_ws.py): socket URL from
          /indus/home/tbtws, handshake header authorization: APPID:TOKEN, subscribe {"type": 1, ... "mode": "depth"},
          resume {"type": 2, ...}, the text "ping" every 10 s, protobuf replies = snapshot + positional diffs.
          The SDK's protobuf schema is embedded below, so only `protobuf` is needed - not the SDK, whose pinned
          requirements (requests 2.31, aiohttp 3.9.3, setuptools 68) would downgrade or fail on Python 3.14.

One row = one book update of one symbol, holding the whole book after that update:
    ts          receive time (IST)                  symbol    NSE:XXX-EQ          conn  websocket 0-2
    seq         Fyers sequence number               snapshot  True for a full-book message
    exch_ts     Fyers feed time (IST)               send_ts   Fyers send time (IST)
    feed_time   raw feed_time from Fyers            send_time raw send_time from Fyers
    tbq, tsq    total buy / sell quantity
    bid_px, bid_qty, bid_ord, ask_px, ask_qty, ask_ord    lists, level 0 = best price, up to 50 levels

    import pandas as pd
    day = pd.read_parquet(__import__("os").environ.get("NSE_ROOT", r"D:\CBIL") + r"/code\Fyerstbt\20261006")       # every file of a day
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import base64
import csv
import importlib
import importlib.util
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "1.0.0"
IST = timezone(timedelta(hours=5, minutes=30))
TZ = "Asia/Kolkata"
LEVELS = 50
CHANNEL = "1"
URL_LOOKUP = "https://api-t1.fyers.in/indus/home/tbtws"
URL_FALLBACK = "wss://rtsocket-api.fyers.in/versova"
PROFILE_URL = "https://api-t1.fyers.in/api/v3/profile"
TOKEN_TASK = "NSEUIENGINE-FyersToken"
DEFAULT_OUT = str((Path(os.environ.get("NSE_ROOT") or Path(__file__).resolve().parents[2]) / "data" / "Fyerstbt").resolve())
MAX_ROWS_PER_FILE = 250_000          # a minute with more updates than this is written in several files
MINUTE_NS = 60_000_000_000

log = logging.getLogger("fyers_tbt")

# Fyers' msg.proto as shipped in fyers-apiv3 3.1.18 (FyersWebsocket/msg_pb2.py): serialized FileDescriptorProto.
_MSG_PROTO_B64 = (
    "Cgltc2cucHJvdG8aHmdvb2dsZS9wcm90b2J1Zi93cmFwcGVycy5wcm90byK7AQoLTWFya2V0TGV2ZWwSKgoFcHJpY2UYASABKAsyGy5nb29n"
    "bGUucHJvdG9idWYuSW50NjRWYWx1ZRIpCgNxdHkYAiABKAsyHC5nb29nbGUucHJvdG9idWYuVUludDMyVmFsdWUSKgoEbm9yZBgDIAEoCzIc"
    "Lmdvb2dsZS5wcm90b2J1Zi5VSW50MzJWYWx1ZRIpCgNudW0YBCABKAsyHC5nb29nbGUucHJvdG9idWYuVUludDMyVmFsdWUilQEKBURlcHRo"
    "EikKA3RicRgBIAEoCzIcLmdvb2dsZS5wcm90b2J1Zi5VSW50NjRWYWx1ZRIpCgN0c3EYAiABKAsyHC5nb29nbGUucHJvdG9idWYuVUludDY0"
    "VmFsdWUSGgoEYXNrcxgDIAMoCzIMLk1hcmtldExldmVsEhoKBGJpZHMYBCADKAsyDC5NYXJrZXRMZXZlbCK3AgoFUXVvdGUSKAoDbHRwGAEg"
    "ASgLMhsuZ29vZ2xlLnByb3RvYnVmLkludDY0VmFsdWUSKQoDbHR0GAIgASgLMhwuZ29vZ2xlLnByb3RvYnVmLlVJbnQzMlZhbHVlEikKA2x0"
    "cRgDIAEoCzIcLmdvb2dsZS5wcm90b2J1Zi5VSW50MzJWYWx1ZRIpCgN2dHQYBCABKAsyHC5nb29nbGUucHJvdG9idWYuVUludDY0VmFsdWUS"
    "LgoIdnR0X2RpZmYYBSABKAsyHC5nb29nbGUucHJvdG9idWYuVUludDY0VmFsdWUSKAoCb2kYBiABKAsyHC5nb29nbGUucHJvdG9idWYuVUlu"
    "dDY0VmFsdWUSKQoEbHRwYxgHIAEoCzIbLmdvb2dsZS5wcm90b2J1Zi5JbnQ2NFZhbHVlIogDCg1FeHRlbmRlZFF1b3RlEigKA2F0cBgBIAEo"
    "CzIbLmdvb2dsZS5wcm90b2J1Zi5JbnQ2NFZhbHVlEicKAmNwGAIgASgLMhsuZ29vZ2xlLnByb3RvYnVmLkludDY0VmFsdWUSKAoCbGMYAyAB"
    "KAsyHC5nb29nbGUucHJvdG9idWYuVUludDMyVmFsdWUSKAoCdWMYBCABKAsyHC5nb29nbGUucHJvdG9idWYuVUludDMyVmFsdWUSJwoCeWgY"
    "BSABKAsyGy5nb29nbGUucHJvdG9idWYuSW50NjRWYWx1ZRInCgJ5bBgGIAEoCzIbLmdvb2dsZS5wcm90b2J1Zi5JbnQ2NFZhbHVlEikKA3Bv"
    "aRgHIAEoCzIcLmdvb2dsZS5wcm90b2J1Zi5VSW50NjRWYWx1ZRIpCgRvaWNoGAggASgLMhsuZ29vZ2xlLnByb3RvYnVmLkludDY0VmFsdWUS"
    "KAoCcGMYCSABKAsyHC5nb29nbGUucHJvdG9idWYuVUludDMyVmFsdWUiiAIKCkRhaWx5UXVvdGUSJwoCZG8YASABKAsyGy5nb29nbGUucHJv"
    "dG9idWYuSW50NjRWYWx1ZRInCgJkaBgCIAEoCzIbLmdvb2dsZS5wcm90b2J1Zi5JbnQ2NFZhbHVlEicKAmRsGAMgASgLMhsuZ29vZ2xlLnBy"
    "b3RvYnVmLkludDY0VmFsdWUSJwoCZGMYBCABKAsyGy5nb29nbGUucHJvdG9idWYuSW50NjRWYWx1ZRIqCgRkaG9pGAUgASgLMhwuZ29vZ2xl"
    "LnByb3RvYnVmLlVJbnQ2NFZhbHVlEioKBGRsb2kYBiABKAsyHC5nb29nbGUucHJvdG9idWYuVUludDY0VmFsdWUijgIKBU9ITENWEikKBG9w"
    "ZW4YASABKAsyGy5nb29nbGUucHJvdG9idWYuSW50NjRWYWx1ZRIpCgRoaWdoGAIgASgLMhsuZ29vZ2xlLnByb3RvYnVmLkludDY0VmFsdWUS"
    "KAoDbG93GAMgASgLMhsuZ29vZ2xlLnByb3RvYnVmLkludDY0VmFsdWUSKgoFY2xvc2UYBCABKAsyGy5nb29nbGUucHJvdG9idWYuSW50NjRW"
    "YWx1ZRIsCgZ2b2x1bWUYBSABKAsyHC5nb29nbGUucHJvdG9idWYuVUludDMyVmFsdWUSKwoFZXBvY2gYBiABKAsyHC5nb29nbGUucHJvdG9i"
    "dWYuVUludDMyVmFsdWUiHQoJU3ltRGV0YWlsEhAKCHRpY2tzaXplGAEgASgJIs0CCgpNYXJrZXRGZWVkEhUKBXF1b3RlGAEgASgLMgYuUXVv"
    "dGUSGgoCZXEYAiABKAsyDi5FeHRlbmRlZFF1b3RlEhcKAmRxGAMgASgLMgsuRGFpbHlRdW90ZRIVCgVvaGxjdhgEIAEoCzIGLk9ITENWEhUK"
    "BWRlcHRoGAUgASgLMgYuRGVwdGgSLwoJZmVlZF90aW1lGAYgASgLMhwuZ29vZ2xlLnByb3RvYnVmLlVJbnQ2NFZhbHVlEi8KCXNlbmRfdGlt"
    "ZRgHIAEoCzIcLmdvb2dsZS5wcm90b2J1Zi5VSW50NjRWYWx1ZRINCgV0b2tlbhgIIAEoCRITCgtzZXF1ZW5jZV9ubxgJIAEoBBIQCghzbmFw"
    "c2hvdBgKIAEoCBIOCgZ0aWNrZXIYCyABKAkSHQoJc3ltZGV0YWlsGAwgASgLMgouU3ltRGV0YWlsIr4BCg1Tb2NrZXRNZXNzYWdlEhoKBHR5"
    "cGUYASABKA4yDC5NZXNzYWdlVHlwZRIoCgVmZWVkcxgCIAMoCzIZLlNvY2tldE1lc3NhZ2UuRmVlZHNFbnRyeRIQCghzbmFwc2hvdBgDIAEo"
    "CBILCgNtc2cYBCABKAkSDQoFZXJyb3IYBSABKAgaOQoKRmVlZHNFbnRyeRILCgNrZXkYASABKAkSGgoFdmFsdWUYAiABKAsyCy5NYXJrZXRG"
    "ZWVkOgI4ASqGAQoLTWVzc2FnZVR5cGUSCAoEcGluZxAAEgkKBXF1b3RlEAESEgoOZXh0ZW5kZWRfcXVvdGUQAhIPCgtkYWlseV9xdW90ZRAD"
    "EhAKDG1hcmtldF9sZXZlbBAEEgkKBW9obGN2EAUSCQoFZGVwdGgQBhIHCgNhbGwQBxIMCghyZXNwb25zZRAIQgpaCC9nZW5jb2RlYgZwcm90"
    "bzM="
)


# ---- environment, paths ------------------------------------------------------------------------------------------
def env(name: str) -> str | None:
    """Registry first (user, then machine), like NSEUIENGINE's fyers_token._read: a console opened before the 08:00
    token refresh still sees today's values. Then the process environment."""
    if os.name == "nt":
        import winreg
        for root, path in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                           (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(root, path) as k:
                    v = winreg.QueryValueEx(k, name)[0]
                    if v:
                        return str(v)
            except OSError:
                pass
    return os.environ.get(name) or None


def nseui_home() -> Path:
    return Path(env("NSEUIENGINE_HOME") or (Path(os.environ.get("NSE_ROOT") or Path(__file__).resolve().parents[2]) / "code" / "NSEUIENGINE")).resolve()


def unique(paths) -> list[Path]:
    out = []
    for p in paths:
        if p is not None and Path(p) not in out:
            out.append(Path(p))
    return out


def signal_dirs() -> list[Path]:
    """Where NSEDATAENGINE writes signals_for_<date>.csv, resolved the way swing_trader.py does it:
    %NSEDATAENGINE_SIGNALS_DIR%, else %NSE_DATA_ROOT%\\NSEDATAENGINE\\signals, else <NSEDATAENGINE>\\signals."""
    root = env("NSE_DATA_ROOT")
    return unique([env("NSEDATAENGINE_SIGNALS_DIR"),
                   Path(root) / "NSEDATAENGINE" / "signals" if root else None,
                   Path(env("NSEDATAENGINE_HOME") or __import__("os").environ.get("NSE_ROOT", r"D:\CBIL") + r"/code\NSEDATAENGINE") / "signals",
                   Path(__import__("os").environ.get("NSE_ROOT", r"D:\CBIL") + r"/code\NSEDATAENGINE\signals")])


def symbol_master_files() -> list[Path]:
    root = env("NSE_DATA_ROOT")
    bases = unique([env("NSEUIENGINE_DATA_DIR"), Path(root) / "NSEUIENGINE" if root else None, nseui_home()])
    return [b / "state" / "NSE_CM.csv" for b in bases]


# ---- packages, schema ----------------------------------------------------------------------------------------------
def ensure_packages() -> None:
    """Install whatever is missing into this Python (websocket-client, pyarrow, protobuf). Nothing else is touched."""
    missing = []
    try:
        import websocket
        if not hasattr(websocket, "WebSocketApp"):
            sys.exit("The package 'websocket' installed here is not websocket-client. Fix:\n"
                     f'  "{sys.executable}" -m pip uninstall -y websocket\n'
                     f'  "{sys.executable}" -m pip install websocket-client')
    except ImportError:
        missing.append("websocket-client")
    for module, package in (("pyarrow.parquet", "pyarrow"), ("google.protobuf.message_factory", "protobuf")):
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(package)
    if not missing:
        return
    print(f"[setup] installing {' '.join(missing)} for {sys.executable}", flush=True)
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", *missing]
    if subprocess.call(cmd) != 0 and subprocess.call(cmd[:4] + ["--user"] + cmd[4:]) != 0:
        sys.exit(f"[setup] pip could not install {' '.join(missing)}; install them and run this again")
    importlib.invalidate_caches()


def message_class():
    """The protobuf SocketMessage class: from the Fyers SDK if it is installed, else from the embedded schema."""
    try:
        from fyers_apiv3.FyersWebsocket import msg_pb2
        return msg_pb2.SocketMessage, "fyers-apiv3 SDK schema"
    except Exception:
        pass
    from google.protobuf import descriptor_pool, message_factory, wrappers_pb2
    pool = descriptor_pool.DescriptorPool()                  # private pool: never clashes with another msg.proto
    pool.AddSerializedFile(wrappers_pb2.DESCRIPTOR.serialized_pb)
    pool.AddSerializedFile(base64.b64decode("".join(_MSG_PROTO_B64)))
    desc = pool.FindMessageTypeByName("SocketMessage")
    if hasattr(message_factory, "GetMessageClass"):
        return message_factory.GetMessageClass(desc), "embedded Fyers schema"
    return message_factory.MessageFactory(pool).GetPrototype(desc), "embedded Fyers schema"


# ---- token -------------------------------------------------------------------------------------------------------
def credentials() -> tuple[str, str, str]:
    """(app id, access token, where they came from). NSEUIENGINE's fyers_token.py is the source of truth."""
    f = nseui_home() / "fyers_token.py"
    if f.is_file():
        spec = importlib.util.spec_from_file_location("nseui_fyers_token", f)
        tok = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tok)
        try:
            day = tok._read("FYERS_TOKEN_DATE") if hasattr(tok, "_read") else None
            return tok.get_client_id(), tok.get_access_token(require_today=True), f"{f} (issued {day})"
        except Exception as ex:                              # FyersTokenMissing: its text says what to run
            sys.exit(f"[token] {ex}")
    app, token = env("FYERS_CLIENT_ID"), env("FYERS_ACCESS_TOKEN")
    if not app or not token:
        sys.exit(f"[token] {f} not found and FYERS_CLIENT_ID / FYERS_ACCESS_TOKEN are not set")
    return app, token, "environment variables FYERS_CLIENT_ID / FYERS_ACCESS_TOKEN"


def http_json(url: str, auth: str, timeout: float = 10.0) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={"Authorization": auth, "Accept": "application/json",
                                               "User-Agent": f"fyers-tbt-recorder/{VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        body, status = e.read().decode("utf-8", "replace"), e.code
    try:
        return status, json.loads(body) if body.strip() else {}
    except ValueError:
        return status, body[:200]


def socket_url(auth: str) -> tuple[str, bool | None, str]:
    """(url, token accepted?, note). The SDK's getUrl(); if that lookup fails, /profile tells whether the token is
    the problem."""
    try:
        status, d = http_json(URL_LOOKUP, auth)
        url = ((d.get("data") or {}).get("socket_url") if isinstance(d, dict) else None)
        if status == 200 and url:
            return url, True, "from /indus/home/tbtws"
        note = f"lookup HTTP {status} {str(d)[:160]}"
    except Exception as ex:
        note = f"lookup failed: {ex}"
    try:
        status, d = http_json(PROFILE_URL, auth)
        if isinstance(d, dict) and d.get("s") == "ok":
            return URL_FALLBACK, True, f"default URL ({note})"
        msg = d.get("message") if isinstance(d, dict) else d
        return URL_FALLBACK, False, f"/profile HTTP {status}: {msg}"
    except Exception as ex:
        return URL_FALLBACK, None, f"default URL; could not reach Fyers to check the token ({ex})"


# ---- symbols -----------------------------------------------------------------------------------------------------
def load_master() -> tuple[dict[str, list[str]], Path | None]:
    """underlying symbol -> Fyers tickers, from the Fyers NSE_CM symbol master (column 9 ticker, column 13 symbol)."""
    for f in symbol_master_files():
        if f.is_file():
            m: dict[str, list[str]] = {}
            with open(f, newline="", encoding="utf-8", errors="replace") as fh:
                for row in csv.reader(fh):
                    if len(row) > 13 and row[9].startswith("NSE:"):
                        m.setdefault(row[13].strip().upper(), []).append(row[9].strip())
            if m:
                return m, f
    return {}, None


def ticker_for(symbol: str, master: dict[str, list[str]]) -> str | None:
    s = symbol.strip().upper()
    if s.startswith("NSE:"):
        return s if not master or any(s in v for v in master.values()) else None
    if not master:
        return f"NSE:{s}-EQ"
    tickers = master.get(s) or []
    for suffix in ("-EQ", "-BE", "-BZ", "-SM", "-ST"):
        if f"NSE:{s}{suffix}" in tickers:
            return f"NSE:{s}{suffix}"
    return tickers[0] if tickers else None


def signal_file(today) -> Path | None:
    """signals_for_<today>.csv (yesterday evening's scan, for today's session); else the newest one up to today."""
    want = f"signals_for_{today:%Y%m%d}.csv"
    dirs = [d for d in signal_dirs() if d.is_dir()]
    for d in dirs:
        if (d / want).is_file():
            return d / want
    found = []
    for d in dirs:
        for f in d.glob("signals_for_*.csv"):
            stamp = f.stem.rsplit("_", 1)[-1]
            if stamp.isdigit() and len(stamp) == 8 and stamp <= f"{today:%Y%m%d}":
                found.append((stamp, f))
    return max(found)[1] if found else None


def top_signals(path: Path, count: int, master) -> tuple[list[tuple[str, str]], int]:
    """[(ticker, why)] for the `count` best distinct stocks, and how many signal rows the file had."""
    def num(v, default):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    rows.sort(key=lambda r: (str(r.get("regime_allowed", "")).strip().lower() != "true",
                             -num(r.get("score"), float("-inf")),
                             num(r.get("turnover_rank"), float("inf"))))
    picked, seen = [], set()
    for r in rows:
        sym = (r.get("symbol") or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        t = ticker_for(sym, master)
        if not t:
            log.warning("%s is not in the Fyers symbol master - skipped", sym)
            continue
        picked.append((t, f"{r.get('strategy', '?')} {r.get('score', '')}".strip()))
        if len(picked) >= count:
            break
    return picked, len(rows)


# ---- order book recording ------------------------------------------------------------------------------------------
class Book:
    __slots__ = ("bp", "bq", "bo", "ap", "aq", "ao", "tbq", "tsq", "ready", "n")

    def __init__(self):
        self.reset()
        self.ready, self.n = False, 0

    def reset(self):
        self.bp, self.bq, self.bo = [0.0] * LEVELS, [0] * LEVELS, [0] * LEVELS
        self.ap, self.aq, self.ao = [0.0] * LEVELS, [0] * LEVELS, [0] * LEVELS
        self.tbq = self.tsq = 0


def filled(prices: list) -> int:
    k = len(prices)
    while k and prices[k - 1] <= 0:
        k -= 1
    return k


class Recorder:
    """Applies Fyers TBT messages to per-symbol books and keeps one row per update, bucketed by receive minute."""

    def __init__(self, out_dir: Path, levels: int, msg_cls):
        self.out_dir, self.levels, self.Msg = out_dir, levels, msg_cls
        self.lock = threading.Lock()
        self.books: dict[str, Book] = {}
        self.buckets: dict[int, list] = {}
        self.minute_counts: Counter = Counter()      # symbol -> updates since the last status line
        self.total_rows = self.files = self.bytes = self.bad = 0
        self.level_num = [0, 0, 0]                   # Fyers 'num' field vs list position: ==i, ==i+1, other
        self.time_mult: dict[str, int] = {}
        self.texts: Counter = Counter()
        self.snapshots: Counter = Counter()          # symbol -> snapshot messages received

    # called from the websocket threads
    def server_text(self, conn: int, text: str, error: bool = False) -> None:
        self.texts[text] += 1
        if self.texts[text] <= 3:
            (log.error if error else log.info)("WS-%d server%s: %s", conn, " error" if error else "", text)

    def ingest(self, conn: int, data: bytes) -> None:
        m = self.Msg()
        try:
            m.ParseFromString(bytes(data))
        except Exception as ex:
            self.bad += 1
            if self.bad <= 5:
                log.warning("WS-%d: undecodable message (%s)", conn, ex)
            return
        if m.error:
            self.server_text(conn, m.msg or "error", error=True)
            return
        if m.msg:
            self.server_text(conn, m.msg)
        if not m.feeds:
            return
        with self.lock:
            recv = time.time_ns()                    # taken under the lock: a closed minute never gets late rows
            bucket = self.buckets.setdefault(recv // MINUTE_NS, [])
            for key, f in m.feeds.items():
                if not f.HasField("depth"):
                    continue
                t = f.ticker or key
                b = self.books.get(t)
                if b is None:
                    b = self.books[t] = Book()
                snap = bool(m.snapshot or f.snapshot)
                if snap:
                    b.reset()
                self._apply(b, f.depth)
                b.n += 1
                if snap:
                    b.ready = True
                elif not b.ready and b.n >= 200:
                    b.ready = True
                    log.warning("%s: no snapshot flag in 200 updates - recording from now on", t)
                if not b.ready:
                    continue
                kb, ka = min(filled(b.bp), self.levels), min(filled(b.ap), self.levels)
                if snap:
                    self.snapshots[t] += 1
                    if self.snapshots[t] == 1:
                        log.info("%-18s first book: bid %.2f x %d | ask %.2f x %d | %d bid / %d ask levels",
                                 t, b.bp[0], b.bq[0], b.ap[0], b.aq[0], kb, ka)
                bucket.append((recv, t, conn, f.sequence_no, snap,
                               f.feed_time.value if f.HasField("feed_time") else 0,
                               f.send_time.value if f.HasField("send_time") else 0,
                               b.tbq, b.tsq, b.bp[:kb], b.bq[:kb], b.bo[:kb], b.ap[:ka], b.aq[:ka], b.ao[:ka]))
                self.minute_counts[t] += 1

    def _apply(self, b: Book, d) -> None:
        """The SDK's rule (tbt_ws.Depth._addDepth): element i of bids/asks is level i; only fields present change."""
        if d.HasField("tbq"):
            b.tbq = d.tbq.value
        if d.HasField("tsq"):
            b.tsq = d.tsq.value
        ln = self.level_num
        for levels, P, Q, O in ((d.bids, b.bp, b.bq, b.bo), (d.asks, b.ap, b.aq, b.ao)):
            for i, lv in enumerate(levels):
                if i >= LEVELS:
                    break
                if lv.HasField("price"):
                    P[i] = lv.price.value / 100.0
                if lv.HasField("qty"):
                    Q[i] = lv.qty.value
                if lv.HasField("nord"):
                    O[i] = lv.nord.value
                if lv.HasField("num"):
                    n = lv.num.value
                    ln[0 if n == i else 1 if n == i + 1 else 2] += 1

    # called from the main thread
    def pending_rows(self) -> int:
        with self.lock:
            return sum(len(v) for v in self.buckets.values())

    def flush(self, final: bool = False) -> list[tuple[str, int, int]]:
        """Write every finished minute (and, on `final`, the current one; a minute past MAX_ROWS_PER_FILE early).
        Returns [(file, rows, bytes)]."""
        with self.lock:
            now_min = time.time_ns() // MINUTE_NS
            todo = []
            for minute in sorted(self.buckets):
                rows = self.buckets[minute]
                if minute < now_min or final or len(rows) >= MAX_ROWS_PER_FILE:
                    todo.append((minute, rows))
                    del self.buckets[minute]
        done = []
        for minute, rows in todo:
            if rows:
                done.append(self._write(minute, rows))
        return done

    def _ts(self, field: str, raw: tuple, ts_type):
        import pyarrow as pa
        mult = self.time_mult.get(field)
        if mult is None:
            v = next((x for x in raw if x), 0)
            if not v:
                return pa.nulls(len(raw), ts_type)
            now = time.time()
            mult = next((m for m in (1_000_000_000, 1_000_000, 1_000, 1) if abs(v * m / 1e9 - now) < 3 * 86400), 0)
            self.time_mult[field] = mult
            unit = {1_000_000_000: "seconds", 1_000_000: "milliseconds", 1_000: "microseconds", 1: "nanoseconds"}
            log.info("Fyers %s is in %s since 1970 (e.g. %d)", field, unit.get(mult, "an unknown unit"), v)
        if not mult:
            return pa.nulls(len(raw), ts_type)
        return pa.array([x * mult if x else None for x in raw], ts_type)

    def _write(self, minute: int, rows: list) -> tuple[str, int, int]:
        import pyarrow as pa
        import pyarrow.parquet as pq
        ts_type = pa.timestamp("ns", tz=TZ)
        c = list(zip(*rows))
        table = pa.table({
            "ts": pa.array(c[0], ts_type),
            "symbol": pa.array(c[1], pa.string()).dictionary_encode(),
            "conn": pa.array(c[2], pa.int8()),
            "seq": pa.array(c[3], pa.int64()),
            "snapshot": pa.array(c[4], pa.bool_()),
            "exch_ts": self._ts("feed_time", c[5], ts_type),
            "send_ts": self._ts("send_time", c[6], ts_type),
            "tbq": pa.array(c[7], pa.int64()),
            "tsq": pa.array(c[8], pa.int64()),
            "bid_px": pa.array(c[9], pa.list_(pa.float64())),
            "bid_qty": pa.array(c[10], pa.list_(pa.int64())),
            "bid_ord": pa.array(c[11], pa.list_(pa.int32())),
            "ask_px": pa.array(c[12], pa.list_(pa.float64())),
            "ask_qty": pa.array(c[13], pa.list_(pa.int64())),
            "ask_ord": pa.array(c[14], pa.list_(pa.int32())),
            "feed_time": pa.array(c[5], pa.int64()),
            "send_time": pa.array(c[6], pa.int64()),
        })
        start = datetime.fromtimestamp(minute * 60, IST)
        folder = self.out_dir / f"{start:%Y%m%d}"
        folder.mkdir(parents=True, exist_ok=True)
        base = f"tbt_{start:%Y%m%d_%H%M}"
        name, part = f"{base}.parquet", 1
        while (folder / name).exists():                 # never overwrite (a restart within the same minute)
            part += 1
            name = f"{base}_p{part}.parquet"
        tmp = folder / (name + ".tmp")
        pq.write_table(table, tmp, compression="zstd")
        os.replace(tmp, folder / name)                   # readers never see a half-written file
        size = (folder / name).stat().st_size
        self.files += 1
        self.bytes += size
        self.total_rows += len(rows)
        return name, len(rows), size


# ---- websockets ----------------------------------------------------------------------------------------------------
class Conn:
    """One TBT websocket with up to 5 symbols on channel "1"; reconnects with backoff and re-subscribes."""

    def __init__(self, idx: int, tickers: list[str], url: str, auth: str, rec: Recorder, stop: threading.Event):
        self.idx, self.tickers, self.url, self.auth, self.rec, self.stop = idx, tickers, url, auth, rec, stop
        self.ws, self.open, self.opens, self.msgs, self.last_error = None, False, 0, 0, ""
        self.msgs_reported = 0
        self.thread = threading.Thread(target=self._run, name=f"WS-{idx}", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def send(self, obj) -> None:
        ws = self.ws
        if ws is not None and self.open:
            try:
                ws.send(obj if isinstance(obj, str) else json.dumps(obj))
            except Exception as ex:
                log.warning("WS-%d send failed: %s", self.idx, ex)

    def _on_open(self, ws) -> None:
        self.open, self.last_error = True, ""
        self.opens += 1
        log.info("WS-%d connected%s - subscribing %s", self.idx, " again" if self.opens > 1 else "",
                 " ".join(self.tickers))
        self.send({"type": 1, "data": {"subs": 1, "symbols": list(self.tickers), "mode": "depth",
                                       "channel": CHANNEL}})
        self.send({"type": 2, "data": {"resumeChannels": [CHANNEL], "pauseChannels": []}})

    def _on_message(self, ws, data) -> None:
        if isinstance(data, str):
            if data.strip().lower() != "pong":
                self.rec.server_text(self.idx, data[:300])
            return
        self.msgs += 1
        self.rec.ingest(self.idx, data)

    def _on_error(self, ws, err) -> None:
        self.last_error = str(err)
        if not self.stop.is_set():
            log.warning("WS-%d error: %s", self.idx, err)
            if "401" in self.last_error or "403" in self.last_error:
                log.error("WS-%d: Fyers refused the token. Re-issue it with: schtasks /run /tn %s",
                          self.idx, TOKEN_TASK)

    def _on_close(self, ws, code=None, reason=None) -> None:
        self.open = False
        if not self.stop.is_set():
            log.warning("WS-%d closed (code %s%s)", self.idx, code, f", {reason}" if reason else "")

    def _run(self) -> None:
        import websocket
        backoff = 1.0
        while not self.stop.is_set():
            self.ws = websocket.WebSocketApp(self.url, header={"authorization": self.auth},
                                             on_open=self._on_open, on_message=self._on_message,
                                             on_error=self._on_error, on_close=self._on_close)
            started = time.monotonic()
            try:
                self.ws.run_forever()
            except Exception as ex:
                log.warning("WS-%d: %s", self.idx, ex)
            self.open = False
            if self.stop.is_set():
                break
            backoff = 1.0 if time.monotonic() - started > 60 else min(backoff * 2, 30.0)
            log.info("WS-%d reconnecting in %.0f s", self.idx, backoff)
            self.stop.wait(backoff)

    def close(self) -> None:
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass


# ---- main ----------------------------------------------------------------------------------------------------------
def single_instance(path: Path):
    """Keep a second copy from opening 3 more TBT sockets (Fyers allows 3 per app per user)."""
    fh = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def setup_logging(out_dir: Path) -> Path:
    logs = out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / f"tbt_{datetime.now(IST):%Y%m%d}.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    root.handlers[:] = [fh, ch]
    logging.getLogger("websocket").setLevel(logging.CRITICAL)   # its errors reach us through on_error
    return path


def mask(s: str, keep: int = 4) -> str:
    return f"...{s[-keep:]}" if s and len(s) > keep else "***"


def parse_args(argv):
    p = argparse.ArgumentParser(description="Record Fyers TBT 50-level depth to one Parquet file per minute.")
    p.add_argument("--symbols", nargs="+", help="stocks to stream instead of the signals (SBIN or NSE:SBIN-EQ)")
    p.add_argument("--count", type=int, default=15, help="how many stocks (default 15 = 3 sockets x 5)")
    p.add_argument("--connections", type=int, default=3, help="TBT websockets (Fyers allows 3)")
    p.add_argument("--per-connection", type=int, default=5, help="stocks per websocket (Fyers allows 5)")
    p.add_argument("--levels", type=int, default=LEVELS, help="depth levels kept per side (default 50)")
    p.add_argument("--out", default=None, help=r"output folder (default %%FYERSTBT_DATA_DIR%% or ${NSE_ROOT}/code\Fyerstbt)")
    p.add_argument("--until", default="15:31", help="IST time to stop by itself (default 15:31; 'none' = never)")
    p.add_argument("--seconds", type=float, default=0, help="stop after this many seconds (a test run)")
    p.add_argument("--dry-run", action="store_true", help="show symbols, token and socket, then exit")
    p.add_argument("--url", help=argparse.SUPPRESS)        # tests: a local fake TBT server
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    out_dir = Path(a.out or env("FYERSTBT_DATA_DIR") or DEFAULT_OUT)
    log_path = setup_logging(out_dir)
    ensure_packages()
    msg_cls, schema = message_class()
    import google.protobuf
    import pyarrow
    import websocket
    log.info("Fyers TBT recorder %s | Python %s | protobuf %s (%s) | websocket-client %s | pyarrow %s",
             VERSION, sys.version.split()[0], google.protobuf.__version__, schema,
             getattr(websocket, "__version__", "?"), pyarrow.__version__)

    app, token, token_src = credentials()
    auth = f"{app}:{token}"
    log.info("token: app %s, token %s, from %s", app[:4] + "***" + (app[app.rfind("-"):] if "-" in app else ""),
             mask(token), token_src)

    # symbols
    count = max(1, min(a.count, a.connections * a.per_connection))
    master, master_file = load_master()
    if not master:
        log.warning("Fyers symbol master not found (%s) - assuming NSE:<symbol>-EQ",
                    ", ".join(str(f) for f in symbol_master_files()))
    if a.symbols:
        picked = []
        for s in a.symbols:
            t = ticker_for(s, master)
            if t and t not in [x for x, _ in picked]:
                picked.append((t, "--symbols"))
            elif not t:
                log.warning("%s is not in the Fyers symbol master - skipped", s)
        picked = picked[:count]
        log.info("symbols: %d from --symbols", len(picked))
    else:
        today = datetime.now(IST).date()
        f = signal_file(today)
        if f is None:
            sys.exit("[signals] no signals_for_<date>.csv found in " + ", ".join(str(d) for d in signal_dirs()) +
                     " - pass --symbols instead")
        picked, n_rows = top_signals(f, count, master)
        stamp = f.stem.rsplit("_", 1)[-1]
        made = datetime.fromtimestamp(f.stat().st_mtime, IST)
        note = "" if stamp == f"{today:%Y%m%d}" else f"  (NOT today's - newest available is for {stamp})"
        log.info("signals: %s, written %s, %d signals -> best %d%s", f, f"{made:%a %d %b %H:%M}", n_rows,
                 len(picked), note)
        log.info("ranked: %s", ", ".join(f"{t.split(':', 1)[-1].rsplit('-', 1)[0]} ({why})" for t, why in picked))
    if not picked:
        sys.exit("[signals] no symbols to stream")
    tickers = [t for t, _ in picked]
    groups = [tickers[i:i + a.per_connection] for i in range(0, len(tickers), a.per_connection)][:a.connections]
    for i, g in enumerate(groups):
        log.info("WS-%d: %s", i, " ".join(g))

    # socket URL (doubles as the token check)
    if a.url:
        url, ok, note = a.url, True, "--url"
    else:
        url, ok, note = socket_url(auth)
    log.info("socket: %s (%s)", url, note)
    if ok is False:
        log.error("Fyers rejected the access token (%s).", note)
        log.error("If you typed a token into the old START_FYERS_TBT.bat prompt, it was saved over the 08:00 token "
                  "with setx. Re-issue today's token:  schtasks /run /tn %s   then start this again.", TOKEN_TASK)
        return 2

    until = None
    if a.until and a.until.lower() != "none":
        hh, mm = (int(x) for x in a.until.split(":"))
        until = datetime.now(IST).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if datetime.now(IST) >= until:
            log.info("it is past %s - running until Ctrl+C", a.until)
            until = None
    deadline = time.monotonic() + a.seconds if a.seconds else None
    log.info("output: %s\\<yyyymmdd>\\tbt_<yyyymmdd>_<hhmm>.parquet, one per minute | log: %s | stops %s",
             out_dir, log_path, f"at {a.until} IST" if until else ("after %.0f s" % a.seconds if deadline else
                                                                  "on Ctrl+C"))
    if a.dry_run:
        log.info("dry run - not streaming")
        return 0

    lock = single_instance(out_dir / ".fyers_tbt.lock")
    if lock is None:
        log.error("another copy of this recorder is already running (it holds %s) - not starting a second one",
                  out_dir / ".fyers_tbt.lock")
        return 3

    rec = Recorder(out_dir, max(1, min(a.levels, LEVELS)), msg_cls)
    stop = threading.Event()
    conns = [Conn(i, g, url, auth, rec, stop) for i, g in enumerate(groups)]
    for c in conns:
        c.start()
        time.sleep(0.3)
    log.info("streaming - Ctrl+C to stop")

    last_ping, last_min = time.monotonic(), time.time_ns() // MINUTE_NS
    try:
        while True:
            time.sleep(0.5)                                 # time.sleep, not Event.wait: Ctrl+C works on Windows
            if time.monotonic() - last_ping >= 10:
                last_ping = time.monotonic()
                for c in conns:
                    c.send("ping")
            minute = time.time_ns() // MINUTE_NS
            if minute != last_min:
                last_min = minute
                status(rec, conns, rec.flush(), tickers)
            elif rec.pending_rows() >= MAX_ROWS_PER_FILE:
                rec.flush()
            if (until and datetime.now(IST) >= until) or (deadline and time.monotonic() >= deadline):
                log.info("stop time reached")
                break
    except KeyboardInterrupt:
        log.info("Ctrl+C - stopping")
    finally:
        stop.set()
        for c in conns:
            c.close()
        written = rec.flush(final=True)
        for name, rows, size in written:
            log.info("wrote %s: %s updates, %.1f MB (last, partial minute)", name, f"{rows:,}", size / 1e6)
        log.info("done: %d files, %s updates, %.1f MB in %s", rec.files, f"{rec.total_rows:,}", rec.bytes / 1e6,
                 out_dir)
        lock.close()
    return 0


def status(rec: Recorder, conns: list[Conn], written, tickers) -> None:
    counts = rec.minute_counts
    rec.minute_counts = Counter()
    socks = []
    for c in conns:
        n, c.msgs_reported = c.msgs - c.msgs_reported, c.msgs
        socks.append(f"WS-{c.idx} {'open' if c.open else 'DOWN'} {n:,} msg/min")
    socks = "  ".join(socks)
    if written:
        for name, rows, size in written:
            log.info("wrote %s: %s updates, %d/%d symbols, %.1f MB | %s", name, f"{rows:,}",
                     sum(1 for t in tickers if counts.get(t)), len(tickers), size / 1e6, socks)
    else:
        now = datetime.now(IST)
        quiet = (now.weekday() >= 5 or not (9 * 60 + 15 <= now.hour * 60 + now.minute <= 15 * 60 + 31))
        (log.info if quiet else log.warning)("no updates in the last minute | %s%s", socks,
                                             "" if all(c.open for c in conns) else
                                             " | " + "; ".join(f"WS-{c.idx}: {c.last_error}" for c in conns
                                                               if not c.open and c.last_error))
    silent = [t for t in tickers if not counts.get(t)]
    if written and silent:
        log.info("no updates last minute for: %s", " ".join(silent))
    n0, n1, n2 = rec.level_num
    if (n1 or n2) and not getattr(rec, "_num_warned", False):
        rec._num_warned = True
        log.warning("Fyers 'num' level field: 0-based %d, 1-based %d, other %d (rows follow list position, as the "
                    "SDK does)", n0, n1, n2)


if __name__ == "__main__":
    sys.exit(main())
