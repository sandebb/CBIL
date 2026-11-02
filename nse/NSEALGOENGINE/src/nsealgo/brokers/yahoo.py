"""Yahoo Finance data-only adapter (free, delayed ~15 min). Useful to forward-test swing strategies
without a broker API; unsuitable for intraday execution research. No order support."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

from datetime import timedelta

import pandas as pd

from ..core.models import Instrument, Quote, default_instrument, parse_key
from .base import BrokerAdapter, NotSupported, fnum, now_ist
from .symbols import COLS

IDX_MAP = {"NIFTY50": "^NSEI", "NIFTYBANK": "^NSEBANK", "INDIAVIX": "^INDIAVIX", "NIFTY500": "^CRSLDX"}


class YahooData(BrokerAdapter):
    name = "yahoo"
    min_intervals = {"quote": 2.0, "history": 0.5}

    def login(self, interactive=True):
        import yfinance  # noqa: F401

    def _fetch_master(self):
        return pd.DataFrame(columns=COLS)

    def instrument(self, key: str) -> Instrument:
        inst = default_instrument(key)
        p = parse_key(key)
        inst.broker_symbol = IDX_MAP.get(p["symbol"], p["symbol"]) if p["kind"] == "IDX" else f"{p['symbol']}.NS"
        return inst

    def get_quotes(self, keys):
        import yfinance as yf
        out = []
        for k in keys:
            self.throttle("quote")
            try:
                fi = yf.Ticker(self.instrument(k).broker_symbol).fast_info
                out.append(Quote(k, now_ist(), float(fi["last_price"]), volume=fnum(fi.get("last_volume"), 0.0),
                                 open=fnum(fi.get("open")), high=fnum(fi.get("day_high")), low=fnum(fi.get("day_low")),
                                 prev_close=fnum(fi.get("previous_close"))))
            except Exception as ex:
                self.log.debug("quote %s failed: %s", k, ex)
        return out

    def get_history(self, key, interval, start, end):
        import yfinance as yf
        self.throttle("history")
        df = yf.download(self.instrument(key).broker_symbol, start=start, end=end + timedelta(days=1),
                         interval="1m" if interval == "1m" else "1d", progress=False, auto_adjust=False)
        if df is None or df.empty:
            return self.candles_df([], interval)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        rows = zip(df.index, df["Open"], df["High"], df["Low"], df["Close"], df["Volume"])
        return self.candles_df(rows, interval)

    def place_order(self, req, client_id):
        raise NotSupported("yahoo is data-only")

    def modify_order(self, broker_id, **kw):
        raise NotSupported("yahoo is data-only")

    def cancel_order(self, broker_id):
        raise NotSupported("yahoo is data-only")

    def get_orders(self):
        return []

    def get_positions(self):
        return []
