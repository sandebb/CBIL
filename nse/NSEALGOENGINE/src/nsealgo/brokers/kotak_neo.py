"""Kotak Neo API v2 (neo_api_client). Beta. No historical-data API: pair it with a data broker.

Install: pip install "git+https://github.com/Kotak-Neo/Kotak-neo-api-v2.git" then pip uninstall -y asyncio
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402


import pandas as pd

from ..core.models import BrokerOrder, BrokerPosition, OrderType, Product, Quote, Side, Status, eq
from .base import (AuthError, BrokerAdapter, BrokerError, NotSupported, chunks, fnum, inum, now_ist, pick,
                   quote_from_depth)


class KotakNeoAdapter(BrokerAdapter):
    name = "kotak"
    supports_streaming = True
    stream_capacity = 1000
    quote_batch = 50
    min_intervals = {"quote": 1.0, "order": 0.2, "orders": 0.6}
    STATUS_MAP = {"OPEN": Status.OPEN, "COMPLETE": Status.FILLED, "TRADED": Status.FILLED, "CANCELLED": Status.CANCELLED,
                  "REJECTED": Status.REJECTED, "TRIGGER PENDING": Status.TRIGGER_PENDING, "PUT ORDER REQ RECEIVED": Status.NEW,
                  "OPEN PENDING": Status.NEW, "VALIDATION PENDING": Status.NEW, "MODIFIED": Status.OPEN,
                  "PARTIALLY FILLED": Status.PARTIAL, "MODIFY PENDING": Status.OPEN, "CANCEL PENDING": Status.OPEN}
    PRODUCT = {Product.MIS: "MIS", Product.CNC: "CNC"}
    OT = {OrderType.LIMIT: "L", OrderType.MARKET: "MKT", OrderType.SL: "SL", OrderType.SLM: "SL-M"}

    def login(self, interactive: bool = True) -> None:
        try:
            from neo_api_client import NeoAPI
        except ImportError as ex:
            raise BrokerError('pip install "git+https://github.com/Kotak-Neo/Kotak-neo-api-v2.git"') from ex
        import pyotp
        c = self.cfg
        client = NeoAPI(environment=c.get("environment", "prod"), access_token=None, neo_fin_key=None,
                        consumer_key=c["consumer_key"])
        r1 = client.totp_login(mobile_number=c["mobile_number"], ucc=c["ucc"], totp=pyotp.TOTP(c["totp_secret"]).now())
        r2 = client.totp_validate(mpin=c["mpin"])
        if isinstance(r2, dict) and r2.get("error"):
            raise AuthError(f"Kotak login failed: {r1} / {r2}")
        self._client = client

    def _master_url(self, seg: str) -> str:
        res = self._client.scrip_master(exchange_segment=seg)
        if isinstance(res, str):
            return res
        paths = pick(res or {}, "filesPaths", default=[]) or pick((res or {}).get("data") or {}, "filesPaths", default=[])
        url = next((p for p in paths if seg in p.lower()), None)
        if not url:
            raise BrokerError(f"Kotak scrip master URL not found for {seg}: {res}")
        return url

    def _fetch_master(self) -> pd.DataFrame:
        rows = []
        for seg in ("nse_cm",):
            df = pd.read_csv(self._master_url(seg), dtype=str, low_memory=False)
            df.columns = [c.strip().rstrip(";") for c in df.columns]
            for _, r in df.iterrows():
                d = r.to_dict()
                t = fnum(pick(d, "dTickSize"), 5.0) or 5.0
                tick = t / 100 if t >= 1 else t
                tsym = str(pick(d, "pTrdSymbol", default=""))
                if seg == "nse_cm" and (str(pick(d, "pGroup", default="")) == "EQ" or tsym.endswith("-EQ")):
                    sym = str(pick(d, "pSymbolName", default=tsym.replace("-EQ", "")))
                    rows.append({"key": eq(sym), "exchange": "NSE", "kind": "EQ", "symbol": sym, "broker_symbol": tsym,
                                 "token": str(pick(d, "pSymbol")), "lot_size": pick(d, "lLotSize", default=1),
                                 "tick_size": tick})
        return pd.DataFrame(rows)

    def _seg(self, inst) -> str:
        return "nse_cm"

    def get_quotes(self, keys):
        tok = {}
        for k in keys:
            inst = self.instrument(k)
            tok[str(inst.token)] = (k, self._seg(inst))
        out = []
        for batch in chunks(list(tok), self.quote_batch):
            self.throttle("quote")
            resp = self._client.quotes(instrument_tokens=[{"instrument_token": t, "exchange_segment": tok[t][1]}
                                                          for t in batch], quote_type="all")
            items = resp if isinstance(resp, list) else pick(resp or {}, "message", "data", default=[]) or []
            for d in items:
                k = tok.get(str(pick(d, "exchange_token", "instrument_token", "tk", default="")), (None,))[0]
                ltp = fnum(pick(d, "ltp", "last_traded_price"))
                if k and ltp:
                    o = d.get("ohlc") or {}
                    out.append(quote_from_depth(k, ltp, pick(d, "last_volume", "volume"), d.get("depth"),
                                                open=fnum(o.get("open")), high=fnum(o.get("high")),
                                                low=fnum(o.get("low")), prev_close=fnum(o.get("close"))))
        return out

    def get_history(self, key, interval, start, end):
        raise NotSupported("Kotak Neo has no historical-data API; set broker.data to another broker or yahoo")

    def place_order(self, req, client_id):
        inst = self.instrument(req.key)
        self.throttle("order")
        resp = self._client.place_order(exchange_segment=self._seg(inst), product=self.PRODUCT[req.product],
                                        price=str(req.price or 0), order_type=self.OT[req.order_type],
                                        quantity=str(int(req.qty)), validity="DAY", trading_symbol=inst.broker_symbol,
                                        transaction_type="B" if req.side == Side.BUY else "S", amo="NO",
                                        disclosed_quantity="0", pf="N",
                                        market_protection=str(self.cfg.get("market_protection", "0")),  # OMS sends no MARKET orders
                                        trigger_price=str(req.trigger or 0), tag=client_id) or {}
        oid = pick(resp, "nOrdNo")
        if not oid:
            raise BrokerError(f"Kotak place_order: {resp}")
        self._order_cache[str(oid)] = {"inst": inst, "req": req}
        return str(oid)

    def modify_order(self, broker_id, *, price=None, trigger=None, qty=None, order_type=None):
        c = self._order_cache.get(broker_id)
        if not c:
            raise BrokerError("unknown order (placed by another session?)")
        inst, req = c["inst"], c["req"]
        ot = OrderType(order_type) if order_type is not None else req.order_type
        self.throttle("order")
        resp = self._client.modify_order(order_id=broker_id, price=str(price if price is not None else (req.price or 0)),
                                         order_type=self.OT[ot], quantity=str(int(qty or req.qty)), validity="DAY",
                                         instrument_token=str(inst.token), exchange_segment=self._seg(inst),
                                         product=self.PRODUCT[req.product], trading_symbol=inst.broker_symbol,
                                         transaction_type="B" if req.side == Side.BUY else "S",
                                         trigger_price=str(trigger if trigger is not None else (req.trigger or 0))) or {}
        if isinstance(resp, dict) and resp.get("stat") not in (None, "Ok"):
            raise BrokerError(f"Kotak modify: {resp}")

    def cancel_order(self, broker_id):
        self.throttle("order")
        self._client.cancel_order(order_id=broker_id)

    def get_orders(self):
        self.throttle("orders")
        out = []
        for r in (self._client.order_report() or {}).get("data") or []:
            out.append(BrokerOrder(str(r.get("nOrdNo")), self.map_status(r.get("ordSt")), inum(r.get("fldQty")),
                                   fnum(r.get("avgPrc"), 0.0), self.key_for(symbol=r.get("trdSym")) or "",
                                   Side.BUY if r.get("trnsTp") == "B" else Side.SELL, inum(r.get("qty")),
                                   str(pick(r, "tag", "usrRmk", default="")), r.get("rejRsn") or "", r))
        return out

    def get_positions(self):
        self.throttle("orders")
        out = []
        for r in (self._client.positions() or {}).get("data") or []:
            k = self.key_for(symbol=r.get("trdSym"))
            if not k:
                continue
            buy = inum(r.get("cfBuyQty")) + inum(r.get("flBuyQty"))
            sell = inum(r.get("cfSellQty")) + inum(r.get("flSellQty"))
            avg = fnum(r.get("buyAmt"), 0.0) / buy if buy else fnum(r.get("sellAmt"), 0.0) / max(sell, 1)
            out.append(BrokerPosition(k, buy - sell, avg, str(r.get("prod", "")), r))
        return out

    # ---- streaming (free): Neo HSM websocket; partial updates are merged into a per-instrument snapshot ----
    @staticmethod
    def parse_tick(d: dict, key: str) -> Quote | None:
        ltp = fnum(pick(d, "ltp", "iv"))
        if not ltp:
            return None
        return Quote(key, now_ist(), ltp, fnum(d.get("v"), 0.0) or 0.0, fnum(d.get("bp")) or None, fnum(d.get("sp")) or None,
                     fnum(d.get("bq")), fnum(d.get("bs")), upper_circuit=fnum(d.get("ucl")) or None,
                     lower_circuit=fnum(d.get("lcl")) or None, open=fnum(d.get("op")), high=fnum(d.get("h")),
                     low=fnum(d.get("lo")), prev_close=fnum(pick(d, "c", "ic")), source="kotak")

    def stream(self, keys, on_quote):
        new = {}
        for k in keys:
            inst = self.instrument(k)
            new[(self._seg(inst), str(inst.token))] = k
        if getattr(self, "_ktoks", None) is None:
            self._ktoks, self._ksnap = {}, {}

            def on_message(msg):
                if not isinstance(msg, dict) or msg.get("type") != "stock_feed":
                    return
                for d in msg.get("data") or []:
                    if not isinstance(d, dict):
                        continue
                    tk = (str(d.get("e")), str(d.get("tk")))
                    k = self._ktoks.get(tk)
                    if not k:
                        continue
                    snap = self._ksnap.setdefault(tk, {})
                    snap.update({x: v for x, v in d.items() if v not in (None, "")})
                    q = self.parse_tick(snap, k)
                    if q:
                        on_quote(q)

            c = self._client
            c.on_message = on_message
            c.on_error = lambda e: self.log.warning("websocket error: %s", e)
            c.on_open = lambda m: self.log.info("websocket open")
            c.on_close = lambda m: self.log.info("websocket closed")
        self._ktoks.update(new)
        self._client.subscribe(instrument_tokens=[{"instrument_token": t, "exchange_segment": seg} for seg, t in new],
                               isIndex=False, isDepth=False)
