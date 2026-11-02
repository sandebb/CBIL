"""Alice Blue ANT (pya3). Beta."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import json
import os
import threading
from datetime import datetime, time

import pandas as pd

from ..core.models import BrokerOrder, BrokerPosition, OrderType, Product, Quote, Side, Status, eq
from .base import (AuthError, BrokerAdapter, BrokerError, date_spans, fnum, inum, noren_quote, now_ist, pick)


class AliceBlueAdapter(BrokerAdapter):
    name = "aliceblue"
    supports_streaming = True
    stream_capacity = 1000
    quote_batch = 1
    min_intervals = {"quote": 0.2, "history": 0.5, "order": 0.2, "orders": 0.6}
    STATUS_MAP = {"OPEN": Status.OPEN, "COMPLETE": Status.FILLED, "CANCELLED": Status.CANCELLED,
                  "REJECTED": Status.REJECTED, "TRIGGER PENDING": Status.TRIGGER_PENDING,
                  "AFTER MARKET ORDER REQ RECEIVED": Status.OPEN, "MODIFIED": Status.OPEN, "OPEN PENDING": Status.NEW,
                  "VALIDATION PENDING": Status.NEW, "PUT ORDER REQ RECEIVED": Status.NEW}

    def login(self, interactive: bool = True) -> None:
        try:
            import pya3
        except ImportError as ex:
            raise BrokerError("pip install pya3") from ex
        self._pya3 = pya3
        alice = pya3.Aliceblue(user_id=self.cfg["user_id"], api_key=self.cfg["api_key"])
        sess = alice.get_session_id()
        if not sess or (isinstance(sess, dict) and sess.get("stat") == "Not_ok"):
            raise AuthError(f"Alice Blue session failed: {sess}")
        self._client = alice
        self._ins: dict = {}

    def _fetch_master(self) -> pd.DataFrame:
        cwd = os.getcwd()
        os.chdir(self.cache_dir)
        try:
            for ex in ("NSE",):
                self._client.get_contract_master(ex)
            nse = pd.read_csv("NSE.csv", dtype=str)
        finally:
            os.chdir(cwd)
        rows = []
        for _, r in nse.iterrows():
            ts = str(r.get("Trading Symbol", ""))
            if ts.endswith("-EQ"):
                rows.append({"key": eq(r["Symbol"]), "exchange": "NSE", "kind": "EQ", "symbol": r["Symbol"],
                             "broker_symbol": ts, "token": r["Token"], "lot_size": r.get("Lot Size", 1),
                             "tick_size": r.get("Tick Size", 0.05)})
        return pd.DataFrame(rows)

    def _instrument_obj(self, key):
        if key not in self._ins:
            inst = self.instrument(key)
            self._ins[key] = self._client.get_instrument_by_token(self.exch(inst), int(inst.token))
        return self._ins[key]

    def get_quotes(self, keys):
        out = []
        for k in keys:
            self.throttle("quote")
            d = self._client.get_scrip_info(self._instrument_obj(k)) or {}
            ltp = fnum(pick(d, "LTP", "Ltp", "ltp"))
            if ltp:
                out.append(Quote(k, now_ist(), ltp, fnum(pick(d, "TradeVolume", "Volume", "v"), 0.0),
                                 fnum(pick(d, "BestBuyPrice1", "bp1")) or None, fnum(pick(d, "BestSellPrice1", "sp1")) or None,
                                 fnum(pick(d, "BestBuyQuantity1", "bq1")), fnum(pick(d, "BestSellQuantity1", "sq1")),
                                 open=fnum(pick(d, "openPrice", "Open")), high=fnum(pick(d, "High", "high")),
                                 low=fnum(pick(d, "Low", "low")), prev_close=fnum(pick(d, "PrvClose", "PrevClose"))))
        return out

    def get_history(self, key, interval, start, end):
        ins = self._instrument_obj(key)
        rows = []
        for s, e in date_spans(start, end, 30 if interval == "1m" else 1500):
            self.throttle("history")
            df = self._client.get_historical(ins, datetime.combine(s, time(9, 0)), datetime.combine(e, time(15, 30)),
                                             "1" if interval == "1m" else "D")
            if isinstance(df, pd.DataFrame) and not df.empty:
                c = {x.lower(): x for x in df.columns}
                rows += list(zip(df[c["datetime"]], df[c["open"]], df[c["high"]], df[c["low"]], df[c["close"]],
                                 df[c["volume"]]))
        return self.candles_df(rows, interval)

    def _enums(self, req):
        p = self._pya3
        tt = p.TransactionType.Buy if req.side == Side.BUY else p.TransactionType.Sell
        ot = {OrderType.LIMIT: p.OrderType.Limit, OrderType.MARKET: p.OrderType.Market,
              OrderType.SL: p.OrderType.StopLossLimit, OrderType.SLM: p.OrderType.StopLossMarket}[req.order_type]
        pt = {Product.MIS: p.ProductType.Intraday, Product.CNC: p.ProductType.Delivery}[req.product]
        return tt, ot, pt

    def place_order(self, req, client_id):
        tt, ot, pt = self._enums(req)
        self.throttle("order")
        resp = self._client.place_order(transaction_type=tt, instrument=self._instrument_obj(req.key),
                                        quantity=int(req.qty), order_type=ot, product_type=pt,
                                        price=float(req.price or 0.0), trigger_price=float(req.trigger) if req.trigger else None,
                                        stop_loss=None, square_off=None, trailing_sl=None, is_amo=False,
                                        order_tag=client_id) or {}
        oid = pick(resp, "NOrdNo", "nestOrderNumber")
        if not oid:
            raise BrokerError(f"Alice Blue place_order: {resp}")
        self._order_cache[str(oid)] = {"req": req}
        return str(oid)

    def modify_order(self, broker_id, *, price=None, trigger=None, qty=None, order_type=None):
        c = self._order_cache.get(broker_id)
        if not c:
            raise BrokerError("unknown order (placed by another session?)")
        req = c["req"]
        if order_type is not None:
            req.order_type = OrderType(order_type)
        tt, ot, pt = self._enums(req)
        self.throttle("order")
        self._client.modify_order(transaction_type=tt, instrument=self._instrument_obj(req.key), product_type=pt,
                                  order_id=broker_id, order_type=ot, quantity=int(qty or req.qty),
                                  price=float(price if price is not None else (req.price or 0.0)),
                                  trigger_price=float(trigger) if trigger else None)

    def cancel_order(self, broker_id):
        self.throttle("order")
        self._client.cancel_order(broker_id)

    def get_orders(self):
        self.throttle("orders")
        rows = self._client.order_data() or []
        out = []
        for r in rows if isinstance(rows, list) else []:
            out.append(BrokerOrder(str(pick(r, "Nstordno")), self.map_status(pick(r, "Status")), inum(pick(r, "Fillshares")),
                                   fnum(pick(r, "Avgprc"), 0.0), self.key_for(symbol=pick(r, "Trsym")) or "",
                                   Side.BUY if pick(r, "Trantype") == "B" else Side.SELL, inum(pick(r, "Qty")),
                                   str(pick(r, "remarks", default="")), str(pick(r, "RejReason", default="")), r))
        return out

    def get_positions(self):
        self.throttle("orders")
        rows = self._client.get_netwise_positions() or []
        out = []
        for r in rows if isinstance(rows, list) else []:
            k = self.key_for(token=pick(r, "Token")) or self.key_for(symbol=pick(r, "Tsym"))
            if k:
                out.append(BrokerPosition(k, inum(pick(r, "Netqty")), fnum(pick(r, "NetBuyavgprc", "Buyavgprc"), 0.0),
                                          str(pick(r, "Pcode", default="")), r))
        return out

    # ---- streaming (free): ANT websocket speaks the Noren protocol (tk/tf/dk/df snapshots) ----
    def stream(self, keys, on_quote):
        new = {}
        for k in keys:
            inst = self.instrument(k)
            new[(self.exch(inst), str(inst.token))] = k
        if getattr(self, "_atoks", None) is None:
            self._atoks, self._asnap, opened = {}, {}, threading.Event()

            def on_msg(message):
                try:
                    d = json.loads(message) if isinstance(message, (str, bytes)) else message
                except ValueError:
                    return
                if not isinstance(d, dict) or d.get("t") not in ("tk", "tf", "dk", "df"):
                    return
                tk = (str(d.get("e")), str(d.get("tk")))
                k = self._atoks.get(tk)
                if not k:
                    return
                snap = self._asnap.setdefault(tk, {})
                snap.update({x: v for x, v in d.items() if v not in (None, "")})
                if snap.get("lp") is not None:
                    on_quote(noren_quote(k, snap, "aliceblue"))

            self._atoks.update(new)
            self._client.start_websocket(socket_open_callback=opened.set, subscription_callback=on_msg,
                                         socket_close_callback=lambda: self.log.info("websocket closed"),
                                         socket_error_callback=lambda e: self.log.warning("websocket error: %s", e),
                                         run_in_background=True, market_depth=False)
            opened.wait(15)
        else:
            self._atoks.update(new)
        self._client.subscribe([self._instrument_obj(k) for k in new.values()])

    def stop_stream(self):
        try:
            self._client.stop_websocket()
        except Exception:
            pass
