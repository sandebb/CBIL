"""Angel One SmartAPI (smartapi-python). TOTP login each run; polling quotes with depth (FULL mode)."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import threading
from datetime import datetime

import pandas as pd
import requests

from ..core.clock import IST
from ..core.models import BrokerOrder, BrokerPosition, OrderType, Product, Quote, Side, Status, eq, idx
from .base import (AuthError, BrokerAdapter, BrokerError, chunks, date_spans, fnum, inum, now_ist, pick,
                   quote_from_depth)

MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"


class AngelOneAdapter(BrokerAdapter):
    name = "angelone"
    supports_streaming = True
    stream_capacity = 3000          # SmartAPI WebSocket 2.0: 1,000 tokens per session, 3 sessions per user
    WS_PER_SESSION, WS_SESSIONS = 1000, 3
    quote_batch = 50
    min_intervals = {"quote": 1.0, "history": 0.35, "order": 0.12, "orders": 0.5}
    STATUS_MAP = {
        "OPEN": Status.OPEN, "COMPLETE": Status.FILLED, "CANCELLED": Status.CANCELLED, "REJECTED": Status.REJECTED,
        "TRIGGER PENDING": Status.TRIGGER_PENDING, "OPEN PENDING": Status.NEW, "VALIDATION PENDING": Status.NEW,
        "PUT ORDER REQ RECEIVED": Status.NEW, "MODIFY PENDING": Status.OPEN, "MODIFIED": Status.OPEN,
        "CANCEL PENDING": Status.OPEN, "AFTER MARKET ORDER REQ RECEIVED": Status.OPEN,
    }
    PRODUCT = {Product.MIS: "INTRADAY", Product.CNC: "DELIVERY"}
    OT = {OrderType.LIMIT: "LIMIT", OrderType.MARKET: "MARKET", OrderType.SL: "STOPLOSS_LIMIT",
          OrderType.SLM: "STOPLOSS_MARKET"}

    def login(self, interactive: bool = True) -> None:
        try:
            from SmartApi import SmartConnect
        except ImportError as ex:
            raise BrokerError("pip install smartapi-python logzero websocket-client") from ex
        import pyotp
        c = self.cfg
        obj = SmartConnect(api_key=c["api_key"])
        data = obj.generateSession(c["client_code"], c["pin"], pyotp.TOTP(c["totp_secret"]).now())
        if not data or not data.get("status"):
            raise AuthError(f"Angel One login failed: {pick(data or {}, 'message', default=data)}")
        self._client = obj
        self._jwt = (data.get("data") or {}).get("jwtToken")
        self._feed_token = obj.getfeedToken()

    def _fetch_master(self) -> pd.DataFrame:
        rows = []
        for r in requests.get(MASTER_URL, timeout=120).json():
            ex, it, sym = r.get("exch_seg"), r.get("instrumenttype") or "", r.get("symbol") or ""
            tick = (fnum(r.get("tick_size"), 5.0) or 5.0) / 100.0
            if ex == "NSE" and sym.endswith("-EQ"):
                rows.append({"key": eq(r["name"]), "exchange": "NSE", "kind": "EQ", "symbol": r["name"],
                             "broker_symbol": sym, "token": r["token"], "lot_size": r.get("lotsize", 1), "tick_size": tick})
            elif ex == "NSE" and it == "AMXIDX":
                rows.append({"key": idx(r["name"]), "exchange": "NSE", "kind": "IDX", "symbol": r["name"],
                             "broker_symbol": sym, "token": r["token"]})
        return pd.DataFrame(rows)

    def get_quotes(self, keys):
        toks = {}
        for k in keys:
            inst = self.instrument(k)
            toks[(self.exch(inst), inst.token)] = k
        out = []
        for batch in chunks(list(toks), self.quote_batch):
            req: dict = {}
            for ex, t in batch:
                req.setdefault(ex, []).append(t)
            self.throttle("quote")
            resp = self._client.getMarketData("FULL", req) or {}
            for d in ((resp.get("data") or {}).get("fetched") or []):
                k = toks.get((d.get("exchange"), str(d.get("symbolToken"))))
                if k and d.get("ltp") is not None:
                    out.append(quote_from_depth(k, d["ltp"], d.get("tradeVolume"), d.get("depth"),
                                                oi=d.get("opnInterest"), upper_circuit=d.get("upperCircuit"),
                                                lower_circuit=d.get("lowerCircuit"), open=d.get("open"),
                                                high=d.get("high"), low=d.get("low"), prev_close=d.get("close")))
        return out

    def get_history(self, key, interval, start, end):
        inst = self.instrument(key)
        iv, span = ("ONE_MINUTE", 30) if interval == "1m" else ("ONE_DAY", 1900)
        rows = []
        for s, e in date_spans(start, end, span):
            self.throttle("history")
            resp = self._client.getCandleData({"exchange": self.exch(inst), "symboltoken": inst.token, "interval": iv,
                                               "fromdate": f"{s:%Y-%m-%d} 09:00", "todate": f"{e:%Y-%m-%d} 15:30"}) or {}
            rows += [tuple(c[:6]) for c in (resp.get("data") or [])]
        return self.candles_df(rows, interval)

    def place_order(self, req, client_id):
        inst = self.instrument(req.key)
        variety = "STOPLOSS" if req.order_type in (OrderType.SL, OrderType.SLM) else "NORMAL"
        p = {"variety": variety, "tradingsymbol": inst.broker_symbol, "symboltoken": inst.token,
             "transactiontype": req.side.value, "exchange": self.exch(inst), "ordertype": self.OT[req.order_type],
             "producttype": self.PRODUCT[req.product], "duration": "DAY", "price": str(req.price or 0),
             "triggerprice": str(req.trigger or 0), "squareoff": "0", "stoploss": "0", "quantity": str(int(req.qty)),
             "ordertag": client_id}
        self.throttle("order")
        resp = self._client.placeOrderFullResponse(p) or {}
        oid = (resp.get("data") or {}).get("orderid")
        if not resp.get("status") or not oid:
            raise BrokerError(f"Angel place_order: {pick(resp, 'message', default=resp)}")
        self._order_cache[str(oid)] = p
        return str(oid)

    def modify_order(self, broker_id, *, price=None, trigger=None, qty=None, order_type=None):
        p = dict(self._order_cache.get(broker_id) or {})
        if not p:
            raise BrokerError("unknown order (placed by another session?)")
        if price is not None:
            p["price"] = str(price)
        if trigger is not None:
            p["triggerprice"] = str(trigger)
        if qty:
            p["quantity"] = str(int(qty))
        if order_type is not None:
            p["ordertype"] = self.OT[OrderType(order_type)]
        p["orderid"] = broker_id
        self.throttle("order")
        resp = self._client.modifyOrder(p) or {}
        if not resp.get("status"):
            raise BrokerError(f"Angel modify: {pick(resp, 'message', default=resp)}")
        self._order_cache[broker_id] = p

    def cancel_order(self, broker_id):
        variety = (self._order_cache.get(broker_id) or {}).get("variety", "NORMAL")
        self.throttle("order")
        resp = self._client.cancelOrder(broker_id, variety) or {}
        if not resp.get("status"):
            raise BrokerError(f"Angel cancel: {pick(resp, 'message', default=resp)}")

    def get_orders(self):
        self.throttle("orders")
        out = []
        for r in (self._client.orderBook() or {}).get("data") or []:
            out.append(BrokerOrder(str(r.get("orderid")), self.map_status(pick(r, "status", "orderstatus")),
                                   inum(r.get("filledshares")), fnum(r.get("averageprice"), 0.0),
                                   self.key_for(token=r.get("symboltoken")) or "",
                                   Side(r["transactiontype"]) if r.get("transactiontype") in ("BUY", "SELL") else None,
                                   inum(r.get("quantity")), r.get("ordertag") or "", r.get("text") or "", r))
        return out

    def get_positions(self):
        self.throttle("orders")
        out: dict = {}
        pm = {"INTRADAY": "MIS", "DELIVERY": "CNC"}
        for r in (self._client.position() or {}).get("data") or []:
            k = self.key_for(token=r.get("symboltoken"))
            if k:
                p = pm.get(r.get("producttype"), r.get("producttype"))
                out[(k, p)] = BrokerPosition(k, inum(r.get("netqty")), fnum(pick(r, "avgnetprice", "netprice"), 0.0), p, r)
        for h in (self._client.holding() or {}).get("data") or []:
            k = self.key_for(token=h.get("symboltoken"))
            q = inum(h.get("quantity")) + inum(h.get("t1quantity"))
            if k and q:
                prev = out.get((k, "CNC"))
                out[(k, "CNC")] = BrokerPosition(k, q + (prev.qty if prev else 0), fnum(h.get("averageprice"), 0.0), "CNC", h)
        return list(out.values())

    # ---- streaming (free): SmartAPI WebSocket 2.0, SNAP_QUOTE mode (LTP, volume, OHLC, 5-level depth) ----
    @staticmethod
    def parse_tick(m: dict, key: str, scale: float = 100.0) -> Quote | None:
        """Prices arrive in paise. The SDK's buy/sell book labels have been swapped in some versions, so the
        lower of the two top-of-book prices is taken as the bid and the higher as the ask."""
        ltp = m.get("last_traded_price")
        if ltp is None:
            return None

        def px(v):
            return float(v) / scale if v else None

        tops = [x for x in ((m.get("best_5_buy_data") or [])[:1] + (m.get("best_5_sell_data") or [])[:1]) if x.get("price")]
        bid = ask = bq = aq = None
        if len(tops) == 2:
            lo, hi = sorted(tops, key=lambda x: x["price"])
            bid, bq, ask, aq = lo["price"] / scale, float(lo["quantity"]), hi["price"] / scale, float(hi["quantity"])
        ts = m.get("exchange_timestamp")
        return Quote(key, now_ist(), float(ltp) / scale, float(m.get("volume_trade_for_the_day") or 0.0), bid, ask, bq, aq,
                     oi=fnum(m.get("open_interest")), upper_circuit=px(m.get("upper_circuit_limit")),
                     lower_circuit=px(m.get("lower_circuit_limit")), open=px(m.get("open_price_of_the_day")),
                     high=px(m.get("high_price_of_the_day")), low=px(m.get("low_price_of_the_day")),
                     prev_close=px(m.get("closed_price")),
                     exch_ts=datetime.fromtimestamp(ts / 1000, IST) if ts else None, source="angelone")

    def _new_session(self) -> dict:
        from SmartApi.smartWebSocketV2 import SmartWebSocketV2
        sws = SmartWebSocketV2(self._jwt, self.cfg["api_key"], self.cfg["client_code"], self._feed_token,
                               max_retry_attempt=5)
        sess = {"ws": sws, "tokens": [], "open": False}

        def on_open(wsapp):
            sess["open"] = True
            groups: dict = {}
            for et, t in sess["tokens"]:
                groups.setdefault(et, []).append(t)
            if groups:
                sws.subscribe("nsealgo", 3, [{"exchangeType": et, "tokens": ts} for et, ts in groups.items()])

        def on_data(wsapp, msg):
            if isinstance(msg, dict):
                k = self._tokmap.get((msg.get("exchange_type"), str(msg.get("token"))))
                q = self.parse_tick(msg, k) if k else None
                if q:
                    self._on_quote(q)

        def on_close(*_a):
            sess["open"] = False

        sws.on_open, sws.on_data, sws.on_close = on_open, on_data, on_close
        sws.on_error = lambda *a: self.log.warning("websocket error: %s", a[-1] if a else "")
        return sess

    def stream(self, keys, on_quote):
        self._on_quote = on_quote
        if not hasattr(self, "_sessions"):
            self._sessions, self._tokmap = [], {}
        added: dict = {}
        for k in keys:
            inst = self.instrument(k)
            tk = (1, str(inst.token))
            if tk in self._tokmap:
                continue
            sess = next((x for x in self._sessions if len(x["tokens"]) < self.WS_PER_SESSION), None)
            if sess is None:
                if len(self._sessions) >= self.WS_SESSIONS:
                    self.log.warning("Angel One stream capacity reached; %s not streamed", k)
                    continue
                sess = self._new_session()
                self._sessions.append(sess)
                threading.Thread(target=sess["ws"].connect, daemon=True, name="angel-ws").start()
            sess["tokens"].append(tk)
            self._tokmap[tk] = k
            added.setdefault(id(sess), (sess, []))[1].append(tk)
        for sess, tks in added.values():
            if sess["open"]:
                groups: dict = {}
                for et, t in tks:
                    groups.setdefault(et, []).append(t)
                sess["ws"].subscribe("nsealgo", 3, [{"exchangeType": et, "tokens": ts} for et, ts in groups.items()])

    def stop_stream(self):
        for sess in getattr(self, "_sessions", []):
            try:
                sess["ws"].close_connection()
            except Exception:
                pass
