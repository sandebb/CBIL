"""5paisa (py5paisa). Beta: cash equities. TOTP session login."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402


import pandas as pd

from ..core.models import BrokerOrder, BrokerPosition, OrderType, Product, Quote, Side, Status, eq
from .base import (AuthError, BrokerAdapter, BrokerError, NotSupported, chunks, date_spans, fnum, inum, now_ist, pick)

MASTER_URL = "https://openapi.5paisa.com/VendorsAPI/Service1.svc/ScripMaster/segment/nse_eq"


class FivePaisaAdapter(BrokerAdapter):
    name = "fivepaisa"
    quote_batch = 50
    min_intervals = {"quote": 1.0, "history": 0.5, "order": 0.2, "orders": 0.6}
    STATUS_MAP = {"FULLY EXECUTED": Status.FILLED, "EXECUTED": Status.FILLED, "PENDING": Status.OPEN,
                  "PLACED": Status.OPEN, "XMITTED": Status.OPEN, "MODIFIED": Status.OPEN,
                  "PARTIALLY EXECUTED": Status.PARTIAL, "CANCELLED": Status.CANCELLED, "REJECTED": Status.REJECTED,
                  "REJECTED BY 5P": Status.REJECTED, "REJECTED BY EXCH": Status.REJECTED, "EXPIRED": Status.CANCELLED,
                  "TRIGGER PENDING": Status.TRIGGER_PENDING, "SL TRIGGERED": Status.OPEN}

    def login(self, interactive: bool = True) -> None:
        try:
            from py5paisa import FivePaisaClient
        except ImportError as ex:
            raise BrokerError("pip install py5paisa") from ex
        import pyotp
        c = self.cfg
        cred = {"APP_NAME": c["app_name"], "APP_SOURCE": c["app_source"], "USER_ID": c["user_id"],
                "PASSWORD": c["password"], "USER_KEY": c["user_key"], "ENCRYPTION_KEY": c["encryption_key"]}
        client = FivePaisaClient(cred=cred)
        client.get_totp_session(c["client_code"], pyotp.TOTP(c["totp_secret"]).now(), c["pin"])
        if not getattr(client, "access_token", None) and not getattr(client, "Jwt_token", None):
            raise AuthError("5paisa TOTP session failed")
        self._client = client

    def _fetch_master(self) -> pd.DataFrame:
        df = pd.read_csv(self.cfg.get("master_url", MASTER_URL), dtype=str)
        df.columns = [c.strip().lower() for c in df.columns]
        rows = []
        for _, r in df.iterrows():
            d = r.to_dict()
            if str(pick(d, "exch", default="N")) != "N" or str(pick(d, "exchtype", default="C")) != "C":
                continue
            if str(pick(d, "series", default="EQ")).strip() != "EQ":
                continue
            sym = str(pick(d, "symbolroot", "name", default="")).strip()
            if not sym:
                continue
            t = fnum(pick(d, "ticksize"), 0.05) or 0.05
            rows.append({"key": eq(sym), "exchange": "NSE", "kind": "EQ", "symbol": sym, "broker_symbol": sym,
                         "token": str(pick(d, "scripcode")), "lot_size": pick(d, "lotsize", default=1),
                         "tick_size": t / 100 if t >= 1 else t, "name": str(pick(d, "fullname", default=""))})
        return pd.DataFrame(rows)

    def _eq(self, key):
        inst = self.instrument(key)
        if inst.kind != "EQ":
            raise NotSupported("5paisa adapter supports cash equities only")
        return inst

    def get_quotes(self, keys):
        tok = {str(self._eq(k).token): k for k in keys}
        out = []
        for batch in chunks(list(tok), self.quote_batch):
            self.throttle("quote")
            resp = self._client.fetch_market_feed_scrip([{"Exch": "N", "ExchType": "C", "ScripCode": int(t)}
                                                          for t in batch]) or {}
            for d in resp.get("Data") or []:
                k = tok.get(str(pick(d, "Token", "ScripCode", default="")))
                ltp = fnum(pick(d, "LastRate", "LTP"))
                if k and ltp:
                    out.append(Quote(k, now_ist(), ltp, fnum(pick(d, "TotalQty", "Volume"), 0.0),
                                     open=fnum(pick(d, "OpenRate", "Open")), high=fnum(pick(d, "High")),
                                     low=fnum(pick(d, "Low")), prev_close=fnum(pick(d, "PClose", "PrevClose"))))
        return out

    def get_history(self, key, interval, start, end):
        inst = self._eq(key)
        iv, span = ("1m", 20) if interval == "1m" else ("1d", 1500)
        rows = []
        for s, e in date_spans(start, end, span):
            self.throttle("history")
            df = self._client.historical_data("N", "C", int(inst.token), iv, s.isoformat(), e.isoformat())
            if isinstance(df, pd.DataFrame) and not df.empty:
                cols = {c.lower(): c for c in df.columns}
                rows += list(zip(df[cols.get("datetime", df.columns[0])], df[cols["open"]], df[cols["high"]],
                                 df[cols["low"]], df[cols["close"]], df[cols["volume"]]))
        return self.candles_df(rows, interval)

    def place_order(self, req, client_id):
        inst = self._eq(req.key)
        if req.order_type == OrderType.SLM:
            raise NotSupported("5paisa adapter: use stop-limit (SL) orders")
        self.throttle("order")
        resp = self._client.place_order(OrderType="B" if req.side == Side.BUY else "S", Exchange="N", ExchangeType="C",
                                        ScripCode=int(inst.token), Qty=int(req.qty), Price=float(req.price or 0),
                                        IsIntraday=req.product == Product.MIS, StopLossPrice=float(req.trigger or 0),
                                        RemoteOrderID=client_id) or {}
        oid = pick(resp, "BrokerOrderID", "BrokerOrderId")
        if not oid or inum(pick(resp, "Status", default=0)) != 0:
            raise BrokerError(f"5paisa place_order: {pick(resp, 'Message', default=resp)}")
        self._order_cache[str(oid)] = {"qty": int(req.qty), "price": req.price, "trigger": req.trigger}
        return str(oid)

    def _exch_id(self, broker_id: str) -> str:
        for r in self._client.order_book() or []:
            if str(pick(r, "BrokerOrderId", "BrokerOrderID")) == str(broker_id):
                return str(pick(r, "ExchOrderID", default=""))
        raise BrokerError("order not found in 5paisa order book")

    def modify_order(self, broker_id, *, price=None, trigger=None, qty=None, order_type=None):
        c = self._order_cache.setdefault(broker_id, {"qty": qty or 0, "price": price, "trigger": trigger})
        c.update({k: v for k, v in (("price", price), ("trigger", trigger), ("qty", qty)) if v is not None})
        if order_type in (OrderType.LIMIT, OrderType.MARKET):
            c["trigger"] = 0
        self.throttle("order")
        self._client.modify_order(ExchOrderID=self._exch_id(broker_id), Qty=int(c["qty"]), Price=float(c["price"] or 0),
                                  StopLossPrice=float(c["trigger"] or 0))

    def cancel_order(self, broker_id):
        self.throttle("order")
        self._client.cancel_order(exch_order_id=self._exch_id(broker_id))

    def get_orders(self):
        self.throttle("orders")
        out = []
        for r in self._client.order_book() or []:
            k = self.key_for(token=pick(r, "ScripCode"))
            out.append(BrokerOrder(str(pick(r, "BrokerOrderId", "BrokerOrderID")), self.map_status(pick(r, "OrderStatus")),
                                   inum(pick(r, "TradedQty")), fnum(pick(r, "AveragePrice", "AvgRate", "Rate"), 0.0),
                                   k or "", Side.BUY if pick(r, "BuySell") == "B" else Side.SELL, inum(pick(r, "Qty")),
                                   str(pick(r, "RemoteOrderID", default="")), str(pick(r, "Reason", default="")), r))
        return out

    def get_positions(self):
        self.throttle("orders")
        out: dict = {}
        for r in self._client.positions() or []:
            k = self.key_for(token=pick(r, "ScripCode"))
            if k:
                p = "MIS" if str(pick(r, "OrderFor", default="I")).upper() == "I" else "CNC"
                out[(k, p)] = BrokerPosition(k, inum(pick(r, "NetQty")), fnum(pick(r, "AvgRate", "BuyAvgRate"), 0.0), p, r)
        for h in self._client.holdings() or []:
            k = self.key_for(token=pick(h, "NseCode", "ScripCode"))
            q = inum(pick(h, "Quantity"))
            if k and q:
                prev = out.get((k, "CNC"))
                out[(k, "CNC")] = BrokerPosition(k, q + (prev.qty if prev else 0), fnum(pick(h, "AvgRate"), 0.0), "CNC", h)
        return list(out.values())
