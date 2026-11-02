"""Noren OMS brokers: Shoonya (Finvasia), Flattrade, Zebu — same REST/WebSocket API via NorenRestApiPy."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import io
import json
import zipfile
from datetime import datetime, time

import pandas as pd
import requests

from ..core.clock import IST
from ..core.models import BrokerOrder, BrokerPosition, OrderType, Product, Quote, Side, Status, eq, idx
from .base import (AuthError, BrokerAdapter, BrokerError, date_spans, fnum, inum, noren_quote, pick)

HOSTS = {
    "shoonya": ("https://api.shoonya.com/NorenWClientTP/", "wss://api.shoonya.com/NorenWSTP/",
                "https://api.shoonya.com/{exch}_symbols.txt.zip"),
    "flattrade": ("https://piconnect.flattrade.in/PiConnectTP/", "wss://piconnect.flattrade.in/PiConnectWSTp/", ""),
    "zebu": ("https://go.mynt.in/NorenWClientTP/", "wss://go.mynt.in/NorenWSTP/", ""),
}
DEFAULT_INDEX_TOKENS = {"NIFTY50": "26000", "NIFTYBANK": "26009", "INDIAVIX": "26017"}


class NorenAdapter(BrokerAdapter):
    name = "shoonya"
    supports_streaming = True
    stream_capacity = 1000
    quote_batch = 1
    min_intervals = {"quote": 0.1, "history": 0.3, "order": 0.12, "orders": 0.5}
    STATUS_MAP = {"OPEN": Status.OPEN, "COMPLETE": Status.FILLED, "CANCELED": Status.CANCELLED,
                  "CANCELLED": Status.CANCELLED, "REJECTED": Status.REJECTED, "TRIGGER_PENDING": Status.TRIGGER_PENDING,
                  "PENDING": Status.NEW}
    PRODUCT = {Product.MIS: "I", Product.CNC: "C"}
    PRICE_TYPE = {OrderType.LIMIT: "LMT", OrderType.MARKET: "MKT", OrderType.SL: "SL-LMT", OrderType.SLM: "SL-MKT"}

    def __init__(self, cfg=None, cache_dir=".cache", **kw):
        self.name = (cfg or {}).get("flavor", "shoonya")
        super().__init__(cfg, cache_dir, **kw)
        h = HOSTS.get(self.name, HOSTS["shoonya"])
        self.host, self.ws_url = self.cfg.get("host") or h[0], self.cfg.get("websocket") or h[1]
        self.master_url = self.cfg.get("master_url") or h[2]
        self._snap: dict[str, dict] = {}

    def login(self, interactive: bool = True) -> None:
        try:
            from NorenRestApiPy.NorenApi import NorenApi
        except ImportError as ex:
            raise BrokerError("pip install NorenRestApiPy websocket-client") from ex

        import sys
        from .transport import BrokerTransport, route_sdk
        self.transport = BrokerTransport.from_config(self.name, self.host, self.cfg)
        self.transport.verify()                  # with static_ip set: no login, no orders unless it is the egress IP
        route_sdk(sys.modules[NorenApi.__module__], self.transport)
        self.transport.warm()                    # DNS + TCP + TLS (+ proxy tunnel) now, not on the first order
        api, c = NorenApi(host=self.host, websocket=self.ws_url), self.cfg
        sess = self.load_session()
        token = (sess or {}).get("token") or c.get("session_token")
        if token:
            api.set_session(userid=c["user_id"], password=c.get("password", ""), usertoken=token)
        else:
            import pyotp
            otp = pyotp.TOTP(c["totp_secret"]).now() if c.get("totp_secret") else (
                input("TOTP: ") if interactive else None)
            if not otp:
                raise AuthError("TOTP required")
            ret = api.login(userid=c["user_id"], password=c["password"], twoFA=otp, vendor_code=c["vendor_code"],
                            api_secret=c["api_secret"], imei=c.get("imei", "nsealgo01"))
            if not ret or ret.get("stat") != "Ok":
                raise AuthError(f"{self.name} login failed: {pick(ret or {}, 'emsg', default=ret)}")
            self.save_session({"token": ret["susertoken"]})
        self._client = api
        self.transport.start_keepalive()

    def _fetch_master(self) -> pd.DataFrame:
        if not self.master_url:
            raise BrokerError(f"set broker.brokers.{self.name}.master_url (Noren symbol-master zip URL template)")
        rows = []
        for exch in ("NSE",):
            raw = requests.get(self.master_url.format(exch=exch), timeout=60).content
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                df = pd.read_csv(z.open(z.namelist()[0]), dtype=str)
            df.columns = [c.strip() for c in df.columns]
            for _, r in df.iterrows():
                inst_type = str(r.get("Instrument", "")).strip()
                if exch == "NSE" and inst_type == "EQ":
                    rows.append({"key": eq(r["Symbol"]), "exchange": "NSE", "kind": "EQ", "symbol": r["Symbol"],
                                 "broker_symbol": r["TradingSymbol"], "token": r["Token"], "lot_size": r.get("LotSize"),
                                 "tick_size": r.get("TickSize")})
        for name, tok in {**DEFAULT_INDEX_TOKENS, **(self.cfg.get("index_tokens") or {})}.items():
            rows.append({"key": idx(name), "exchange": "NSE", "kind": "IDX", "symbol": name, "broker_symbol": name,
                         "token": str(tok)})
        return pd.DataFrame(rows)

    def _q(self, k: str, d: dict) -> Quote:
        return noren_quote(k, d, self.name)

    def get_quotes(self, keys):
        out = []
        for k in keys:
            inst = self.instrument(k)
            self.throttle("quote")
            d = self._client.get_quotes(exchange=self.exch(inst), token=inst.token)
            if d and d.get("stat") == "Ok" and d.get("lp") is not None:
                out.append(self._q(k, d))
        return out

    def get_history(self, key, interval, start, end):
        inst = self.instrument(key)
        rows = []
        if interval == "1m":
            for s, e in date_spans(start, end, 30):
                self.throttle("history")
                t0 = datetime.combine(s, time(9, 0), IST).timestamp()
                t1 = datetime.combine(e, time(15, 31), IST).timestamp()
                for r in self._client.get_time_price_series(exchange=self.exch(inst), token=inst.token,
                                                            starttime=t0, endtime=t1, interval=1) or []:
                    ts = datetime.strptime(r["time"], "%d-%m-%Y %H:%M:%S")
                    rows.append((ts, r["into"], r["inth"], r["intl"], r["intc"], r.get("intv", 0)))
        else:
            self.throttle("history")
            t0 = datetime.combine(start, time(0, 0), IST).timestamp()
            t1 = datetime.combine(end, time(23, 59), IST).timestamp()
            for r in self._client.get_daily_price_series(exchange=self.exch(inst), tradingsymbol=inst.broker_symbol,
                                                         startdate=t0, enddate=t1) or []:
                r = json.loads(r) if isinstance(r, str) else r
                ts = datetime.strptime(r["time"], "%d-%b-%Y")
                rows.append((ts, r["into"], r["inth"], r["intl"], r["intc"], r.get("intv", 0)))
        return self.candles_df(rows, interval)

    def place_order(self, req, client_id):
        inst = self.instrument(req.key)
        self.throttle("order")
        ret = self._client.place_order(buy_or_sell="B" if req.side == Side.BUY else "S",
                                       product_type=self.PRODUCT[req.product], exchange=self.exch(inst),
                                       tradingsymbol=inst.broker_symbol, quantity=int(req.qty), discloseqty=0,
                                       price_type=self.PRICE_TYPE[req.order_type], price=float(req.price or 0),
                                       trigger_price=req.trigger, retention="DAY", amo="NO", remarks=client_id)
        if not ret or ret.get("stat") != "Ok":
            raise BrokerError(f"place_order failed: {pick(ret or {}, 'emsg', default=ret)}")
        bid = str(ret["norenordno"])
        self._order_cache[bid] = {"exch": self.exch(inst), "tsym": inst.broker_symbol, "qty": int(req.qty),
                                  "ptype": self.PRICE_TYPE[req.order_type], "price": req.price}
        return bid

    def modify_order(self, broker_id, *, price=None, trigger=None, qty=None, order_type=None):
        c = self._order_cache.get(broker_id)
        if not c:
            raise BrokerError("unknown order (placed by another session?)")
        ptype = self.PRICE_TYPE[OrderType(order_type)] if order_type is not None else c["ptype"]
        self.throttle("order")
        ret = self._client.modify_order(orderno=broker_id, exchange=c["exch"], tradingsymbol=c["tsym"],
                                        newquantity=int(qty or c["qty"]), newprice_type=ptype,
                                        newprice=float(price if price is not None else (c["price"] or 0)),
                                        newtrigger_price=trigger)
        if not ret or ret.get("stat") != "Ok":
            raise BrokerError(f"modify failed: {pick(ret or {}, 'emsg', default=ret)}")
        c.update({"ptype": ptype, "price": price if price is not None else c["price"], "qty": int(qty or c["qty"])})

    def cancel_order(self, broker_id):
        self.throttle("order")
        ret = self._client.cancel_order(orderno=broker_id)
        if not ret or ret.get("stat") != "Ok":
            raise BrokerError(f"cancel failed: {pick(ret or {}, 'emsg', default=ret)}")

    def get_orders(self):
        self.throttle("orders")
        out = []
        for r in self._client.get_order_book() or []:
            if r.get("stat") != "Ok":
                continue
            out.append(BrokerOrder(str(r.get("norenordno")), self.map_status(r.get("status")), inum(r.get("fillshares")),
                                   fnum(r.get("avgprc"), 0.0), self.key_for(symbol=r.get("tsym")) or "",
                                   Side.BUY if r.get("trantype") == "B" else Side.SELL, inum(r.get("qty")),
                                   r.get("remarks") or "", r.get("rejreason") or "", r))
        return out

    def get_funds(self) -> dict:
        self.throttle("orders")
        d = self._client.get_limits() or {}
        if d.get("stat") != "Ok":
            return {}
        cash = fnum(d.get("cash"), 0.0) + fnum(d.get("payin"), 0.0)
        return {"cash": cash, "used": fnum(d.get("marginused"), 0.0),
                "available": cash - fnum(d.get("marginused"), 0.0)}

    def get_positions(self):
        self.throttle("orders")
        out: dict = {}
        prod_map = {"I": "MIS", "C": "CNC"}
        for r in self._client.get_positions() or []:
            k = self.key_for(symbol=r.get("tsym"))
            if k and r.get("stat", "Ok") == "Ok":
                p = prod_map.get(r.get("prd"), r.get("prd"))
                out[(k, p)] = BrokerPosition(k, inum(r.get("netqty")), fnum(r.get("netavgprc"), 0.0), p, r)
        for h in self._client.get_holdings() or []:
            sym = next((x.get("tsym") for x in h.get("exch_tsym", []) if x.get("exch") == "NSE"), None)
            k = self.key_for(symbol=sym)
            q = inum(h.get("holdqty")) + inum(h.get("btstqty"))
            if k and q:
                prev = out.get((k, "CNC"))
                out[(k, "CNC")] = BrokerPosition(k, q + (prev.qty if prev else 0), fnum(h.get("upldprc"), 0.0), "CNC", h)
        return list(out.values())

    def stream(self, keys, on_quote):
        """Noren websocket (free on Shoonya/Flattrade/Zebu), snapquote feed with 5-level depth. Incremental."""
        new = {}
        for k in keys:
            inst = self.instrument(k)
            new[f"{self.exch(inst)}|{inst.token}"] = k
        if getattr(self, "_toks", None) is not None:
            self._toks.update(new)
            if self._ws_open:
                self._client.subscribe(list(new), feed_type=2)
            return
        self._toks, self._ws_open = dict(new), False

        def on_feed(msg):
            if msg.get("t") not in ("tk", "tf", "dk", "df"):
                return
            tk = f"{msg.get('e')}|{msg.get('tk')}"
            k = self._toks.get(tk)
            if not k:
                return
            snap = self._snap.setdefault(tk, {})
            snap.update({x: v for x, v in msg.items() if v not in (None, "")})
            if snap.get("lp") is not None:
                on_quote(self._q(k, snap))

        def on_open():
            self._ws_open = True
            self._client.subscribe(list(self._toks), feed_type=2)

        self._client.start_websocket(subscribe_callback=on_feed, socket_open_callback=on_open)
