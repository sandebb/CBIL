"""Zerodha Kite Connect (kiteconnect v5). Streaming via KiteTicker in full mode (5-level depth)."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

from datetime import datetime

import pandas as pd

from ..core.clock import IST
from ..core.models import BrokerOrder, BrokerPosition, OrderType, Side, Status, eq, idx
from .base import (AuthError, BrokerAdapter, BrokerError, chunks, date_spans, fnum, inum, 
                   quote_from_depth)


class ZerodhaAdapter(BrokerAdapter):
    name = "zerodha"
    supports_streaming = True
    stream_capacity = 3000
    quote_batch = 500
    min_intervals = {"quote": 1.0, "history": 0.34, "order": 0.1, "orders": 0.5}
    STATUS_MAP = {
        "OPEN": Status.OPEN, "COMPLETE": Status.FILLED, "CANCELLED": Status.CANCELLED, "REJECTED": Status.REJECTED,
        "TRIGGER PENDING": Status.TRIGGER_PENDING, "OPEN PENDING": Status.NEW, "VALIDATION PENDING": Status.NEW,
        "PUT ORDER REQ RECEIVED": Status.NEW, "MODIFY PENDING": Status.OPEN, "MODIFY VALIDATION PENDING": Status.OPEN,
        "CANCEL PENDING": Status.OPEN, "AMO REQ RECEIVED": Status.OPEN, "MODIFIED": Status.OPEN,
    }

    def login(self, interactive: bool = True) -> None:
        try:
            from kiteconnect import KiteConnect
        except ImportError as ex:
            raise BrokerError("pip install kiteconnect") from ex
        kite = KiteConnect(api_key=self.cfg["api_key"])
        sess = self.load_session()
        if sess is None:
            if not interactive:
                raise AuthError("no Kite session for today; run: nsealgo login zerodha")
            token = self.oauth_capture(kite.login_url(), "request_token", self.cfg.get("redirect_uri"))
            data = kite.generate_session(token, api_secret=self.cfg["api_secret"])
            sess = {"access_token": data["access_token"]}
            self.save_session(sess)
        kite.set_access_token(sess["access_token"])
        self._client, self._token = kite, sess["access_token"]

    def _fetch_master(self) -> pd.DataFrame:
        rows = []
        for exch in ("NSE",):
            for r in self._client.instruments(exch):
                seg, it = r.get("segment"), r.get("instrument_type")
                if exch == "NSE" and seg == "NSE" and it == "EQ":
                    key, kind, sym = eq(r["tradingsymbol"]), "EQ", r["tradingsymbol"]
                elif exch == "NSE" and seg == "INDICES":
                    key, kind, sym = idx(r["tradingsymbol"]), "IDX", r["tradingsymbol"]
                else:
                    continue
                rows.append({"key": key, "exchange": exch, "kind": kind, "symbol": sym, "broker_symbol": r["tradingsymbol"],
                             "token": str(r["instrument_token"]), "lot_size": r.get("lot_size") or 1,
                             "tick_size": r.get("tick_size") or 0.05,
                             "expiry": None, "name": r.get("name") or ""})
        return pd.DataFrame(rows)

    def _name(self, key: str) -> str:
        inst = self.instrument(key)
        return f"{self.exch(inst)}:{inst.broker_symbol}"

    def get_quotes(self, keys):
        names = {self._name(k): k for k in keys}
        out = []
        for batch in chunks(list(names), self.quote_batch):
            self.throttle("quote")
            data = self._client.quote(batch) or {}
            for n, d in data.items():
                k = names.get(n)
                if not k or d.get("last_price") is None:
                    continue
                o = d.get("ohlc") or {}
                out.append(quote_from_depth(k, d["last_price"], d.get("volume"), d.get("depth"), oi=d.get("oi"),
                                            upper_circuit=d.get("upper_circuit_limit"),
                                            lower_circuit=d.get("lower_circuit_limit"), open=o.get("open"),
                                            high=o.get("high"), low=o.get("low"), prev_close=o.get("close")))
        return out

    def get_history(self, key, interval, start, end):
        inst = self.instrument(key)
        iv, span = ("minute", 60) if interval == "1m" else ("day", 2000)
        rows = []
        for s, e in date_spans(start, end, span):
            self.throttle("history")
            for r in self._client.historical_data(int(inst.token), s, e, iv) or []:
                rows.append((r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"]))
        return self.candles_df(rows, interval)

    def place_order(self, req, client_id):
        inst = self.instrument(req.key)
        kw = dict(variety="regular", exchange=self.exch(inst), tradingsymbol=inst.broker_symbol,
                  transaction_type=req.side.value, quantity=int(req.qty), product=req.product.value,
                  order_type=req.order_type.value, validity=req.validity, tag=client_id[:20])
        if req.order_type in (OrderType.LIMIT, OrderType.SL):
            kw["price"] = req.price
        if req.order_type in (OrderType.SL, OrderType.SLM):
            kw["trigger_price"] = req.trigger
        if req.order_type in (OrderType.MARKET, OrderType.SLM):
            # mandatory for API orders since 1 Apr 2026; 0 is rejected, -1 = automatic within exchange LPP ranges
            kw["market_protection"] = self.cfg.get("market_protection", -1) or -1
        if self.cfg.get("algo_id"):
            kw["algo_id"] = self.cfg["algo_id"]
        self.throttle("order")
        try:
            return str(self._client.place_order(**kw))
        except TypeError as ex:
            if "market_protection" in str(ex):
                raise BrokerError("installed kiteconnect SDK lacks market_protection: pip install -U kiteconnect") from ex
            raise
        except Exception as ex:
            if ex.__class__.__name__ in ("InputException", "OrderException", "TokenException", "PermissionException"):
                raise BrokerError(str(ex)) from ex
            raise

    def modify_order(self, broker_id, *, price=None, trigger=None, qty=None, order_type=None):
        kw = {"variety": "regular", "order_id": broker_id}
        if price is not None:
            kw["price"] = price
        if trigger is not None:
            kw["trigger_price"] = trigger
        if qty:
            kw["quantity"] = int(qty)
        if order_type is not None:
            kw["order_type"] = self.ot_value(order_type)
        self.throttle("order")
        try:
            self._client.modify_order(**kw)
        except Exception as ex:
            raise BrokerError(str(ex)) from ex

    def cancel_order(self, broker_id):
        self.throttle("order")
        try:
            self._client.cancel_order(variety="regular", order_id=broker_id)
        except Exception as ex:
            raise BrokerError(str(ex)) from ex

    def get_orders(self):
        self.throttle("orders")
        out = []
        for r in self._client.orders() or []:
            out.append(BrokerOrder(str(r["order_id"]), self.map_status(r.get("status")), inum(r.get("filled_quantity")),
                                   fnum(r.get("average_price"), 0.0), self.key_for(symbol=r.get("tradingsymbol")) or "",
                                   Side(r["transaction_type"]) if r.get("transaction_type") in ("BUY", "SELL") else None,
                                   inum(r.get("quantity")), r.get("tag") or "", r.get("status_message") or "", r))
        return out

    def get_positions(self):
        self.throttle("orders")
        out: dict = {}
        for r in (self._client.positions() or {}).get("net", []):
            k = self.key_for(symbol=r.get("tradingsymbol"))
            if k:
                out[(k, r.get("product"))] = BrokerPosition(k, inum(r.get("quantity")), fnum(r.get("average_price"), 0.0),
                                                            r.get("product", ""), r)
        for h in self._client.holdings() or []:
            k = self.key_for(symbol=h.get("tradingsymbol"))
            q = inum(h.get("quantity")) + inum(h.get("t1_quantity"))
            if k and q:
                prev = out.get((k, "CNC"))
                out[(k, "CNC")] = BrokerPosition(k, q + (prev.qty if prev else 0), fnum(h.get("average_price"), 0.0), "CNC", h)
        return list(out.values())

    def get_funds(self):
        return (self._client.margins() or {}).get("equity", {})

    def stream(self, keys, on_quote):
        """Kite ticker (paid Kite Connect plan). Incremental: later calls add instruments to the same socket."""
        from kiteconnect import KiteTicker
        new = {int(self.instrument(k).token): k for k in keys}
        ws = getattr(self, "_ws", None)
        if ws is not None:
            self._tok.update(new)
            if ws.is_connected():
                ws.subscribe(list(new))
                ws.set_mode(ws.MODE_FULL, list(new))
            return
        self._tok = dict(new)
        ws = KiteTicker(self.cfg["api_key"], self._token)

        def on_ticks(_ws, ticks):
            for t in ticks:
                k = self._tok.get(t.get("instrument_token"))
                if not k or t.get("last_price") is None:
                    continue
                o, et = t.get("ohlc") or {}, t.get("exchange_timestamp")
                if isinstance(et, datetime) and et.tzinfo is None:
                    et = et.replace(tzinfo=IST)
                on_quote(quote_from_depth(k, t["last_price"], t.get("volume_traded"), t.get("depth"), oi=t.get("oi"),
                                          open=o.get("open"), high=o.get("high"), low=o.get("low"),
                                          prev_close=o.get("close"), exch_ts=et if isinstance(et, datetime) else None,
                                          source="zerodha"))

        def on_connect(_ws, _resp):   # also runs after reconnects: resubscribe everything
            _ws.subscribe(list(self._tok))
            _ws.set_mode(_ws.MODE_FULL, list(self._tok))

        ws.on_ticks, ws.on_connect = on_ticks, on_connect
        ws.connect(threaded=True)
        self._ws = ws

    def stop_stream(self):
        ws = getattr(self, "_ws", None)
        if ws:
            try:
                ws.close()
            except Exception:
                pass
