"""Adapter registry, install hints and maturity labels."""
from __future__ import annotations

import importlib

ADAPTERS = {
    "paper": "nsealgo.brokers.paper:PaperBroker",
    "zerodha": "nsealgo.brokers.zerodha:ZerodhaAdapter",
    "fyers": "nsealgo.brokers.fyers:FyersAdapter",
    "shoonya": "nsealgo.brokers.noren:NorenAdapter",
    "flattrade": "nsealgo.brokers.noren:NorenAdapter",
    "zebu": "nsealgo.brokers.noren:NorenAdapter",
    "upstox": "nsealgo.brokers.upstox:UpstoxAdapter",
    "angelone": "nsealgo.brokers.angelone:AngelOneAdapter",
    "dhan": "nsealgo.brokers.dhan:DhanAdapter",
    "icici": "nsealgo.brokers.icici_breeze:ICICIBreezeAdapter",
    "fivepaisa": "nsealgo.brokers.fivepaisa:FivePaisaAdapter",
    "kotak": "nsealgo.brokers.kotak_neo:KotakNeoAdapter",
    "aliceblue": "nsealgo.brokers.aliceblue:AliceBlueAdapter",
    "groww": "nsealgo.brokers.groww:GrowwAdapter",
    "yahoo": "nsealgo.brokers.yahoo:YahooData",
}

INSTALL = {
    "zerodha": "pip install kiteconnect", "fyers": "pip install fyers-apiv3  (then: pip uninstall -y asyncio)",
    "shoonya": "pip install NorenRestApiPy websocket-client", "flattrade": "pip install NorenRestApiPy websocket-client",
    "zebu": "pip install NorenRestApiPy websocket-client", "upstox": "pip install upstox-python-sdk  (then: pip uninstall -y uuid)",
    "angelone": "pip install smartapi-python logzero websocket-client", "dhan": "pip install dhanhq",
    "icici": "pip install breeze-connect", "fivepaisa": "pip install py5paisa",
    "kotak": 'pip install "git+https://github.com/Kotak-Neo/Kotak-neo-api-v2.git"  (then: pip uninstall -y asyncio)',
    "aliceblue": "pip install pya3", "groww": 'pip install growwapi "pandas<3"', "yahoo": "pip install yfinance",
}

MATURITY = {
    "zerodha": "full: orders, history, streaming depth", "fyers": "full: orders, history, streaming depth",
    "shoonya": "full: orders, history, streaming depth", "flattrade": "beta (Noren API; verify master URL)",
    "zebu": "beta (Noren API; verify host)", "upstox": "full: orders, history (polling quotes)",
    "angelone": "full: orders, history (polling quotes)", "dhan": "full: orders, history (polling quotes)",
    "icici": "beta: equities only", "fivepaisa": "beta: equities only", "kotak": "beta: no history API",
    "aliceblue": "beta", "groww": "beta", "yahoo": "data only, delayed; no orders", "paper": "simulated exchange",
}

SDK_MODULE = {
    "zerodha": "kiteconnect", "fyers": "fyers_apiv3", "shoonya": "NorenRestApiPy", "flattrade": "NorenRestApiPy",
    "zebu": "NorenRestApiPy", "upstox": "upstox_client", "angelone": "SmartApi", "dhan": "dhanhq",
    "icici": "breeze_connect", "fivepaisa": "py5paisa", "kotak": "neo_api_client", "aliceblue": "pya3",
    "groww": "growwapi", "yahoo": "yfinance",
}


def adapter_class(name: str):
    if name not in ADAPTERS:
        raise ValueError(f"unknown broker '{name}'. Choose from: {', '.join(ADAPTERS)}")
    mod, cls = ADAPTERS[name].split(":")
    return getattr(importlib.import_module(mod), cls)


def make_broker(name: str, cfg: dict, cache_dir=".cache", **kw):
    if name == "multi":                                   # several accounts behind one interface
        from .multi import build_multi
        return build_multi(cfg, cache_dir, **kw)
    C = adapter_class(name)
    bcfg = dict(((cfg.get("broker") or {}).get("brokers") or {}).get(name) or {})
    if name in ("shoonya", "flattrade", "zebu"):
        bcfg.setdefault("flavor", name)
    return C(bcfg, cache_dir, **kw)


# Live market-data access as published by each broker (checked September 2026 — pricing changes; verify).
# stream_capacity = instruments one user can stream at once, used by the tick mesh (override: feeds.limits).
FEEDS = {
    "fyers": {"cost": "free", "login": "browser OAuth, daily", "depth": "best bid/ask + sizes",
              "note": "data socket; FYERS says up to 5,000 symbols with current SDKs (mesh uses 1,000 by default)"},
    "shoonya": {"cost": "free", "login": "TOTP, automatic", "depth": "5 levels", "note": "Noren websocket"},
    "flattrade": {"cost": "free", "login": "session token", "depth": "5 levels", "note": "Noren websocket; set master_url"},
    "zebu": {"cost": "free (verify with Zebu)", "login": "TOTP, automatic", "depth": "5 levels", "note": "Noren websocket"},
    "angelone": {"cost": "free", "login": "TOTP, automatic", "depth": "5 levels",
                 "note": "WebSocket 2.0: 1,000 tokens per session, 3 sessions"},
    "upstox": {"cost": "free", "login": "browser OAuth, daily", "depth": "5 levels",
               "note": "Feed V3: 2 connections; 'full' mode 2,000 keys (1,500 when modes are mixed)"},
    "kotak": {"cost": "free", "login": "TOTP + MPIN, automatic", "depth": "best bid/ask + sizes",
              "note": "no history API: pair with a source that has one"},
    "icici": {"cost": "free", "login": "browser session, daily", "depth": "best bid/ask + sizes",
              "note": "Breeze websocket, cash equities in this adapter"},
    "aliceblue": {"cost": "free", "login": "API key session", "depth": "best bid/ask + sizes", "note": "Noren-style websocket"},
    "zerodha": {"cost": "Rs 500/month (Kite Connect)", "login": "browser OAuth, daily", "depth": "5 levels",
                "note": "the free Personal plan has no market data"},
    "dhan": {"cost": "Rs 499 + taxes/month (Data APIs)", "login": "access token", "depth": "5 levels",
             "note": "streaming not wired in this adapter"},
    "groww": {"cost": "Rs 499 + GST/month (Live Data)", "login": "TOTP", "depth": "5 levels",
              "note": "streaming not wired in this adapter"},
}
FREE_FEEDS = [n for n, v in FEEDS.items() if v["cost"].startswith("free")]
