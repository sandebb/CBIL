"""Dhan (dhanhq v2). Access token generated on web.dhan.co (put it in .env); polling quotes with depth."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402



import pandas as pd

from ..core.models import BrokerOrder, BrokerPosition, OrderType, Product, Side, Status, eq, idx
from .base import (AuthError, BrokerAdapter, BrokerError, chunks, date_spans, fnum, inum, pick, quote_from_depth)

MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"


class DhanAdapter(BrokerAdapter):
    name = "dhan"
    quote_batch = 1000
    min_intervals = {"quote": 1.05, "history": 0.35, "order": 0.12, "orders": 0.5}
    STATUS_MAP = {"TRANSIT": Status.NEW, "PENDING": Status.OPEN, "REJECTED": Status.REJECTED,
                  "CANCELLED": Status.CANCELLED, "TRADED": Status.FILLED, "EXPIRED": Status.CANCELLED,
                  "PART_TRADED": Status.PARTIAL, "TRIGGERED": Status.OPEN, "CLOSED": Status.FILLED}
    PRODUCT = {Product.MIS: "INTRADAY", Product.CNC: "CNC"}
    OT = {OrderType.LIMIT: "LIMIT", OrderType.MARKET: "MARKET", OrderType.SL: "STOP_LOSS",
          OrderType.SLM: "STOP_LOSS_MARKET"}

    def login(self, interactive: bool = True) -> None:
        try:
            import dhanhq as dh
        except ImportError as ex:
            raise BrokerError("pip install dhanhq") from ex
        c = self.cfg
        if not c.get("access_token"):
            raise AuthError("set DHAN_ACCESS_TOKEN (generate it on web.dhan.co -> My Profile -> DhanHQ APIs)")
        if hasattr(dh, "DhanContext"):
            self._client = dh.dhanhq(dh.DhanContext(c["client_id"], c["access_token"]))
        else:
            self._client = dh.dhanhq(c["client_id"], c["access_token"])

    def _fetch_master(self) -> pd.DataFrame:
        df = pd.read_csv(MASTER_URL, dtype=str, low_memory=False)
        df = df[df["SEM_EXM_EXCH_ID"] == "NSE"]
        div = 100.0 if self.cfg.get("tick_in_paise", True) else 1.0
        rows = []
        for _, r in df.iterrows():
            it, ts = r.get("SEM_INSTRUMENT_NAME"), str(r.get("SEM_TRADING_SYMBOL"))
            tick = (fnum(r.get("SEM_TICK_SIZE"), 5.0) or 5.0) / div
            if it == "EQUITY" and r.get("SEM_SERIES") == "EQ":
                rows.append({"key": eq(ts), "exchange": "NSE", "kind": "EQ", "symbol": ts, "broker_symbol": ts,
                             "token": r["SEM_SMST_SECURITY_ID"], "lot_size": r.get("SEM_LOT_UNITS", 1), "tick_size": tick})
            elif it == "INDEX":
                rows.append({"key": idx(ts), "exchange": "NSE", "kind": "IDX", "symbol": ts, "broker_symbol": ts,
                             "token": r["SEM_SMST_SECURITY_ID"], "name": "INDEX"})
        return pd.DataFrame(rows)

    def _seg(self, inst) -> str:
        return {"IDX": "IDX_I"}.get(inst.kind, "NSE_EQ")

    def _itype(self, inst) -> str:
        return {"IDX": "INDEX"}.get(inst.kind, "EQUITY")

    @staticmethod
    def _data(resp):
        d = (resp or {}).get("data") if isinstance(resp, dict) else None
        return d.get("data", d) if isinstance(d, dict) and "data" in d else d

    def get_quotes(self, keys):
        seg_map: dict = {}
        for k in keys:
            inst = self.instrument(k)
            seg_map[(self._seg(inst), str(inst.token))] = k
        out = []
        for batch in chunks(list(seg_map), self.quote_batch):
            req: dict = {}
            for seg, t in batch:
                req.setdefault(seg, []).append(int(t))
            self.throttle("quote")
            data = self._data(self._client.quote_data(req)) or {}
            for seg, secs in data.items():
                for sid, d in (secs or {}).items():
                    k = seg_map.get((seg, str(sid)))
                    if k and d.get("last_price") is not None:
                        o = d.get("ohlc") or {}
                        out.append(quote_from_depth(k, d["last_price"], d.get("volume"), d.get("depth"), oi=d.get("oi"),
                                                    upper_circuit=d.get("upper_circuit_limit"),
                                                    lower_circuit=d.get("lower_circuit_limit"), open=o.get("open"),
                                                    high=o.get("high"), low=o.get("low"), prev_close=o.get("close")))
        return out

    def get_history(self, key, interval, start, end):
        inst = self.instrument(key)
        rows = []
        span = 60 if interval == "1m" else 3000
        for s, e in date_spans(start, end, span):
            self.throttle("history")
            if interval == "1m":
                resp = self._client.intraday_minute_data(str(inst.token), self._seg(inst), self._itype(inst),
                                                         f"{s:%Y-%m-%d} 09:15:00", f"{e:%Y-%m-%d} 15:30:00", 1)
            else:
                resp = self._client.historical_daily_data(str(inst.token), self._seg(inst), self._itype(inst),
                                                          s.isoformat(), e.isoformat())
            d = self._data(resp) or {}
            if isinstance(d, dict) and d.get("timestamp"):
                rows += list(zip(d["timestamp"], d["open"], d["high"], d["low"], d["close"], d["volume"]))
        return self.candles_df(rows, interval)

    def place_order(self, req, client_id):
        inst = self.instrument(req.key)
        self.throttle("order")
        resp = self._client.place_order(security_id=str(inst.token), exchange_segment=self._seg(inst),
                                        transaction_type=req.side.value, quantity=int(req.qty),
                                        order_type=self.OT[req.order_type], product_type=self.PRODUCT[req.product],
                                        price=float(req.price or 0), trigger_price=float(req.trigger or 0),
                                        validity="DAY", tag=client_id)
        d = self._data(resp) or {}
        oid = pick(d, "orderId")
        if (resp or {}).get("status") != "success" or not oid:
            raise BrokerError(f"Dhan place_order: {resp}")
        self._order_cache[str(oid)] = {"qty": int(req.qty), "price": req.price, "trigger": req.trigger,
                                       "type": self.OT[req.order_type]}
        return str(oid)

    def modify_order(self, broker_id, *, price=None, trigger=None, qty=None, order_type=None):
        c = self._order_cache.setdefault(broker_id, {"qty": qty or 0, "price": price, "trigger": trigger, "type": "LIMIT"})
        if order_type is not None:
            c["type"] = self.OT[OrderType(order_type)]
        c.update({k: v for k, v in (("price", price), ("trigger", trigger), ("qty", qty)) if v is not None})
        self.throttle("order")
        resp = self._client.modify_order(broker_id, c["type"], "", int(c["qty"]), float(c["price"] or 0),
                                         float(c["trigger"] or 0), 0, "DAY")
        if (resp or {}).get("status") != "success":
            raise BrokerError(f"Dhan modify: {resp}")

    def cancel_order(self, broker_id):
        self.throttle("order")
        resp = self._client.cancel_order(broker_id)
        if (resp or {}).get("status") != "success":
            raise BrokerError(f"Dhan cancel: {resp}")

    def get_orders(self):
        self.throttle("orders")
        out = []
        for r in self._data(self._client.get_order_list()) or []:
            out.append(BrokerOrder(str(r.get("orderId")), self.map_status(r.get("orderStatus")),
                                   inum(pick(r, "filledQty", "filled_qty")), fnum(r.get("averageTradedPrice"), 0.0),
                                   self.key_for(token=r.get("securityId")) or "",
                                   Side(r["transactionType"]) if r.get("transactionType") in ("BUY", "SELL") else None,
                                   inum(r.get("quantity")), r.get("correlationId") or "",
                                   r.get("omsErrorDescription") or "", r))
        return out

    def get_positions(self):
        self.throttle("orders")
        out: dict = {}
        pm = {"INTRADAY": "MIS", "CNC": "CNC"}
        for r in self._data(self._client.get_positions()) or []:
            k = self.key_for(token=r.get("securityId"))
            if k:
                p = pm.get(r.get("productType"), r.get("productType"))
                out[(k, p)] = BrokerPosition(k, inum(r.get("netQty")), fnum(pick(r, "costPrice", "buyAvg"), 0.0), p, r)
        for h in self._data(self._client.get_holdings()) or []:
            k = self.key_for(token=h.get("securityId"))
            q = inum(h.get("totalQty")) + inum(h.get("t1Qty"))
            if k and q:
                prev = out.get((k, "CNC"))
                out[(k, "CNC")] = BrokerPosition(k, q + (prev.qty if prev else 0), fnum(h.get("avgCostPrice"), 0.0), "CNC", h)
        return list(out.values())
