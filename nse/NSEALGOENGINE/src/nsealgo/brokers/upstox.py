"""Upstox API v2/v3 (upstox-python-sdk). Polling quotes; v3 order endpoints.

Install note: the SDK pulls the obsolete PyPI 'uuid' package which breaks the stdlib on Python 3 ->
run `pip uninstall -y uuid` after installing (nsealgo doctor checks this).
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import gzip
import io
import json
from datetime import datetime
from urllib.parse import quote as urlquote

import pandas as pd
import requests

from ..core.clock import IST
from ..core.models import BrokerOrder, BrokerPosition, OrderType, Product, Quote, Side, Status, eq, idx
from .base import (AuthError, BrokerAdapter, BrokerError, as_dict, chunks, date_spans, fnum, inum, now_ist, pick,
                   quote_from_depth)

MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


D30_LIMIT = 50                     # Upstox Plus: full_d30 (30-level depth) for at most 50 instruments per user


class UpstoxAdapter(BrokerAdapter):
    name = "upstox"
    supports_streaming = True
    stream_capacity = 1500          # Market Data Feed V3 'full' mode: 2,000 keys (1,500 when modes are combined)
    quote_batch = 400
    min_intervals = {"quote": 1.0, "history": 0.35, "order": 0.12, "orders": 0.5}
    STATUS_MAP = {
        "OPEN": Status.OPEN, "COMPLETE": Status.FILLED, "CANCELLED": Status.CANCELLED, "REJECTED": Status.REJECTED,
        "TRIGGER PENDING": Status.TRIGGER_PENDING, "PUT ORDER REQ RECEIVED": Status.NEW,
        "VALIDATION PENDING": Status.NEW, "OPEN PENDING": Status.NEW, "MODIFY PENDING": Status.OPEN,
        "CANCEL PENDING": Status.OPEN, "AFTER MARKET ORDER REQ RECEIVED": Status.OPEN, "MODIFIED": Status.OPEN,
        "NOT CANCELLED": Status.OPEN, "NOT MODIFIED": Status.OPEN,
    }
    PRODUCT = {Product.MIS: "I", Product.CNC: "D"}
    OT = {OrderType.LIMIT: "LIMIT", OrderType.MARKET: "MARKET", OrderType.SL: "SL", OrderType.SLM: "SL-M"}

    def login(self, interactive: bool = True) -> None:
        try:
            import upstox_client
        except ImportError as ex:
            raise BrokerError("pip install upstox-python-sdk (then pip uninstall -y uuid)") from ex
        c = self.cfg
        sess = self.load_session()
        if sess is None:
            if not interactive:
                raise AuthError("no Upstox session for today; run: nsealgo login upstox")
            url = ("https://api.upstox.com/v2/login/authorization/dialog?response_type=code"
                   f"&client_id={c['api_key']}&redirect_uri={urlquote(c['redirect_uri'], safe='')}")
            code = self.oauth_capture(url, "code", c["redirect_uri"])
            resp = upstox_client.LoginApi().token("2.0", code=code, client_id=c["api_key"], client_secret=c["api_secret"],
                                                  redirect_uri=c["redirect_uri"], grant_type="authorization_code")
            sess = {"access_token": as_dict(resp).get("access_token") or getattr(resp, "access_token", None)}
            if not sess["access_token"]:
                raise AuthError(f"Upstox token exchange failed: {resp}")
            self.save_session(sess)
        conf = upstox_client.Configuration()
        conf.access_token = sess["access_token"]
        self._up = upstox_client
        self._client = upstox_client.ApiClient(conf)

    def _fetch_master(self) -> pd.DataFrame:
        raw = requests.get(MASTER_URL, timeout=60).content
        data = json.load(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), encoding="utf-8"))
        div = 100.0 if self.cfg.get("tick_in_paise", True) else 1.0
        rows = []
        for r in data:
            seg, it = r.get("segment"), r.get("instrument_type")
            tick = (fnum(r.get("tick_size"), 5.0) or 5.0) / div
            if seg == "NSE_EQ" and it == "EQ":
                rows.append({"key": eq(r["trading_symbol"]), "exchange": "NSE", "kind": "EQ", "symbol": r["trading_symbol"],
                             "broker_symbol": r["trading_symbol"], "token": r["instrument_key"],
                             "lot_size": r.get("lot_size", 1), "tick_size": tick, "name": r.get("name", "")})
            elif seg == "NSE_INDEX":
                nm = r.get("trading_symbol") or r.get("name")
                rows.append({"key": idx(nm), "exchange": "NSE", "kind": "IDX", "symbol": nm, "broker_symbol": nm,
                             "token": r["instrument_key"]})
        return pd.DataFrame(rows)

    def get_quotes(self, keys):
        api = self._up.MarketQuoteApi(self._client)
        tok = {self.instrument(k).token: k for k in keys}
        out = []
        for batch in chunks(list(tok), self.quote_batch):
            self.throttle("quote")
            data = as_dict(api.get_full_market_quote(",".join(batch), "2.0")).get("data") or {}
            for d in data.values():
                d = as_dict(d)
                k = tok.get(d.get("instrument_token"))
                if not k or d.get("last_price") is None:
                    continue
                o = d.get("ohlc") or {}
                out.append(quote_from_depth(k, d["last_price"], d.get("volume"), d.get("depth"), oi=d.get("oi"),
                                            upper_circuit=d.get("upper_circuit_limit"),
                                            lower_circuit=d.get("lower_circuit_limit"), open=o.get("open"),
                                            high=o.get("high"), low=o.get("low"), prev_close=o.get("close")))
        return out

    def get_history(self, key, interval, start, end):
        api = self._up.HistoryV3Api(self._client)
        tok = self.instrument(key).token
        unit, span = ("minutes", 28) if interval == "1m" else ("days", 3000)
        rows = []
        for s, e in date_spans(start, end, span):
            self.throttle("history")
            resp = as_dict(api.get_historical_candle_data1(tok, unit, "1", e.isoformat(), s.isoformat()))
            rows += [tuple(c[:6]) for c in ((resp.get("data") or {}).get("candles") or [])]
        return self.candles_df(rows, interval)

    def place_order(self, req, client_id):
        inst = self.instrument(req.key)
        body = self._up.PlaceOrderV3Request(quantity=int(req.qty), product=self.PRODUCT[req.product], validity="DAY",
                                            price=float(req.price or 0), tag=client_id, instrument_token=inst.token,
                                            order_type=self.OT[req.order_type], transaction_type=req.side.value,
                                            disclosed_quantity=0, trigger_price=float(req.trigger or 0), is_amo=False,
                                            slice=False)
        self.throttle("order")
        try:
            resp = as_dict(self._up.OrderApiV3(self._client).place_order(body))
        except Exception as ex:
            raise BrokerError(str(ex)) from ex
        ids = (resp.get("data") or {}).get("order_ids") or []
        if not ids:
            raise BrokerError(f"Upstox place_order: {resp}")
        self._order_cache[str(ids[0])] = {"qty": int(req.qty), "price": req.price, "type": self.OT[req.order_type],
                                          "trigger": req.trigger}
        return str(ids[0])

    def modify_order(self, broker_id, *, price=None, trigger=None, qty=None, order_type=None):
        c = self._order_cache.setdefault(broker_id, {"qty": qty or 0, "price": price, "type": "LIMIT", "trigger": trigger})
        if order_type is not None:
            c["type"] = self.OT[OrderType(order_type)]
        c.update({k: v for k, v in (("price", price), ("trigger", trigger), ("qty", qty)) if v is not None})
        body = self._up.ModifyOrderRequest(quantity=int(c["qty"]), validity="DAY", price=float(c["price"] or 0),
                                           order_id=broker_id, order_type=c["type"], disclosed_quantity=0,
                                           trigger_price=float(c["trigger"] or 0))
        self.throttle("order")
        try:
            self._up.OrderApiV3(self._client).modify_order(body)
        except Exception as ex:
            raise BrokerError(str(ex)) from ex

    def cancel_order(self, broker_id):
        self.throttle("order")
        try:
            self._up.OrderApiV3(self._client).cancel_order(broker_id)
        except Exception as ex:
            raise BrokerError(str(ex)) from ex

    def get_orders(self):
        self.throttle("orders")
        data = as_dict(self._up.OrderApi(self._client).get_order_book("2.0")).get("data") or []
        out = []
        for r in map(as_dict, data):
            out.append(BrokerOrder(str(r.get("order_id")), self.map_status(r.get("status")), inum(r.get("filled_quantity")),
                                   fnum(r.get("average_price"), 0.0), self.key_for(token=r.get("instrument_token")) or "",
                                   Side(r["transaction_type"]) if r.get("transaction_type") in ("BUY", "SELL") else None,
                                   inum(r.get("quantity")), r.get("tag") or "", r.get("status_message") or "", r))
        return out

    def get_positions(self):
        self.throttle("orders")
        api = self._up.PortfolioApi(self._client)
        out: dict = {}
        for r in map(as_dict, as_dict(api.get_positions("2.0")).get("data") or []):
            k = self.key_for(token=r.get("instrument_token"))
            if k:
                prod = {"I": "MIS", "D": "CNC"}.get(r.get("product"), r.get("product"))
                out[(k, prod)] = BrokerPosition(k, inum(r.get("quantity")), fnum(pick(r, "average_price", "buy_price"), 0.0),
                                                prod, r)
        for h in map(as_dict, as_dict(api.get_holdings("2.0")).get("data") or []):
            k = self.key_for(token=h.get("instrument_token"))
            q = inum(h.get("quantity")) + inum(h.get("t1_quantity"))
            if k and q:
                prev = out.get((k, "CNC"))
                out[(k, "CNC")] = BrokerPosition(k, q + (prev.qty if prev else 0), fnum(h.get("average_price"), 0.0), "CNC", h)
        return list(out.values())

    # ---- streaming (free): Market Data Feed V3 via the SDK's MarketDataStreamerV3 (protobuf decoded to dicts) ----
    @staticmethod
    def parse_feed(feed: dict, key: str) -> Quote | None:
        ff = feed.get("fullFeed") or {}
        node = ff.get("marketFF") or ff.get("indexFF") or {}
        ltpc = node.get("ltpc") or feed.get("ltpc") or {}
        if ltpc.get("ltp") is None:
            return None
        levels = (node.get("marketLevel") or {}).get("bidAskQuote") or []
        bids = tuple((p, fnum(x.get("bidQ"), 0.0)) for x in levels if (p := fnum(x.get("bidP"))))
        asks = tuple((p, fnum(x.get("askQ"), 0.0)) for x in levels if (p := fnum(x.get("askP"))))
        lvl = levels[0] if levels else {}
        day = next((o for o in ((node.get("marketOHLC") or {}).get("ohlc") or []) if o.get("interval") == "1d"), {})
        ltt = fnum(ltpc.get("ltt"))
        return Quote(key, now_ist(), float(ltpc["ltp"]), fnum(node.get("vtt"), 0.0) or fnum(day.get("vol"), 0.0) or 0.0,
                     fnum(lvl.get("bidP")) or None, fnum(lvl.get("askP")) or None, fnum(lvl.get("bidQ")),
                     fnum(lvl.get("askQ")), oi=fnum(node.get("oi")), open=fnum(day.get("open")),
                     high=fnum(day.get("high")), low=fnum(day.get("low")), prev_close=fnum(ltpc.get("cp")),
                     exch_ts=datetime.fromtimestamp(ltt / 1000, IST) if ltt else None, source="upstox",
                     bids=bids, asks=asks)

    def depth_keys(self) -> list[str]:
        """Stocks to stream with 30-level depth (Upstox Plus `full_d30`: at most 50 instruments per user);
        every other instrument streams in `full` mode with 5 levels."""
        d = self.cfg.get("depth") or {}
        if str(d.get("mode", "full")) != "full_d30":
            return []
        keys = list(dict.fromkeys(k if ":" in k else f"NSE:{str(k).strip().upper()}" for k in (d.get("symbols") or [])))
        if len(keys) > D30_LIMIT:
            self.log.warning("upstox full_d30 allows %d instruments per user; using the first %d of %d",
                             D30_LIMIT, D30_LIMIT, len(keys))
        return keys[:D30_LIMIT]

    def stream(self, keys, on_quote):
        if not hasattr(self, "_d30_want"):
            self._d30_want, self._d30_live = set(self.depth_keys()), set()
        new = {self.instrument(k).token: k for k in keys}
        room = max(D30_LIMIT - len(self._d30_live), 0)
        d30 = [t for t, k in new.items() if k in self._d30_want and t not in self._d30_live][:room]
        self._d30_live.update(d30)
        full = [t for t in new if t not in self._d30_live]
        if getattr(self, "_ws", None) is not None:
            self._ikeys.update(new)
            if full:
                self._ws.subscribe(full, "full")
            if d30:
                self._ws.subscribe(d30, "full_d30")
            return
        self._ikeys = dict(new)

        def on_message(msg):
            for ikey, feed in ((msg or {}).get("feeds") or {}).items():
                k = self._ikeys.get(ikey)
                q = self.parse_feed(feed, k) if k else None
                if q:
                    on_quote(q)

        first, mode, later = (full, "full", d30) if full else (d30, "full_d30", [])
        streamer = self._up.MarketDataStreamerV3(self._client, list(first), mode)
        streamer.on("message", on_message)
        streamer.on("error", lambda e: self.log.warning("websocket error: %s", e))
        if later:
            streamer.on("open", lambda *a: streamer.subscribe(list(later), "full_d30"))
        streamer.auto_reconnect(True, 5, 30)
        streamer.connect()
        self._ws = streamer

    def set_depth_focus(self, keys) -> None:
        """Move the 50 `full_d30` slots to the first 50 subscribed instruments of `keys` (positions first,
        then the strategies' candidates); instruments that lose a slot go back to 5-level `full`."""
        if str((self.cfg.get("depth") or {}).get("mode", "full")) != "full_d30" or getattr(self, "_ws", None) is None:
            return
        tok = {k: t for t, k in self._ikeys.items()}
        want = list(dict.fromkeys(tok[k] for k in keys if k in tok))[:D30_LIMIT]
        if not want:
            return                                       # nothing to focus on: keep the current slots
        down = sorted(self._d30_live - set(want))
        up = [t for t in want if t not in self._d30_live]
        switch = getattr(self._ws, "change_mode", None) or self._ws.subscribe
        if down:
            switch(down, "full")
            self._d30_live -= set(down)
        if up:
            switch(up, "full_d30")
            self._d30_live |= set(up)

    def stop_stream(self):
        if getattr(self, "_ws", None) is not None:
            try:
                self._ws.disconnect()
            except Exception:
                pass
