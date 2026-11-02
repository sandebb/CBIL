"""Normalised instrument master shared by all adapters."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402


import pandas as pd

from ..core.models import Instrument

COLS = ["key", "exchange", "kind", "symbol", "broker_symbol", "token", "lot_size", "tick_size", "expiry", "name"]
INDEX_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX"}


def nse_tick(price: float) -> float:
    """NSE equity tick-size schedule (price-band based, 2025 revision); used when a master lacks ticks."""
    if price < 250:
        return 0.01
    if price < 1000:
        return 0.05
    if price < 5000:
        return 0.10
    if price < 10000:
        return 0.50
    if price < 20000:
        return 1.0
    return 5.0


class SymbolMaster:
    def __init__(self, df: pd.DataFrame, broker: str = "?"):
        df = df.copy() if df is not None else pd.DataFrame(columns=COLS)
        for c in COLS:
            if c not in df.columns:
                df[c] = None
        df = df[COLS]
        df["lot_size"] = pd.to_numeric(df["lot_size"], errors="coerce").fillna(1).astype(int).clip(lower=1)
        df["tick_size"] = pd.to_numeric(df["tick_size"], errors="coerce").fillna(0.05)
        df["token"] = df["token"].astype(str)
        df["broker_symbol"] = df["broker_symbol"].astype(str)
        df = df.drop_duplicates("key", keep="first")
        self.broker = broker
        self.df = df.set_index("key", drop=False)
        self._tok = dict(zip(df["token"], df["key"]))
        self._sym = dict(zip(df["broker_symbol"], df["key"]))

    def __contains__(self, key: str) -> bool:
        return key in self.df.index

    def __len__(self) -> int:
        return len(self.df)

    def get(self, key: str) -> Instrument:
        if key not in self.df.index:
            raise KeyError(f"{key} not found in {self.broker} instrument master")
        r = self.df.loc[key]
        exp = r["expiry"]
        return Instrument(key=key, exchange=str(r["exchange"]), kind=str(r["kind"]), symbol=str(r["symbol"]),
                          broker_symbol=str(r["broker_symbol"]), token=str(r["token"]), lot_size=int(r["lot_size"]),
                          tick_size=float(r["tick_size"]), expiry=None if pd.isna(exp) or exp in ("", "None") else str(exp)[:10],
                          name="" if pd.isna(r["name"]) else str(r["name"]))

    def key_for_token(self, token) -> str | None:
        return self._tok.get(str(token))

    def key_for_symbol(self, sym) -> str | None:
        return self._sym.get(str(sym))

    def keys(self, kind: str | None = None) -> list[str]:
        return list(self.df.index if kind is None else self.df.index[self.df["kind"] == kind])
