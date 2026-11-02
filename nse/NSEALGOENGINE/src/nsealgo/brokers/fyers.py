"""FYERS API v3 (fyers-apiv3). Streaming via FyersDataSocket (SymbolUpdate with best bid/ask sizes).

Install note: fyers-apiv3 pulls the obsolete PyPI package 'asyncio' which shadows the standard
library on Python 3.12 -> run `pip uninstall -y asyncio` after installing (nsealgo doctor checks this).
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import time
from dataclasses import replace

from datetime import datetime

import pandas as pd

from ..core.clock import IST
from ..core.models import BrokerOrder, BrokerPosition, OrderType, Product, Quote, Side, Status, eq, idx
from .base import (AuthError, BrokerAdapter, BrokerError, OrderUncertain, chunks, date_spans, fnum, inum, now_ist,
                   pick)

CM_URL = "https://public.fyers.in/sym_details/NSE_CM.csv"
API_BASE = "https://api-t1.fyers.in/api/v3/"         # the SDK's Config.API; data calls use the same host



def daily_token(client_id) -> str | None:
    """Today's access token from NSEUIENGINE's token task (user environment FYERS_ACCESS_TOKEN, FYERS_TOKEN_DATE,
    FYERS_CLIENT_ID; read from the registry on Windows so a long-running process sees the fresh value)."""
    import os
    from datetime import date

    def read(name):
        if os.name == "nt":
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                    return str(winreg.QueryValueEx(k, name)[0])
            except OSError:
                pass
        return os.environ.get(name)

    tok, day, app = read("FYERS_ACCESS_TOKEN"), read("FYERS_TOKEN_DATE"), read("FYERS_CLIENT_ID")
    if tok and day == date.today().isoformat() and (not client_id or app == client_id):
        return tok
    return None

class FyersAdapter(BrokerAdapter):
    name = "fyers"
    supports_streaming = True
    stream_capacity = 5000        # Fyers docs: up to 5,000 data subscriptions on the single data socket

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        if str(self.cfg.get("depth", "symbol")) == "depth5":
            self.stream_capacity = 2500   # quotes + 5-level depth: two subscriptions per stock
        # Fyers docs: 10 calls/s and 200/min (Prime: 600/min) across the API; exceeding the per-minute limit more than
        # 3 times in a day blocks the user for the rest of the day. Stay at 90%.
        per_min = int(self.cfg.get("rate_per_minute", 200))
        self.api_limits = (9, int(per_min * 0.9))
    quote_batch = 50
    min_intervals = {"quote": 1.0, "history": 0.4, "order": 0.12, "orders": 0.5}
    STATUS_MAP = {"1": Status.CANCELLED, "2": Status.FILLED, "3": Status.NEW, "4": Status.NEW, "5": Status.REJECTED,
                  "6": Status.OPEN, "7": Status.CANCELLED}
    ORDER_TYPE = {OrderType.LIMIT: 1, OrderType.MARKET: 2, OrderType.SLM: 3, OrderType.SL: 4}
    PRODUCT = {Product.MIS: "INTRADAY", Product.CNC: "CNC"}

    def login(self, interactive: bool = True) -> None:
        try:
            from fyers_apiv3 import fyersModel
        except ImportError as ex:
            raise BrokerError("pip install fyers-apiv3 (then pip uninstall -y asyncio)") from ex
        c = self.cfg
        from .transport import BrokerTransport
        if getattr(self, "transport", None) is None:
            self.transport = BrokerTransport.from_config(self.name, API_BASE, c)
        self.transport.verify()                  # with static_ip set: no login, no orders unless it is the egress IP
        sess = self.load_session()
        if sess is None:
            tok = daily_token(c.get("client_id"))
            if tok:                              # NSEUIENGINE's 08:00 task logs in daily (TOTP) and stores it
                sess = {"access_token": tok}
        if sess is None:
            if not interactive:
                raise AuthError("no FYERS session for today; run: nsealgo login fyers")
            s = fyersModel.SessionModel(client_id=c["client_id"], secret_key=c["secret_key"],
                                        redirect_uri=c["redirect_uri"], response_type="code",
                                        grant_type="authorization_code")
            code = self.oauth_capture(s.generate_authcode(), "auth_code", c["redirect_uri"])
            s.set_token(code)
            resp = s.generate_token()
            if not resp or not resp.get("access_token"):
                raise AuthError(f"FYERS token exchange failed: {resp}")
            sess = {"access_token": resp["access_token"]}
            self.save_session(sess)
        self._token = sess["access_token"]
        self._client = fyersModel.FyersModel(client_id=c["client_id"], token=self._token, is_async=False,
                                             log_path=str(self.cache_dir))
        self._wire(self._client)
        self.transport.warm()
        self.transport.start_keepalive()

    def _wire(self, client) -> None:
        """Put the transport in place of the SDK's own requests.Session: pooled keep-alive connection, the static-IP
        proxy, and timeouts (the SDK sets none)."""
        svc = getattr(client, "service", None)
        if svc is not None and hasattr(svc, "session"):
            svc.session = self.transport.as_session()

    def _fetch_master(self) -> pd.DataFrame:
        rows = []
        cm = pd.read_csv(CM_URL, header=None, dtype=str)
        for _, r in cm.iterrows():
            t = str(r[9])
            if t.endswith("-EQ"):
                sym = t.split(":", 1)[1][:-3]
                rows.append({"key": eq(sym), "exchange": "NSE", "kind": "EQ", "symbol": sym, "broker_symbol": t,
                             "token": r[0], "lot_size": r[3], "tick_size": r[4], "name": r[1]})
            elif t.endswith("-INDEX"):
                rows.append({"key": idx(t.split(":", 1)[1]), "exchange": "NSE", "kind": "IDX",
                             "symbol": t.split(":", 1)[1], "broker_symbol": t, "token": r[0], "name": r[1]})
        return pd.DataFrame(rows)

    def _ok(self, resp, what: str) -> dict:
        if not isinstance(resp, dict) or resp.get("s") != "ok":
            raise BrokerError(f"FYERS {what} failed: {pick(resp or {}, 'message', default=resp)}")
        return resp

    def get_quotes(self, keys):
        m = {self.instrument(k).broker_symbol: k for k in keys}
        out = []
        for batch in chunks(list(m), self.quote_batch):
            self.throttle("quote")
            resp = self._ok(self._client.quotes(data={"symbols": ",".join(batch)}), "quotes")
            for d in resp.get("d", []):
                v, k = d.get("v") or {}, m.get(d.get("n"))
                if not k or v.get("lp") is None:
                    continue
                out.append(Quote(k, now_ist(), float(v["lp"]), fnum(v.get("volume"), 0.0), fnum(v.get("bid")) or None,
                                 fnum(v.get("ask")) or None, open=fnum(v.get("open_price")), high=fnum(v.get("high_price")),
                                 low=fnum(v.get("low_price")), prev_close=fnum(v.get("prev_close_price"))))
        return out

    def get_history(self, key, interval, start, end):
        sym = self.instrument(key).broker_symbol
        res, span = ("1", 90) if interval == "1m" else ("D", 365)
        rows = []
        for s, e in date_spans(start, end, span):
            self.throttle("history")
            resp = self._client.history(data={"symbol": sym, "resolution": res, "date_format": "1",
                                              "range_from": s.isoformat(), "range_to": e.isoformat(), "cont_flag": "1"})
            if isinstance(resp, dict) and resp.get("s") == "ok":
                rows += [tuple(c[:6]) for c in resp.get("candles", [])]
        return self.candles_df(self.completed(rows, interval, now_ist()), interval)

    @staticmethod
    def completed(rows: list, interval: str, now) -> list:
        """Drop a candle that hasn't closed yet (docs: the current candle is included unless range_to is a full
        resolution back): a 1-minute candle younger than 60 s, or today's daily candle before 15:30 IST."""
        if not rows:
            return rows
        ts = float(rows[-1][0])
        if interval == "1m" and ts + 60 > now.timestamp():
            return rows[:-1]
        if interval != "1m" and datetime.fromtimestamp(ts, IST).date() == now.date() and (now.hour, now.minute) < (15, 30):
            return rows[:-1]
        return rows

    def place_order(self, req, client_id):
        inst = self.instrument(req.key)
        data = {"symbol": inst.broker_symbol, "qty": int(req.qty), "type": self.ORDER_TYPE[req.order_type],
                "side": 1 if req.side == Side.BUY else -1, "productType": self.PRODUCT[req.product],
                "limitPrice": float(req.price or 0), "stopPrice": float(req.trigger or 0), "validity": "DAY",
                "disclosedQty": 0, "offlineOrder": False, "orderTag": client_id}
        self.throttle("order")
        resp = self._client.place_order(data=data)
        uncertain = isinstance(resp, dict) and (str(resp.get("code")) in ("201", "-99")
                                                or (resp.get("s") == "ok" and not resp.get("id")))
        if uncertain:
            # 201 (docs): request made, no acknowledgement. -99: the SDK swallowed a network error (timeout, reset),
            # so the order may have reached Fyers. Either way, check the order book before anything else.
            bid = self._find_tag(client_id)
            if bid:
                return bid
            why = getattr(getattr(self, "transport", None), "last_error", None) or resp.get("message", resp)
            raise OrderUncertain(f"FYERS gave no acknowledgement for {client_id} ({why}); the order book will confirm it")
        return str(self._ok(resp, "place_order")["id"])

    def modify_order(self, broker_id, *, price=None, trigger=None, qty=None, order_type=None):
        data = {"id": broker_id}
        if price is not None:
            data["limitPrice"] = float(price)
        if trigger is not None:
            data["stopPrice"] = float(trigger)
        if qty:
            data["qty"] = int(qty)
        if order_type is not None:
            data["type"] = self.ORDER_TYPE[OrderType(order_type)]
        self.throttle("order")
        self._ok(self._client.modify_order(data=data), "modify_order")

    def cancel_order(self, broker_id):
        self.throttle("order")
        self._ok(self._client.cancel_order(data={"id": broker_id}), "cancel_order")

    @staticmethod
    def user_tag(raw) -> str:
        """Fyers docs: '1:' is put before a tag you set and '2:' before a tag Fyers generated (e.g. '2:Untagged')."""
        t = str(raw or "")
        if t.startswith("1:"):
            return t[2:]
        return "" if t.startswith("2:") else t

    def _find_tag(self, tag: str, tries: int = 2) -> str | None:
        for i in range(tries):
            for o in self.get_orders():
                if o.tag == tag:
                    return o.broker_id
            if i + 1 < tries:
                time.sleep(0.5)
        return None

    def get_orders(self):
        self.throttle("orders")
        resp = self._client.orderbook() or {}
        out = []
        for r in resp.get("orderBook", []) or []:
            out.append(BrokerOrder(str(r.get("id")), self.map_status(r.get("status")), inum(r.get("filledQty")),
                                   fnum(r.get("tradedPrice"), 0.0), self.key_for(symbol=r.get("symbol")) or "",
                                   Side.BUY if r.get("side") == 1 else Side.SELL, inum(r.get("qty")),
                                   self.user_tag(r.get("orderTag")), r.get("message") or "", r))
        return out

    def get_positions(self):
        self.throttle("orders")
        out: dict = {}
        for r in (self._client.positions() or {}).get("netPositions", []) or []:
            k = self.key_for(symbol=r.get("symbol"))
            if k:
                prod = {"INTRADAY": "MIS"}.get(r.get("productType"), r.get("productType"))
                out[(k, prod)] = BrokerPosition(k, inum(r.get("netQty")), fnum(pick(r, "netAvg", "avgPrice"), 0.0), prod, r)
        self.throttle("orders")                              # holdings is a second API call
        for h in (self._client.holdings() or {}).get("holdings", []) or []:
            k = self.key_for(symbol=h.get("symbol"))
            q = self.holding_qty(h)
            if k and q:
                prev = out.get((k, "CNC"))
                out[(k, "CNC")] = BrokerPosition(k, q + (prev.qty if prev else 0), fnum(h.get("costPrice"), 0.0), "CNC", h)
        return list(out.values())

    @staticmethod
    def holding_qty(h: dict) -> int:
        """Shares held at the start of the day (today's buys and sells are in netPositions). The docs define T1 as
        bought but not yet delivered, without showing a T1 row: count it once whichever field carries it."""
        q, t1 = inum(h.get("quantity")), inum(h.get("qty_t1"))
        return max(q, t1) if str(h.get("holdingType", "")).upper() == "T1" else q + t1

    def stream(self, keys, on_quote):
        """FYERS data socket (free). Incremental: later calls subscribe more symbols on the same socket."""
        from fyers_apiv3.FyersWebsocket import data_ws
        new = {self.instrument(k).broker_symbol: k for k in keys}
        depth5 = str(self.cfg.get("depth", "symbol")) == "depth5"     # 5-level DepthUpdate as well as quotes
        if getattr(self, "_ws", None) is not None:
            self._syms.update(new)
            self._ws.subscribe(symbols=list(new), data_type="SymbolUpdate")
            if depth5:
                self._ws.subscribe(symbols=list(new), data_type="DepthUpdate")
            return
        self._syms, self._lastq, self._depth = dict(new), {}, {}

        def on_msg(msg):
            if not isinstance(msg, dict):
                return
            k = self._syms.get(msg.get("symbol"))
            if not k:
                return
            if "bid_price1" in msg:                       # DepthUpdate: 5 levels with order counts
                self._depth[k] = (time.monotonic(), self.parse_depth(msg))
                q = self._lastq.get(k)
                if q is not None:
                    on_quote(self.with_depth(q, self._depth[k][1], now_ist()))
                return
            if msg.get("ltp") is None:
                return
            q = self.parse_tick(msg, k)
            self._lastq[k] = q
            d = self._depth.get(k)
            on_quote(self.with_depth(q, d[1]) if d and time.monotonic() - d[0] < 2.0 else q)

        sock = data_ws.FyersDataSocket(access_token=f"{self.cfg['client_id']}:{self._token}", litemode=False,
                                       write_to_file=False, reconnect=True, reconnect_retry=50,
                                       log_path=str(self.cache_dir), on_message=on_msg,
                                       on_error=lambda e: self.log.warning("ws error %s", e),
                                       on_close=lambda e: self.log.info("ws closed %s", e),
                                       on_connect=lambda: self._subscribe_all(sock, depth5))
        sock.connect()
        self._ws = sock

    def _subscribe_all(self, sock, depth5: bool) -> None:
        sock.subscribe(symbols=list(self._syms), data_type="SymbolUpdate")
        if depth5:
            sock.subscribe(symbols=list(self._syms), data_type="DepthUpdate")

    @staticmethod
    def parse_depth(msg: dict) -> tuple:
        """DepthUpdate fields bid_price1..5 / bid_size1..5 / bid_order1..5 (and ask_*) -> (bids, asks)."""
        side = []
        for s in ("bid", "ask"):
            lv = [(fnum(msg.get(f"{s}_price{i}")), fnum(msg.get(f"{s}_size{i}"), 0.0), int(fnum(msg.get(f"{s}_order{i}"), 0) or 0))
                  for i in range(1, 6)]
            side.append(tuple((p, q, o) for p, q, o in lv if p and p > 0))
        return side[0], side[1]

    @staticmethod
    def with_depth(q: Quote, depth: tuple, ts=None) -> Quote:
        bids, asks = depth
        if not bids or not asks:
            return q
        return replace(q, ts=ts or q.ts, bids=bids, asks=asks, bid=bids[0][0], ask=asks[0][0], bid_qty=bids[0][1],
                       ask_qty=asks[0][1])

    @staticmethod
    def parse_tick(msg: dict, key: str) -> Quote:
        et = fnum(msg.get("exch_feed_time")) or fnum(msg.get("last_traded_time"))
        return Quote(key, now_ist(), float(msg["ltp"]), fnum(msg.get("vol_traded_today"), 0.0) or 0.0,
                     fnum(msg.get("bid_price")) or None, fnum(msg.get("ask_price")) or None, fnum(msg.get("bid_size")),
                     fnum(msg.get("ask_size")), upper_circuit=fnum(msg.get("upper_ckt")) or None,
                     lower_circuit=fnum(msg.get("lower_ckt")) or None, open=fnum(msg.get("open_price")),
                     high=fnum(msg.get("high_price")), low=fnum(msg.get("low_price")),
                     prev_close=fnum(msg.get("prev_close_price")),
                     exch_ts=datetime.fromtimestamp(et, IST) if et else None, source="fyers")
