"""ICICI Direct Breeze (breeze-connect). Beta: cash equities (MIS 'margin' / CNC 'cash')."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import io
import zipfile
from datetime import date, timedelta
from urllib.parse import quote as urlquote

import pandas as pd
import requests

from ..core.models import BrokerOrder, BrokerPosition, OrderType, Product, Side, Status, eq
from .base import (AuthError, BrokerAdapter, BrokerError, NotSupported, date_spans, fnum, inum, now_ist, pick)
from ..core.models import Quote

MASTER_URL = "https://directlink.icicidirect.com/NewSecurityMaster/SecurityMaster.zip"


def _clean(x) -> str:
    return str(x if x is not None else "").strip().strip('"').strip()


class ICICIBreezeAdapter(BrokerAdapter):
    name = "icici"
    supports_streaming = True
    stream_capacity = 500
    quote_batch = 1
    min_intervals = {"quote": 0.35, "history": 0.5, "order": 0.2, "orders": 0.6}
    STATUS_MAP = {"ORDERED": Status.OPEN, "OPEN": Status.OPEN, "PENDING": Status.OPEN, "REQUESTED": Status.NEW,
                  "EXECUTED": Status.FILLED, "COMPLETE": Status.FILLED, "CANCELLED": Status.CANCELLED,
                  "REJECTED": Status.REJECTED, "EXPIRED": Status.CANCELLED, "PARTIALLY EXECUTED": Status.PARTIAL}
    PRODUCT = {Product.MIS: "margin", Product.CNC: "cash"}
    OT = {OrderType.LIMIT: "limit", OrderType.MARKET: "market", OrderType.SL: "stoploss", OrderType.SLM: "stoploss"}

    def login(self, interactive: bool = True) -> None:
        try:
            from breeze_connect import BreezeConnect
        except ImportError as ex:
            raise BrokerError("pip install breeze-connect") from ex
        c = self.cfg
        b = BreezeConnect(api_key=c["api_key"])
        sess = self.load_session()
        if sess is None:
            if not interactive:
                raise AuthError("no Breeze session for today; run: nsealgo login icici")
            url = f"https://api.icicidirect.com/apiuser/login?api_key={urlquote(c['api_key'], safe='')}"
            sess = {"session_token": self.oauth_capture(url, "apisession", c.get("redirect_uri"))}
            self.save_session(sess)
        b.generate_session(api_secret=c["api_secret"], session_token=sess["session_token"])
        self._client = b

    def _fetch_master(self) -> pd.DataFrame:
        raw = requests.get(self.cfg.get("master_url", MASTER_URL), timeout=120).content
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = next(n for n in z.namelist() if n.upper().startswith("NSESCRIPMASTER"))
            df = pd.read_csv(z.open(name), dtype=str)
        df.columns = [_clean(c).lower() for c in df.columns]
        rows = []
        for _, r in df.iterrows():
            if _clean(r.get("series")) != "EQ":
                continue
            nse, short = _clean(pick(r.to_dict(), "exchangecode", "symbol")), _clean(r.get("shortname"))
            if not nse or not short:
                continue
            t = fnum(_clean(r.get("ticksize")), 0.05) or 0.05
            rows.append({"key": eq(nse), "exchange": "NSE", "kind": "EQ", "symbol": nse, "broker_symbol": short,
                         "token": _clean(r.get("token")), "lot_size": _clean(r.get("lotsize")) or 1,
                         "tick_size": t / 100 if t >= 1 else t, "name": _clean(r.get("companyname"))})
        return pd.DataFrame(rows)

    def _eq_only(self, key):
        inst = self.instrument(key)
        if inst.kind != "EQ":
            raise NotSupported("ICICI adapter supports cash equities only")
        return inst

    def get_quotes(self, keys):
        out = []
        for k in keys:
            inst = self._eq_only(k)
            self.throttle("quote")
            resp = self._client.get_quotes(stock_code=inst.broker_symbol, exchange_code="NSE", expiry_date="",
                                           product_type="cash", right="", strike_price="") or {}
            d = next((x for x in resp.get("Success") or [] if _clean(x.get("exchange_code")).upper() == "NSE"), None)
            if d and fnum(d.get("ltp")):
                out.append(Quote(k, now_ist(), float(d["ltp"]), fnum(d.get("total_quantity_traded"), 0.0),
                                 fnum(d.get("best_bid_price")) or None, fnum(d.get("best_offer_price")) or None,
                                 fnum(d.get("best_bid_quantity")), fnum(d.get("best_offer_quantity")),
                                 upper_circuit=fnum(d.get("upper_circuit")), lower_circuit=fnum(d.get("lower_circuit")),
                                 open=fnum(d.get("open")), high=fnum(d.get("high")), low=fnum(d.get("low")),
                                 prev_close=fnum(d.get("previous_close"))))
        return out

    def get_history(self, key, interval, start, end):
        inst = self._eq_only(key)
        iv, span = ("1minute", 2) if interval == "1m" else ("1day", 900)
        rows = []
        for s, e in date_spans(start, end, span):
            self.throttle("history")
            resp = self._client.get_historical_data_v2(interval=iv, from_date=f"{s}T00:00:00.000Z",
                                                       to_date=f"{e}T23:59:59.000Z", stock_code=inst.broker_symbol,
                                                       exchange_code="NSE", product_type="cash") or {}
            rows += [(r.get("datetime"), r.get("open"), r.get("high"), r.get("low"), r.get("close"), r.get("volume"))
                     for r in resp.get("Success") or []]
        return self.candles_df(rows, interval)

    def place_order(self, req, client_id):
        inst = self._eq_only(req.key)
        if req.product not in self.PRODUCT:
            raise NotSupported("product not supported")
        self.throttle("order")
        resp = self._client.place_order(stock_code=inst.broker_symbol, exchange_code="NSE",
                                        product=self.PRODUCT[req.product], action=req.side.value.lower(),
                                        order_type=self.OT[req.order_type], stoploss=str(req.trigger or ""),
                                        quantity=str(int(req.qty)), price=str(req.price or ""), validity="day",
                                        user_remark=client_id) or {}
        oid = (resp.get("Success") or {}).get("order_id") if isinstance(resp.get("Success"), dict) else None
        if not oid:
            raise BrokerError(f"Breeze place_order: {resp.get('Error') or resp}")
        self._order_cache[str(oid)] = {"qty": int(req.qty), "price": req.price, "trigger": req.trigger,
                                       "type": self.OT[req.order_type]}
        return str(oid)

    def modify_order(self, broker_id, *, price=None, trigger=None, qty=None, order_type=None):
        c = self._order_cache.setdefault(broker_id, {"qty": qty or 0, "price": price, "trigger": trigger, "type": "limit"})
        if order_type is not None:
            c["type"] = self.OT[OrderType(order_type)]
        c.update({k: v for k, v in (("price", price), ("trigger", trigger), ("qty", qty)) if v is not None})
        self.throttle("order")
        resp = self._client.modify_order(order_id=broker_id, exchange_code="NSE", order_type=c["type"],
                                         stoploss=str(c["trigger"] or ""), quantity=str(int(c["qty"])),
                                         price=str(c["price"] or ""), validity="day", disclosed_quantity="0",
                                         validity_date="") or {}
        if resp.get("Error"):
            raise BrokerError(f"Breeze modify: {resp['Error']}")

    def cancel_order(self, broker_id):
        self.throttle("order")
        resp = self._client.cancel_order(exchange_code="NSE", order_id=broker_id) or {}
        if resp.get("Error"):
            raise BrokerError(f"Breeze cancel: {resp['Error']}")

    def get_orders(self):
        self.throttle("orders")
        d = date.today().isoformat()
        resp = self._client.get_order_list(exchange_code="NSE", from_date=f"{d}T00:00:00.000Z",
                                           to_date=f"{d}T23:59:59.000Z") or {}
        out = []
        for r in resp.get("Success") or []:
            qty = inum(r.get("quantity"))
            filled = max(0, qty - inum(r.get("pending_quantity")) - inum(r.get("cancelled_quantity")))
            out.append(BrokerOrder(str(r.get("order_id")), self.map_status(r.get("status")), filled,
                                   fnum(r.get("average_price"), 0.0), self.key_for(symbol=r.get("stock_code")) or "",
                                   Side.BUY if _clean(r.get("action")).lower() == "buy" else Side.SELL, qty,
                                   r.get("user_remark") or "", "", r))
        return out

    def get_positions(self):
        self.throttle("orders")
        out: dict = {}
        for r in (self._client.get_portfolio_positions() or {}).get("Success") or []:
            k = self.key_for(symbol=r.get("stock_code"))
            if k:
                sign = 1 if _clean(r.get("action")).lower() == "buy" else -1
                p = "MIS" if _clean(r.get("product_type")).lower() == "margin" else "CNC"
                out[(k, p)] = BrokerPosition(k, sign * inum(r.get("quantity")), fnum(r.get("average_price"), 0.0), p, r)
        try:
            d0, d1 = date.today() - timedelta(days=3650), date.today()
            hold = self._client.get_portfolio_holdings(exchange_code="NSE", from_date=f"{d0}T00:00:00.000Z",
                                                       to_date=f"{d1}T23:59:59.000Z", stock_code="",
                                                       portfolio_type="") or {}
            for h in hold.get("Success") or []:
                k = self.key_for(symbol=h.get("stock_code"))
                q = inum(h.get("quantity"))
                if k and q:
                    prev = out.get((k, "CNC"))
                    out[(k, "CNC")] = BrokerPosition(k, q + (prev.qty if prev else 0), fnum(h.get("average_price"), 0.0),
                                                     "CNC", h)
        except Exception as ex:
            self.log.debug("holdings unavailable: %s", ex)
        return list(out.values())

    # ---- streaming (free): Breeze websocket, level-1 exchange quotes ("4.1!<token>" for NSE cash) ----
    @staticmethod
    def parse_tick(t: dict, key: str) -> Quote | None:
        ltp = fnum(t.get("last"))
        if not ltp:
            return None
        return Quote(key, now_ist(), ltp, fnum(t.get("ttq"), 0.0) or 0.0, fnum(t.get("bPrice")) or None,
                     fnum(t.get("sPrice")) or None, fnum(t.get("bQty")), fnum(t.get("sQty")), open=fnum(t.get("open")),
                     high=fnum(t.get("high")), low=fnum(t.get("low")), prev_close=fnum(t.get("close")), source="icici")

    def stream(self, keys, on_quote):
        new = {f"4.1!{self._eq_only(k).token}": k for k in keys}
        first = getattr(self, "_stoks", None) is None
        self._stoks = {**(self._stoks or {}), **new} if not first else dict(new)
        if first:
            def on_ticks(t):
                k = self._stoks.get((t or {}).get("symbol"))
                q = self.parse_tick(t, k) if k else None
                if q:
                    on_quote(q)
            self._client.on_ticks = on_ticks
            self._client.ws_connect()
        for st in new:
            self._client.subscribe_feeds(stock_token=st)

    def stop_stream(self):
        try:
            self._client.ws_disconnect()
        except Exception:
            pass
