"""Groww Trade API (growwapi). Beta. Note: growwapi requires pandas<3."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

from datetime import datetime, time

import pandas as pd

from ..core.models import BrokerOrder, BrokerPosition, OrderType, Side, Status, eq, idx
from .base import (AuthError, BrokerAdapter, BrokerError, date_spans, fnum, inum, pick, quote_from_depth)


class GrowwAdapter(BrokerAdapter):
    name = "groww"
    quote_batch = 1
    min_intervals = {"quote": 0.15, "history": 0.3, "order": 0.15, "orders": 0.5}
    STATUS_MAP = {"NEW": Status.NEW, "ACKED": Status.OPEN, "OPEN": Status.OPEN, "APPROVED": Status.OPEN,
                  "TRIGGER_PENDING": Status.TRIGGER_PENDING, "REJECTED": Status.REJECTED, "FAILED": Status.REJECTED,
                  "EXECUTED": Status.FILLED, "COMPLETED": Status.FILLED, "DELIVERY_AWAITED": Status.FILLED,
                  "CANCELLED": Status.CANCELLED, "CANCELLATION_REQUESTED": Status.OPEN,
                  "MODIFICATION_REQUESTED": Status.OPEN, "PARTIALLY_EXECUTED": Status.PARTIAL}
    OT = {OrderType.LIMIT: "LIMIT", OrderType.MARKET: "MARKET", OrderType.SL: "SL", OrderType.SLM: "SL_M"}

    def login(self, interactive: bool = True) -> None:
        try:
            from growwapi import GrowwAPI
        except ImportError as ex:
            raise BrokerError('pip install growwapi "pandas<3"') from ex
        c = self.cfg
        sess = self.load_session()
        if sess is None:
            if c.get("totp_secret"):
                import pyotp
                resp = GrowwAPI.get_access_token(api_key=c["api_key"], totp=pyotp.TOTP(c["totp_secret"]).now())
            else:
                resp = GrowwAPI.get_access_token(api_key=c["api_key"], secret=c["api_secret"])
            token = resp if isinstance(resp, str) else pick(resp or {}, "token", "access_token")
            if not token:
                raise AuthError(f"Groww token failed: {resp}")
            sess = {"access_token": token}
            self.save_session(sess)
        self._client = GrowwAPI(sess["access_token"])

    def _fetch_master(self) -> pd.DataFrame:
        df = self._client.get_all_instruments()
        rows = []
        for _, r in df.iterrows():
            d = r.to_dict()
            if str(d.get("exchange")) != "NSE":
                continue
            it, seg, sym = str(d.get("instrument_type")), str(d.get("segment")), str(d.get("trading_symbol"))
            if seg == "CASH" and it == "EQ" and str(d.get("series", "EQ")) in ("EQ", "nan"):
                rows.append({"key": eq(sym), "exchange": "NSE", "kind": "EQ", "symbol": sym, "broker_symbol": sym,
                             "token": str(d.get("exchange_token")), "lot_size": d.get("lot_size", 1),
                             "tick_size": d.get("tick_size", 0.05)})
            elif it == "IDX":
                rows.append({"key": idx(sym), "exchange": "NSE", "kind": "IDX", "symbol": sym, "broker_symbol": sym,
                             "token": str(d.get("exchange_token"))})
        return pd.DataFrame(rows)

    def _seg(self, inst) -> str:
        return "CASH"

    def get_quotes(self, keys):
        out = []
        for k in keys:
            inst = self.instrument(k)
            self.throttle("quote")
            d = self._client.get_quote(trading_symbol=inst.broker_symbol, exchange="NSE", segment=self._seg(inst)) or {}
            if d.get("last_price") is None:
                continue
            o = d.get("ohlc") or {}
            if isinstance(o, str):
                o = {}
            q = quote_from_depth(k, d["last_price"], d.get("volume"), d.get("depth"), oi=fnum(d.get("open_interest")),
                                 upper_circuit=fnum(d.get("upper_circuit_limit")),
                                 lower_circuit=fnum(d.get("lower_circuit_limit")), open=fnum(o.get("open")),
                                 high=fnum(o.get("high")), low=fnum(o.get("low")), prev_close=fnum(o.get("close")))
            q.bid = q.bid or fnum(d.get("bid_price"))
            q.ask = q.ask or fnum(d.get("offer_price"))
            out.append(q)
        return out

    def get_history(self, key, interval, start, end):
        inst = self.instrument(key)
        mins, span = (1, 7) if interval == "1m" else (1440, 180)
        rows = []
        for s, e in date_spans(start, end, span):
            self.throttle("history")
            resp = self._client.get_historical_candle_data(
                trading_symbol=inst.broker_symbol, exchange="NSE", segment=self._seg(inst),
                start_time=datetime.combine(s, time(9, 15)).strftime("%Y-%m-%d %H:%M:%S"),
                end_time=datetime.combine(e, time(15, 30)).strftime("%Y-%m-%d %H:%M:%S"), interval_in_minutes=mins) or {}
            rows += [tuple(c[:6]) for c in resp.get("candles") or []]
        return self.candles_df(rows, interval)

    def place_order(self, req, client_id):
        inst = self.instrument(req.key)
        self.throttle("order")
        resp = self._client.place_order(validity="DAY", exchange="NSE", order_type=self.OT[req.order_type],
                                        product=req.product.value, quantity=int(req.qty), segment=self._seg(inst),
                                        trading_symbol=inst.broker_symbol, transaction_type=req.side.value,
                                        order_reference_id=client_id, price=float(req.price or 0.0),
                                        trigger_price=float(req.trigger) if req.trigger else None) or {}
        oid = pick(resp, "groww_order_id")
        if not oid:
            raise BrokerError(f"Groww place_order: {resp}")
        self._order_cache[str(oid)] = {"seg": self._seg(inst), "qty": int(req.qty), "price": req.price,
                                       "trigger": req.trigger, "type": self.OT[req.order_type]}
        return str(oid)

    def modify_order(self, broker_id, *, price=None, trigger=None, qty=None, order_type=None):
        c = self._order_cache.get(broker_id)
        if not c:
            raise BrokerError("unknown order (placed by another session?)")
        if order_type is not None:
            c["type"] = self.OT[OrderType(order_type)]
        c.update({k: v for k, v in (("price", price), ("trigger", trigger), ("qty", qty)) if v is not None})
        self.throttle("order")
        self._client.modify_order(order_type=c["type"], segment=c["seg"], groww_order_id=broker_id,
                                  quantity=int(c["qty"]), price=float(c["price"] or 0.0),
                                  trigger_price=float(c["trigger"]) if c["trigger"] and c["type"] in ("SL", "SL_M") else None)

    def cancel_order(self, broker_id):
        seg = (self._order_cache.get(broker_id) or {}).get("seg", "CASH")
        self.throttle("order")
        self._client.cancel_order(groww_order_id=broker_id, segment=seg)

    def get_orders(self):
        self.throttle("orders")
        out = []
        for r in (self._client.get_order_list(page=0, page_size=100) or {}).get("order_list") or []:
            out.append(BrokerOrder(str(r.get("groww_order_id")), self.map_status(r.get("order_status")),
                                   inum(r.get("filled_quantity")), fnum(r.get("average_fill_price"), 0.0),
                                   self.key_for(symbol=r.get("trading_symbol")) or "",
                                   Side(r["transaction_type"]) if r.get("transaction_type") in ("BUY", "SELL") else None,
                                   inum(r.get("quantity")), str(r.get("order_reference_id") or ""),
                                   str(r.get("remark") or ""), r))
        return out

    def get_positions(self):
        self.throttle("orders")
        out: dict = {}
        for r in (self._client.get_positions_for_user() or {}).get("positions") or []:
            k = self.key_for(symbol=r.get("trading_symbol"))
            if k:
                p = str(r.get("product", ""))
                out[(k, p)] = BrokerPosition(k, inum(r.get("quantity")), fnum(r.get("net_price"), 0.0), p, r)
        try:
            for h in (self._client.get_holdings_for_user() or {}).get("holdings") or []:
                k = self.key_for(symbol=h.get("trading_symbol"))
                q = inum(h.get("quantity"))
                if k and q:
                    prev = out.get((k, "CNC"))
                    out[(k, "CNC")] = BrokerPosition(k, q + (prev.qty if prev else 0), fnum(h.get("average_price"), 0.0),
                                                     "CNC", h)
        except Exception as ex:
            self.log.debug("holdings unavailable: %s", ex)
        return list(out.values())
