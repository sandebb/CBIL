"""
NSEUIENGINE - swing_trader.py
Trades NSEDATAENGINE's Nsealgo v3.11 swing signals with real money on Fyers: 1 share per signal, the strategy's own
entry and exit, a protective stop on every position, and hard portfolio risk limits.

Daily cycle (Task Scheduler, registered by setup_swing_trader.ps1)
  20:30  evening   runs NSEDATAENGINE\\nse_signals.py (data update + scan) -> signals / exit flags for the next
                   session, marks today's equity, prints tomorrow's plan
  08:58  morning   the whole trading session:
                   09:00 pre-open  SELL positions whose exit rule / time stop fired (protected LIMIT, fills in the
                                   opening auction like the backtest's "next open"); BUY new signals (LIMIT at the
                                   signal close = the backtest's entry)
                   09:15           protective STOP (SL-limit) for every position; PEAD buy-stops (armed 3 sessions)
                   until 15:30     watches fills (new fill -> its stop), targets (target hit -> cancel stop, sell at
                                   target), risk limits and the kill switch
                   15:31           end-of-day reconcile; day orders expire and are re-placed next morning

Stop protection (fix of 7 Oct 2026)
  * the SL-limit's limit price is kept inside the day's circuit band (Fyers rejects a limit below the lower circuit);
    the band comes from the market-depth API and from Fyers' own rejection message
  * no broker stops are (re)placed 15:15-15:30 (Fyers cancels them at 15:15 and narrows its band); a SOFTWARE STOP
    covers every position that has no live broker stop: price at/below the stop -> sell at once
  * 09:16 protection check: each holding must have a broker stop, a working exit or the software stop -> else ALERT
    (state\ALERTS.txt + a Windows pop-up)

Exit plan per strategy (the backtest variant of your book, else v3.11's own):
  target strategies (pead, pead_200dma)   sell at target; protective stop; 3-month time exit
  rule strategies (everything else)       v3.11 exit rule at the close -> sell next open; strategy time stop;
                                          protective stop; 3-month time exit

Risk limits (state\\trader_config.json): capital, 1 share/trade, max price per share, max positions, max new entries
per day, daily loss limit, drawdown -> no new entries, bigger drawdown -> sell everything and stop, kill switch.

Commands
  python swing_trader.py evening              scan + plan for the next session
  python swing_trader.py morning              run the trading session (what the scheduled task runs)
  python swing_trader.py morning --dry-run    show the orders it would place now; sends nothing, changes nothing
  python swing_trader.py status               positions, orders, P&L, risk state
  python swing_trader.py report               closed trades per strategy vs the backtest
  python swing_trader.py kill [--flatten]     stop new entries (and with --flatten sell every position it holds)
  python swing_trader.py resume               clear kill / drawdown blocks

Safety
  * "live": false in the config = nothing is ever sent to Fyers (dry run). Set it to true to trade.
  * Only ever sells shares it bought itself (tracked in state\\trader_state.json); your other holdings are untouched.
  * Orders only through the static IP (STATICIP_PROXY / STATIC_IP) when require_static_ip is true (SEBI rule).
  * Every order carries a unique tag; when Fyers gives no acknowledgement (code 201 / network error) the order book
    is checked by tag before anything is retried, so an order is never sent twice.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import csv
import io
import json
import logging
import logging.handlers
import math
import os
import re
import subprocess
import sys
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fyers_token import IST, FyersTokenMissing, get_access_token, get_client_id  # noqa: E402
from fyers_token import _read as env_value  # noqa: E402  (registry first: scheduled tasks see fresh values)
from fyers_token import data_dir, shared_dir  # noqa: E402

__version__ = "1.0.0"
HOME = Path(os.environ.get("NSEUIENGINE_HOME") or HERE)
DATA = data_dir()
STATE_DIR, LOG_DIR = DATA / "state", DATA / "logs"
CONFIG_FILE = STATE_DIR / "trader_config.json"
STATE_FILE = STATE_DIR / "trader_state.json"
JOURNAL_FILE = DATA / "reports" / "trade_journal.csv"
STATUS_FILE = STATE_DIR / "trader_status.json"
KILL_FILE = STATE_DIR / "KILL"
API = "https://api-t1.fyers.in/api/v3"
DATA = "https://api-t1.fyers.in/data"
SYMBOL_MASTER = "https://public.fyers.in/sym_details/NSE_CM.csv"

DEFAULT_CONFIG = {
    "live": False,                         # False: dry run - nothing is sent to Fyers
    "capital": 100000,                     # Rs the system may have invested at any time (positions + open buys)
    # ---- position sizing: "risk" = a professional's sizing; "fixed" = qty_per_trade shares per signal
    "sizing": "risk",
    "risk_per_trade_pct": 1.0,             # Rs at risk to the stop per trade, % of capital (1% = Rs 1,000)
    "regime_risk_multiplier": {"turbulent": 0.5, "crisis": 0.0},   # v3.11 halves swing size when turbulent
    "max_portfolio_heat_pct": 6.0,         # all open risk (entry - stop) x qty together <= this % of capital
    "max_position_pct": 20.0,              # no position worth more than this % of capital
    "min_position_value": 2000,            # smaller than this and fixed charges eat the edge -> skip
    "max_stop_pct": 25.0,                  # stop further than this below the entry -> suspect data, skip
    "qty_per_trade": 1,                    # used when sizing = "fixed"
    "value_per_trade": 10000,              # used when sizing = "value": qty = this Rs / price
    "allow_symbols": [],                   # non-empty = trade ONLY these symbols
    "max_price_per_share": 15000,          # skip signals on shares costing more than this
    "max_open_positions": 12,
    "max_new_entries_per_day": 8,
    "daily_loss_limit": 3000,              # Rs: today's P&L at or below -this -> no new entries today
    "stop_entries_drawdown": 10000,        # Rs: total P&L at or below -this -> no new entries until `resume`
    "flatten_drawdown": 20000,             # Rs: total P&L at or below -this -> sell everything, kill
    # strategies with a backtested edge (profit factor >= 1.8 in the v3.11 backtests); "all" = every strategy
    "strategies": ["pead", "pead_200dma", "pullback", "resid_momentum_200dma_hold63"],
    "min_bt_profit_factor": 1.5,           # and never a strategy whose backtest profit factor is below this
    "regime_filter": True,                 # only signals v3.11's regime allows
    "exclude_symbols": [],
    "stop_limit_buffer_pct": 2.0,          # SL-limit: limit this % below the stop trigger
    "exit_protection_pct": 5.0,            # exit / stop-hit sells: LIMIT this % below the last price
    "entry_chase_pct": 0.0,                # 0 = buy exactly at the signal price, as the backtest
    "time_exit_months": 3,
    "poll_seconds": 20,
    "require_static_ip": True,
    "broker": "fyers",                     # fyers | shoonya (Shoonya through NSEALGOENGINE's adapter; see below)
    "nsealgoengine_home": "",              # where NSEALGOENGINE is (default %NSEALGOENGINE_HOME% or ${NSE_ROOT}/code/NSEALGOENGINE)
    "data_python": "python",               # Python that runs NSEDATAENGINE (polars, pandas ...)
    "nsedataengine_home": "",              # default: %NSEDATAENGINE_HOME% or ${NSE_ROOT}/code/NSEDATAENGINE
    "est_charges_per_round_trip": 20.0,    # Rs, for the journal's net P&L (DP charge, STT, ...)
}
EXIT_TARGET, EXIT_RULE = "target", "rule"

log = logging.getLogger("nseui.trader")


class TraderError(Exception):
    pass


# =================================================================================================================
# small helpers
# =================================================================================================================
def now() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


def at(d: date, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, ss)


def fnum(v, default=0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def floor_tick(p: float, tick: float) -> float:
    return round(math.floor(p / tick + 1e-9) * tick, 2)


def ceil_tick(p: float, tick: float) -> float:
    return round(math.ceil(p / tick - 1e-9) * tick, 2)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig")))
    else:
        atomic_write(CONFIG_FILE, json.dumps(DEFAULT_CONFIG, indent=2))
    return cfg


def data_home(cfg) -> Path:
    for c in (cfg.get("nsedataengine_home"), os.environ.get("NSEDATAENGINE_HOME"), os.path.expandvars(os.path.expandvars(__import__("os").environ.get("NSE_ROOT", r"D:\CBIL") + r"/code/NSEDATAENGINE"))):
        if c and Path(c).is_dir():
            return Path(c)
    raise TraderError("NSEDATAENGINE folder not found (set nsedataengine_home in the config)")


def signals_dir(cfg) -> Path:
    """%NSEDATAENGINE_SIGNALS_DIR%, else %NSE_DATA_ROOT%\\NSEDATAENGINE\\signals, else <NSEDATAENGINE>\\signals."""
    d = shared_dir("NSEDATAENGINE_SIGNALS_DIR", "NSEDATAENGINE", "signals", Path())
    return d if str(d) not in ("", ".") and d.is_dir() else data_home(cfg) / "signals"


# =================================================================================================================
# trading calendar (NSE holidays saved by nse_signals.py; fixed-date holidays as fallback)
# =================================================================================================================
class Calendar:
    def __init__(self, sig_dir: Path):
        self.hol = set()
        files = sorted(sig_dir.glob("session_*.json"))
        if files:
            try:
                self.hol = {date.fromisoformat(x) for x in json.loads(files[-1].read_text())["holidays"]}
            except Exception:
                pass
        if not self.hol:
            y = date.today().year
            self.hol = {date(yy, m, d) for yy in (y, y + 1) for m, d in ((1, 26), (5, 1), (8, 15), (10, 2), (12, 25))}

    def is_session(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.hol

    def sessions_between(self, a: date, b: date) -> int:
        """Sessions after a up to and including b."""
        n, d = 0, a
        while d < b:
            d += timedelta(days=1)
            n += self.is_session(d)
        return n

    def add_months_session(self, d: date, months: int) -> date:
        m = d.month - 1 + months
        y, m = d.year + m // 12, m % 12 + 1
        day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31,
                          30, 31][m - 1])
        out = date(y, m, day)
        while not self.is_session(out):                 # last session on/before the date
            out -= timedelta(days=1)
        return out


# =================================================================================================================
# Fyers REST client (orders through the static-IP proxy, throttled, idempotent by tag)
# =================================================================================================================
class Fyers:
    STATUS = {1: "cancelled", 2: "filled", 3: "unused", 4: "transit", 5: "rejected", 6: "pending", 7: "expired"}

    def __init__(self, cfg: dict, dry: bool):
        self.cfg, self.dry = cfg, dry
        self.app = get_client_id()
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"{self.app}:{get_access_token()}", "Accept": "application/json",
                               "User-Agent": f"NSEUIENGINE-trader/{__version__}"})
        self.proxy = env_value("STATICIP_PROXY")
        if self.proxy:
            self.s.proxies.update({"http": self.proxy, "https": self.proxy})
        self._calls = deque()

    # -- plumbing ----------------------------------------------------------------------------------------------
    def _throttle(self):
        while True:
            t = time.monotonic()
            while self._calls and t - self._calls[0] > 60:
                self._calls.popleft()
            last_s = [x for x in self._calls if t - x < 1]
            if len(self._calls) < 150 and len(last_s) < 5:   # Fyers: 10/s, 200/min - stay well below
                break
            time.sleep(0.25)
        self._calls.append(time.monotonic())

    def _req(self, method, url, **kw) -> dict:
        self._throttle()
        r = self.s.request(method, url, timeout=15, **kw)
        try:
            return r.json()
        except ValueError:
            raise TraderError(f"{method} {url.rsplit('/', 1)[-1]}: HTTP {r.status_code}, non-JSON reply")

    def _ok(self, d: dict, what: str) -> dict:
        if d.get("s") != "ok":
            raise TraderError(f"{what}: {d.get('message') or d}")
        return d

    def verify_static_ip(self) -> str:
        want = (env_value("STATIC_IP") or "").strip()
        if not (self.proxy and want):
            raise TraderError("require_static_ip: set the environment variables STATICIP_PROXY (http://user:pass@host:"
                              "port) and STATIC_IP (the IP registered with Fyers)")
        got = requests.get("https://api.ipify.org", proxies=self.s.proxies, timeout=15).text.strip()
        if got != want:
            raise TraderError(f"orders would leave from {got}, not the registered static IP {want} - refusing to trade")
        return got

    # -- reads ---------------------------------------------------------------------------------------------------
    def profile(self):
        return self._ok(self._req("GET", f"{API}/profile"), "profile").get("data", {})

    def funds_available(self) -> float:
        d = self._ok(self._req("GET", f"{API}/funds"), "funds")
        for f in d.get("fund_limit", []):
            if "available balance" in str(f.get("title", "")).lower():
                return fnum(f.get("equityAmount"))
        return 0.0

    def holdings(self) -> dict:
        """symbol -> shares in the account (demat + T1 + today's net buys)."""
        out = {}
        d = self._req("GET", f"{API}/holdings")
        if d.get("s") != "ok" and "holding" not in str(d.get("message", "")).lower():
            self._ok(d, "holdings")
        for h in d.get("holdings", []):
            q = fnum(h.get("quantity")) + fnum(h.get("qty_t1")) if h.get("holdingType") != "T1" \
                else max(fnum(h.get("quantity")), fnum(h.get("qty_t1")))
            out[h.get("symbol")] = out.get(h.get("symbol"), 0) + int(q)
        p = self._req("GET", f"{API}/positions")
        if p.get("s") != "ok" and "position" not in str(p.get("message", "")).lower():
            self._ok(p, "positions")
        for r in p.get("netPositions", []):
            if r.get("productType") == "CNC":
                out[r.get("symbol")] = out.get(r.get("symbol"), 0) + int(fnum(r.get("netQty")))
        return out

    def orders(self) -> dict:
        """order id -> {status, filled, price, tag, message, symbol, side}"""
        d = self._ok(self._req("GET", f"{API}/orders"), "orders")
        out = {}
        for r in d.get("orderBook", []) or []:
            tag = str(r.get("orderTag") or "")
            tag = tag[2:] if tag.startswith("1:") else ("" if tag.startswith("2:") else tag)
            out[str(r.get("id"))] = {"status": self.STATUS.get(int(fnum(r.get("status"), 0)), str(r.get("status"))),
                                     "filled": int(fnum(r.get("filledQty"))), "price": fnum(r.get("tradedPrice")),
                                     "tag": tag, "message": r.get("message") or "", "symbol": r.get("symbol"),
                                     "side": r.get("side")}
        return out

    def quotes(self, symbols) -> dict:
        out = {}
        symbols = list(dict.fromkeys(symbols))
        for i in range(0, len(symbols), 50):
            d = self._req("GET", f"{DATA}/quotes", params={"symbols": ",".join(symbols[i:i + 50])})
            for q in d.get("d", []) or []:
                v = q.get("v") or {}
                if v.get("lp") is not None:
                    out[q.get("n")] = {"ltp": fnum(v.get("lp")), "prev_close": fnum(v.get("prev_close_price")),
                                       "open": fnum(v.get("open_price")), "high": fnum(v.get("high_price")),
                                       "low": fnum(v.get("low_price"))}
        return out

    def circuit(self, symbol) -> tuple[float, float] | None:
        """(lower, upper) circuit limits for today from the market-depth API, or None."""
        d = self._req("GET", f"{DATA}/depth", params={"symbol": symbol, "ohlcv_flag": 1})
        v = (d.get("d") or {}).get(symbol) or {}
        lo, hi = fnum(v.get("lower_ckt"), 0), fnum(v.get("upper_ckt"), 0)
        return (lo, hi) if lo > 0 and hi > lo else None

    # -- writes --------------------------------------------------------------------------------------------------
    def place(self, symbol, side, otype, limit, stop, tag, qty) -> str:
        """otype: 1 limit, 2 market, 3 SL-M, 4 SL-limit. Returns the order id. Never sends twice: on a missing
        acknowledgement the order book is searched for the tag first."""
        body = {"symbol": symbol, "qty": int(qty), "type": otype, "side": side, "productType": "CNC",
                "limitPrice": float(limit or 0), "stopPrice": float(stop or 0), "validity": "DAY",
                "disclosedQty": 0, "offlineOrder": False, "orderTag": tag}
        if self.dry:
            return f"DRY-{tag}"
        try:
            d = self._req("POST", f"{API}/orders/sync", json=body)
        except (requests.RequestException, TraderError) as ex:
            d = {"s": "error", "code": -99, "message": str(ex)}
        if d.get("s") == "ok" and d.get("id"):
            return str(d["id"])
        if str(d.get("code")) in ("201", "-99") or d.get("s") == "ok":
            for _ in range(3):
                time.sleep(1.0)
                try:
                    for oid, o in self.orders().items():
                        if o["tag"] == tag:
                            return oid
                except Exception:
                    pass
            return f"UNCERTAIN-{tag}"                   # resolved by tag on the next poll
        raise TraderError(f"order {tag} {symbol} rejected: {d.get('message') or d}")

    def cancel(self, oid: str) -> bool:
        if self.dry or oid.startswith(("DRY-", "UNCERTAIN-")):
            return True
        d = self._req("DELETE", f"{API}/orders/sync", json={"id": oid})
        return d.get("s") == "ok"


# =================================================================================================================
# Shoonya (Finvasia) client - same interface as Fyers above, built on NSEALGOENGINE's Noren adapter, which does the
# TOTP login, the static-IP check (broker.brokers.shoonya.static_ip) and the symbol master. Run the trader with
# NSEALGOENGINE's Python (.venv) when "broker": "shoonya" - setup_swing_trader.ps1 does that.
# =================================================================================================================
def nsealgo_home(cfg) -> Path:
    for c in (cfg.get("nsealgoengine_home"), os.environ.get("NSEALGOENGINE_HOME"), env_value("NSEALGOENGINE_HOME"),
              __import__("os").environ.get("NSE_ROOT", r"D:\CBIL") + r"/code\NSEALGOENGINE"):
        if c and (Path(c) / "config" / "system.yaml").exists():
            return Path(c)
    raise TraderError("NSEALGOENGINE folder not found (set nsealgoengine_home in the config)")


class Shoonya:
    STATUS = {"FILLED": "filled", "CANCELLED": "cancelled", "REJECTED": "rejected", "OPEN": "pending",
              "NEW": "pending", "TRIGGER_PENDING": "pending", "PARTIAL": "pending", "UNKNOWN": "transit"}
    OTYPE = {1: "LIMIT", 2: "MARKET", 3: "SLM", 4: "SL"}

    def __init__(self, cfg: dict, dry: bool, master: dict):
        self.cfg, self.dry = cfg, dry
        home = nsealgo_home(cfg)
        if str(home / "src") not in sys.path:
            sys.path.insert(0, str(home / "src"))
        try:
            from nsealgo.config import load_config, resolve_path
            from nsealgo.brokers.registry import make_broker
            from nsealgo.core import models as M
        except ImportError as ex:
            raise TraderError(f"NSEALGOENGINE could not be imported ({ex}) - run the trader with "
                              f"{home}\\.venv\\Scripts\\python.exe (setup_swing_trader.ps1 does this)") from ex
        self.M = M
        acfg = load_config(home / "config" / "system.yaml")
        self.bcfg = ((acfg.get("broker") or {}).get("brokers") or {}).get("shoonya") or {}
        if not dry and self.bcfg.get("orders") is False:
            raise TraderError("Shoonya order placement is still dormant (NSEALGOENGINE config/system.yaml: "
                              "broker.brokers.shoonya.orders: false) - run scripts\\windows\\setup_shoonya.ps1 "
                              "-TestOrder -Enable first")
        self.ad = make_broker("shoonya", acfg, resolve_path(acfg, "cache_dir"))
        self.ad.login(interactive=False)                # static IP checked here when static_ip is configured
        self.ad.load_instruments()
        self.to_ticker = {}                             # nsealgo key "NSE:SBIN" -> trader ticker "NSE:SBIN-EQ"
        for sym, ins in (master or {}).items():
            self.to_ticker[M.eq(sym)] = ins["ticker"]

    def key(self, ticker: str) -> str:
        sym = ticker.split(":", 1)[-1]
        base, _, series = sym.rpartition("-")
        return self.M.eq(base if series in ("EQ", "BE", "BZ") and base else sym)

    def ticker(self, key: str) -> str:
        return self.to_ticker.get(key) or f"{key}-EQ"

    def _call(self, what, fn):
        try:
            return fn()
        except TraderError:
            raise
        except Exception as ex:
            raise TraderError(f"shoonya {what}: {type(ex).__name__}: {ex}") from ex

    # -- reads ---------------------------------------------------------------------------------------------------
    def profile(self):
        uid = self.bcfg.get("user_id", "?")
        return {"name": f"Shoonya {uid}", "fy_id": uid}

    def verify_static_ip(self) -> str:
        want = (self.bcfg.get("static_ip") or "").strip()
        if not want:
            raise TraderError("require_static_ip: set static_ip for shoonya in NSEALGOENGINE config/system.yaml "
                              "(\"${STATIC_IP:-}\") and the STATIC_IP / STATICIP_PROXY environment variables")
        return f"{want} (checked by the Shoonya login)"

    def funds_available(self) -> float:
        d = self._call("limits", lambda: self.ad._client.get_limits()) or {}
        if d.get("stat") != "Ok":
            raise TraderError(f"shoonya limits: {d.get('emsg') or d}")
        return fnum(d.get("cash"), 0) + fnum(d.get("payin"), 0) - fnum(d.get("marginused"), 0)

    def holdings(self) -> dict:
        out = {}
        for p in self._call("positions", self.ad.get_positions):
            if p.product == "CNC":
                t = self.ticker(p.key)
                out[t] = out.get(t, 0) + int(p.qty)
        return out

    def orders(self) -> dict:
        out = {}
        for o in self._call("order book", self.ad.get_orders):
            out[str(o.broker_id)] = {"status": self.STATUS.get(o.status.value, "transit"), "filled": int(o.filled_qty),
                                     "price": float(o.avg_price or 0), "tag": o.tag or "", "message": o.message or "",
                                     "symbol": self.ticker(o.key), "side": 1 if o.side == self.M.Side.BUY else -1}
        return out

    def quotes(self, tickers) -> dict:
        out = {}
        tickers = list(dict.fromkeys(tickers))
        for q in self._call("quotes", lambda: self.ad.get_quotes([self.key(t) for t in tickers])):
            out[self.ticker(q.key)] = {"ltp": float(q.ltp), "prev_close": fnum(q.prev_close), "open": fnum(q.open),
                                       "high": fnum(q.high), "low": fnum(q.low),
                                       "lower_ckt": fnum(q.lower_circuit), "upper_ckt": fnum(q.upper_circuit)}
        return out

    def circuit(self, ticker) -> tuple[float, float] | None:
        q = self.quotes([ticker]).get(ticker) or {}
        lo, hi = fnum(q.get("lower_ckt"), 0), fnum(q.get("upper_ckt"), 0)
        return (lo, hi) if lo > 0 and hi > lo else None

    # -- writes --------------------------------------------------------------------------------------------------
    def place(self, ticker, side, otype, limit, stop, tag, qty) -> str:
        """Same contract as Fyers.place: order id; on a lost reply the order book is searched by tag (remarks)."""
        if self.dry:
            return f"DRY-{tag}"
        M = self.M
        req = M.OrderRequest(self.key(ticker), M.Side.BUY if side == 1 else M.Side.SELL, int(qty),
                             M.OrderType[self.OTYPE[otype]], M.Product.CNC, price=float(limit or 0) or None,
                             trigger=float(stop) if stop else None, strategy="swing")
        try:
            return str(self.ad.place_order(req, tag))
        except Exception as ex:
            msg = str(ex)
            if "place_order failed" in msg:            # Shoonya answered with a rejection (emsg): not uncertain
                raise TraderError(f"order {tag} {ticker} rejected: {msg}") from ex
            for _ in range(3):
                time.sleep(1.0)
                try:
                    for oid, o in self.orders().items():
                        if o["tag"] == tag:
                            return oid
                except Exception:
                    pass
            return f"UNCERTAIN-{tag}"

    def cancel(self, oid: str) -> bool:
        if self.dry or oid.startswith(("DRY-", "UNCERTAIN-")):
            return True
        try:
            self.ad.cancel_order(oid)
            return True
        except Exception as ex:
            log.warning("shoonya cancel %s: %s", oid, ex)
            return False


# =================================================================================================================
# symbol master: Fyers ticker + tick size per NSE symbol
# =================================================================================================================
def symbol_master() -> dict:
    cache = STATE_DIR / "NSE_CM.csv"
    if not cache.exists() or datetime.fromtimestamp(cache.stat().st_mtime).date() != date.today():
        try:
            r = requests.get(SYMBOL_MASTER, timeout=30)
            r.raise_for_status()
            atomic_write(cache, r.text)
        except Exception as ex:
            if not cache.exists():
                raise TraderError(f"cannot download the Fyers symbol master: {ex}")
            log.warning("symbol master download failed (%s) - using the cached copy", ex)
    rank = {"EQ": 0, "BE": 1, "BZ": 2}
    out = {}
    for row in csv.reader(io.StringIO(cache.read_text(encoding="utf-8"))):
        if len(row) < 10 or not row[9].startswith("NSE:"):
            continue
        base, _, series = row[9][4:].rpartition("-")
        if series not in rank:
            continue
        cur = out.get(base)
        if cur is None or rank[series] < rank[cur[2]]:
            out[base] = (row[9], fnum(row[4], 0.05) or 0.05, series)
    return {k: {"ticker": v[0], "tick": v[1]} for k, v in out.items()}


# =================================================================================================================
# state + journal
# =================================================================================================================
def load_state() -> dict:
    if STATE_FILE.exists():
        st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    else:
        st = {}
    for k, v in (("positions", {}), ("orders", {}), ("armed", {}), ("realized", 0.0), ("flags", {}),
                 ("equity_marks", {}), ("seq", 0), ("sessions", {})):
        st.setdefault(k, v)
    return st


def save_state(st: dict) -> None:
    atomic_write(STATE_FILE, json.dumps(st, indent=1, default=str))


JOURNAL_COLS = ["pid", "symbol", "strategy", "exit_mode", "signal_date", "entry_date", "entry_price", "exit_date",
                "exit_price", "exit_reason", "qty", "sessions_held", "gross_pnl", "return_pct", "est_charges",
                "net_pnl", "stop", "target", "bt_win_pct", "bt_avg_ret_pct", "bt_profit_factor"]


def journal(row: dict) -> None:
    JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    new = not JOURNAL_FILE.exists()
    with JOURNAL_FILE.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=JOURNAL_COLS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


# =================================================================================================================
# the trader
# =================================================================================================================
class Trader:
    def __init__(self, cfg: dict, dry: bool):
        self.cfg, self.dry = cfg, dry or not cfg.get("live")
        self.sig_dir = signals_dir(cfg)
        self.cal = Calendar(self.sig_dir)
        self.st = load_state()
        self.today = now().date()
        self.fy: Fyers | None = None
        self.master: dict = {}
        self.quotes: dict = {}
        self.fails: dict = {}                          # (kind, symbol) -> rejections today
        self.bands: dict = {}                          # symbol -> {"lower", "upper", "late"} circuit band today
        self.soft_only: set = set()                    # positions protected by the software stop only today
        self.protection: dict = {}                     # pid -> how it is protected (status file)

    # ---------------------------------------------------------------------------------------------------- utils
    def tag(self, kind: str) -> str:
        self.st["seq"] += 1
        return f"NU{self.today:%y%m%d}{kind}{self.st['seq'] % 100000:05d}"

    def save(self):
        if not self.dry:
            save_state(self.st)

    def inst(self, sym):
        return self.master.get(sym)

    def committed(self) -> float:
        c = sum(p["entry_price"] * p["qty"] for p in self.st["positions"].values())
        c += sum(o["price"] * o["qty"] for o in self.st["orders"].values() if o["kind"] == "entry"
                 and o["status"] in ("open", "uncertain"))
        c += sum(a["limit"] * a["qty"] for a in self.st["armed"].values())
        return c

    def heat(self) -> float:
        """Rs at risk if every open position and open buy hit its stop."""
        h = sum(max(0.0, p["entry_price"] - p["stop"]) * p["qty"] for p in self.st["positions"].values())
        h += sum(max(0.0, o["price"] - fnum(o.get("signal", {}).get("stop"))) * o["qty"]
                 for o in self.st["orders"].values() if o["kind"] == "entry" and o["status"] in ("open", "uncertain"))
        h += sum(max(0.0, a["limit"] - fnum(a["signal"].get("stop"))) * a["qty"] for a in self.st["armed"].values())
        return h

    def size(self, r, px) -> tuple[int, str | None]:
        """Shares for a signal, or 0 and the reason. Risk sizing: qty = risk budget / (entry - stop)."""
        cfg = self.cfg
        if cfg.get("sizing", "risk") == "value":
            q = math.floor(fnum(cfg.get("value_per_trade")) / px)
            return (q, None) if q >= 1 else (0, f"price {px:,.0f} > value_per_trade")
        if cfg.get("sizing", "risk") != "risk":
            return int(cfg["qty_per_trade"]), None
        stop = fnum(r.get("stop"))
        per = px - stop
        if per <= 0:
            return 0, "stop not below entry"
        if per / px * 100 > cfg["max_stop_pct"]:
            return 0, f"stop {per / px * 100:.0f}% away (> max_stop_pct, suspect)"
        mult = cfg["regime_risk_multiplier"].get(str(r.get("regime")), 1.0)
        if mult <= 0:
            return 0, f"regime {r.get('regime')}: no new risk"
        risk_rs = cfg["capital"] * cfg["risk_per_trade_pct"] / 100 * mult
        qty = math.floor(risk_rs / per)
        qty = min(qty, math.floor(cfg["capital"] * cfg["max_position_pct"] / 100 / px))
        if qty < 1:
            return 0, f"1 share risks Rs {per:,.0f} > the Rs {risk_rs:,.0f} risk budget"
        if qty * px < cfg["min_position_value"]:
            return 0, f"position Rs {qty * px:,.0f} < min_position_value (stop too wide for the risk budget)"
        return qty, None

    def held_symbols(self) -> set:
        s = {p["symbol"] for p in self.st["positions"].values()}
        s |= {o["symbol"] for o in self.st["orders"].values() if o["kind"] == "entry" and o["status"] in ("open", "uncertain")}
        s |= {a["symbol"] for a in self.st["armed"].values()}
        return s

    def pnl(self) -> tuple[float, float]:
        """(total P&L = realised + open, open P&L)"""
        open_pnl = 0.0
        for p in self.st["positions"].values():
            q = self.quotes.get(p["ticker"])
            if q:
                open_pnl += (q["ltp"] - p["entry_price"]) * p["qty"]
        return self.st["realized"] + open_pnl, open_pnl

    def connect(self):
        broker = str(self.cfg.get("broker", "fyers")).lower()
        held = self.st.setdefault("broker", "fyers")      # positions opened before 16 Oct 2026 are at Fyers
        if self.st["positions"] and held != broker:
            raise TraderError(f"the {len(self.st['positions'])} open position(s) are at {held} but the config says "
                              f"broker {broker} - set \"broker\": \"{held}\" until they are closed")
        if broker == "shoonya":
            self.master = symbol_master()                 # ticker names + tick sizes (public Fyers file)
            self.fy = Shoonya(self.cfg, self.dry, self.master)
            prof = self.fy.profile()
            log.info("Shoonya: %s  mode: %s", prof["fy_id"], "DRY RUN - nothing is sent" if self.dry else "LIVE")
            if not self.dry and self.cfg.get("require_static_ip", True):
                log.info("static IP verified: %s", self.fy.verify_static_ip())
            self.st["broker"] = broker
            return
        if broker != "fyers":
            raise TraderError(f"unknown broker {broker!r} in the config (fyers | shoonya)")
        try:
            self.fy = Fyers(self.cfg, self.dry)
            prof = self.fy.profile()
        except FyersTokenMissing:
            log.info("no token for today - running the token manager")
            subprocess.run([sys.executable, str(HERE / "fyers_token_manager.py")], cwd=str(HERE))
            self.fy = Fyers(self.cfg, self.dry)
            prof = self.fy.profile()
        log.info("Fyers: %s (%s)  mode: %s", prof.get("name", "?"), prof.get("fy_id", "?"),
                 "DRY RUN - nothing is sent" if self.dry else "LIVE")
        if not self.dry and self.cfg.get("require_static_ip", True):
            log.info("static IP verified: %s", self.fy.verify_static_ip())
        self.master = symbol_master()

    # ---------------------------------------------------------------------------------------------------- plan
    def session_files(self, day: date):
        sig = self.sig_dir / f"signals_for_{day:%Y%m%d}.csv"
        ex = self.sig_dir / f"exits_for_{day:%Y%m%d}.csv"
        meta = self.sig_dir / f"session_{day:%Y%m%d}.json"
        return sig, ex, meta

    def load_plan(self, day: date):
        sig_p, ex_p, meta_p = self.session_files(day)
        if not (sig_p.exists() and ex_p.exists() and meta_p.exists()):
            return None
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        with sig_p.open(encoding="utf-8") as f:
            sigs = list(csv.DictReader(f))
        with ex_p.open(encoding="utf-8") as f:
            exits = {(r["strategy"], r["symbol"]) for r in csv.DictReader(f)}
        return {"meta": meta, "signals": sigs, "exits": exits}

    def pick_entries(self, plan, cash: float | None = None) -> tuple[list, list]:
        """Signals to buy now (each with its 'qty'), and the reasons the others were skipped. Order of preference:
        your backtested book, then the highest backtest profit factor, then the most strategies agreeing."""
        cfg, take, skipped = self.cfg, [], []
        allowed_strats = None if cfg["strategies"] == "all" else set(cfg["strategies"])
        agree = {}
        for r in plan["signals"]:
            if r.get("regime_allowed") == "True":
                agree.setdefault(r["symbol"], set()).add(r["strategy"])
        rows = sorted(plan["signals"], key=lambda r: (r.get("in_book") != "True",
                                                       -fnum(r.get("bt_profit_factor"), 0),
                                                       -len(agree.get(r["symbol"], ())),
                                                       fnum(r.get("trigger_or_limit"))))
        seen, held = set(), self.held_symbols()
        budget = cfg["capital"] - self.committed()
        if cash is not None:
            budget = min(budget, cash * 0.98)          # keep 2% of the cash for charges
        heat_cap = cfg["capital"] * cfg["max_portfolio_heat_pct"] / 100
        heat = self.heat()
        n_new = sum(1 for o in self.st["orders"].values() if o["kind"] == "entry" and o["day"] == f"{self.today}")
        n_pos = len(self.st["positions"]) + len(self.st["armed"]) + sum(
            1 for o in self.st["orders"].values() if o["kind"] == "entry" and o["status"] in ("open", "uncertain"))
        block = self.entries_blocked()
        for r in rows:
            sym, strat = r["symbol"], r["strategy"]
            px = fnum(r.get("limit_price") or r.get("trigger_or_limit"))
            qty, why = 0, None
            in_book = r.get("in_book") == "True"
            if block:
                why = block
            elif sym in seen:
                why = "duplicate (another strategy already takes it)"
            elif allowed_strats is not None and strat not in allowed_strats:
                why = "strategy not enabled (no backtested edge)"
            elif not in_book and fnum(r.get("bt_profit_factor"), 0) < cfg["min_bt_profit_factor"]:
                why = f"backtest profit factor {fnum(r.get('bt_profit_factor'), 0):.2f} < {cfg['min_bt_profit_factor']}"
            elif cfg["regime_filter"] and r.get("regime_allowed") != "True":
                why = f"regime {r.get('regime')} blocks {strat}"
            elif sym in held:
                why = "already held / pending"
            elif cfg.get("allow_symbols") and sym not in cfg["allow_symbols"]:
                why = "not in allow_symbols"
            elif sym in cfg["exclude_symbols"]:
                why = "excluded in config"
            elif not self.inst(sym):
                why = "not in the Fyers symbol master"
            elif px > cfg["max_price_per_share"]:
                why = f"price {px:,.0f} > max_price_per_share"
            else:
                qty, why = self.size(r, px)
                if not why:
                    risk = (px - fnum(r.get("stop"))) * qty
                    if px * qty > budget:
                        why = "capital / cash limit"
                    elif heat + risk > heat_cap:
                        why = f"portfolio heat: Rs {heat:,.0f} at risk already (cap Rs {heat_cap:,.0f})"
                    elif n_pos >= cfg["max_open_positions"]:
                        why = "max_open_positions"
                    elif n_new >= cfg["max_new_entries_per_day"]:
                        why = "max_new_entries_per_day"
            if why:
                skipped.append((sym, strat, why))
                continue
            seen.add(sym)
            budget -= px * qty
            heat += (px - fnum(r.get("stop"))) * qty
            n_new += 1
            n_pos += 1
            take.append({**r, "qty": qty, "agree": sorted(agree.get(sym, ()))})
        return take, skipped

    def exit_reason(self, p, plan) -> str | None:
        if self.st["flags"].get("flatten"):
            return "flatten"
        if p["exit_mode"] == EXIT_RULE and plan and (p["strategy"], p["symbol"]) in plan["exits"]:
            return "exit_rule"
        held = self.cal.sessions_between(date.fromisoformat(p["entry_date"]), self.today)
        if p["exit_mode"] == EXIT_RULE and p.get("max_hold") and held >= int(p["max_hold"]):
            return "time_stop"
        if self.today >= date.fromisoformat(p["time_exit_date"]):
            return "time_3m"
        return None

    def entries_blocked(self) -> str | None:
        f = self.st["flags"]
        if KILL_FILE.exists() or f.get("killed"):
            return "kill switch"
        if f.get("dd_block"):
            return f"drawdown block ({f['dd_block']})"
        if f.get("day_block") == f"{self.today}":
            return "daily loss limit"
        return None

    # ---------------------------------------------------------------------------------------------------- orders
    def send(self, kind, sym, side, otype, limit, stop, pid=None, extra=None) -> str | None:
        ins = self.inst(sym)
        tag = self.tag({"entry": "E", "exit": "X", "stop": "S", "target": "T"}[kind])
        qty = (extra or {}).get("qty") or self.cfg["qty_per_trade"]
        desc = (f"{kind:6s} {'BUY ' if side == 1 else 'SELL'} {qty} {sym:12s} "
                f"{ {1: 'LIMIT', 2: 'MARKET', 3: 'SL-M', 4: 'SL-LMT'}[otype]:6s} "
                f"limit {limit:>10.2f}" + (f" trigger {stop:>10.2f}" if stop else "") + f"  [{tag}]")
        try:
            oid = self.fy.place(ins["ticker"], side, otype, limit, stop, tag, qty)
        except TraderError as ex:
            msg = str(ex)
            if side == -1 and ("ddpi" in msg.lower() or "edis" in msg.lower() or "tpin" in msg.lower()
                               or "authoriz" in msg.lower()):
                msg += "  -> enable DDPI on Fyers (or authorise eDIS) so the API can sell from your demat"
            log.error("REJECTED %s: %s", desc, msg)
            self.fails[(kind, sym)] = self.fails.get((kind, sym), 0) + 1
            return None
        log.info("%s %s -> %s", "WOULD PLACE" if self.dry else "PLACED", desc, oid)
        self.st["orders"][oid] = {"kind": kind, "symbol": sym, "ticker": ins["ticker"], "side": side, "type": otype,
                                  "price": limit, "stop": stop, "qty": qty, "tag": tag, "pid": pid,
                                  "status": "uncertain" if oid.startswith("UNCERTAIN") else "open",
                                  "day": f"{self.today}", "placed": now().isoformat(timespec="seconds"),
                                  **(extra or {})}
        self.save()
        return oid

    def live_order(self, pid, kind):
        for oid, o in self.st["orders"].items():
            if o["pid"] == pid and o["kind"] == kind and o["status"] in ("open", "uncertain"):
                return oid
        return None

    def gave_up(self, kind, sym) -> bool:
        n = self.fails.get((kind, sym), 0)
        if n == 3:
            log.critical("%s order for %s rejected 3 times today - no more broker %s orders today%s", kind, sym, kind,
                         "; the SOFTWARE STOP protects it while the trader runs" if kind == "stop" else
                         ". CHECK THIS POSITION YOURSELF.")
            self.fails[(kind, sym)] = 4
        return n >= 3

    # ---- circuit band / stop protection --------------------------------------------------------------------------
    def late_window(self) -> bool:
        """15:15-15:30: Fyers cancels pending stops at 15:15 and only accepts prices near the LTP -> software stop."""
        return at(self.today, 15, 15) <= now() < at(self.today, 15, 30)

    def band(self, sym) -> dict | None:
        b = self.bands.get(sym)
        if b is not None and (b or not self.dry):
            return b or None
        b = {}
        if not self.dry:
            try:
                c = self.fy.circuit(self.inst(sym)["ticker"])
                if c:
                    b = {"lower": c[0], "upper": c[1], "late": False}
            except Exception as ex:                    # never let a data call block protection
                log.warning("circuit band %s: %s", sym, ex)
        self.bands[sym] = b
        return b or None

    def learn_band(self, sym, msg) -> None:
        m = re.search(r"LowerCircuit:INR\s*([\d,\.]+)\s*UpperCircuit:INR\s*([\d,\.]+)", msg or "")
        if m:
            lo, hi = (float(x.replace(",", "")) for x in m.groups())
            self.bands[sym] = {"lower": lo, "upper": hi, "late": now() >= at(self.today, 15, 15)}
            log.info("circuit band %s from Fyers: %.2f - %.2f", sym, lo, hi)

    def clamp_sell(self, sym, lim, ref) -> float:
        """A sell limit no lower than the lower circuit (Fyers rejects it otherwise) - never above the price itself."""
        b = self.band(sym)
        if b and lim < b["lower"] <= ref:
            return ceil_tick(b["lower"], self.inst(sym)["tick"])
        return lim

    def place_stop(self, pid):
        p = self.st["positions"][pid]
        if self.live_order(pid, "stop") or self.live_order(pid, "exit") or self.live_order(pid, "target"):
            return
        if self.late_window() or pid in self.soft_only:
            return
        if self.gave_up("stop", p["symbol"]) or self.gave_up("exit", p["symbol"]):
            return
        ins = self.inst(p["symbol"])
        q = self.quotes.get(p["ticker"], {})
        trig = floor_tick(p["stop"], ins["tick"])
        if q and q["ltp"] <= trig:                       # already through the stop (gap): get out now
            self.exit_now(pid, "stop")
            return
        lim = floor_tick(trig * (1 - self.cfg["stop_limit_buffer_pct"] / 100), ins["tick"])
        b = self.band(p["symbol"])
        if b and not b.get("late"):
            if trig <= b["lower"]:
                self.soft_only.add(pid)
                log.warning("%s: stop %.2f is at/below today's lower circuit %.2f - no broker stop possible; the "
                            "SOFTWARE STOP protects it", p["symbol"], trig, b["lower"])
                return
            lim = max(lim, ceil_tick(b["lower"], ins["tick"]))
        self.send("stop", p["symbol"], -1, 4, lim, trig, pid, {"reason": "stop", "qty": p["qty"]})

    def exit_now(self, pid, reason):
        p = self.st["positions"][pid]
        if self.live_order(pid, "exit") or self.gave_up("exit", p["symbol"]):
            return
        for kind in ("stop", "target"):
            oid = self.live_order(pid, kind)
            if oid and self.fy.cancel(oid):
                self.st["orders"][oid]["status"] = "cancelling"
        ins = self.inst(p["symbol"])
        q = self.quotes.get(p["ticker"], {})
        ref = q.get("ltp") or q.get("prev_close") or p["entry_price"]
        lim = self.clamp_sell(p["symbol"], floor_tick(ref * (1 - self.cfg["exit_protection_pct"] / 100), ins["tick"]), ref)
        self.send("exit", p["symbol"], -1, 1, lim, 0, pid, {"reason": reason, "qty": p["qty"]})

    # ---------------------------------------------------------------------------------------------------- fills
    def open_position(self, o, price):
        sig = o.get("signal", {})
        pid = f"{o['symbol']}_{sig.get('strategy', 'x')}_{self.today:%Y%m%d}"
        variant = sig.get("variant", "rules_stop")
        self.st["positions"][pid] = {
            "pid": pid, "symbol": o["symbol"], "ticker": o["ticker"], "qty": o["qty"],
            "strategy": sig.get("strategy"), "variant": variant,
            "exit_mode": EXIT_TARGET if variant.startswith("target") else EXIT_RULE,
            "signal_date": sig.get("signal_date"), "entry_date": f"{self.today}", "entry_price": price,
            "stop": fnum(sig.get("stop")), "target": fnum(sig.get("target")),
            "max_hold": int(fnum(sig.get("max_hold"), 63)),
            "time_exit_date": f"{self.cal.add_months_session(self.today, self.cfg['time_exit_months'])}",
            "bt": {k: sig.get(k) for k in ("bt_win_pct", "bt_avg_ret_pct", "bt_profit_factor")},
        }
        log.info("BOUGHT %s %d @ %.2f (%s, %s exit, stop %.2f%s)", o["symbol"], o["qty"], price, sig.get("strategy"),
                 self.st["positions"][pid]["exit_mode"], fnum(sig.get("stop")),
                 f", target {fnum(sig.get('target')):.2f}" if variant.startswith("target") else "")
        return pid

    def close_position(self, pid, price, reason):
        p = self.st["positions"].pop(pid, None)
        if not p:
            return
        gross = (price - p["entry_price"]) * p["qty"]
        ch = self.cfg["est_charges_per_round_trip"]
        self.st["realized"] += gross
        for oid, o in self.st["orders"].items():            # cancel what is left for it
            if o["pid"] == pid and o["status"] in ("open", "uncertain"):
                if self.fy.cancel(oid):
                    o["status"] = "cancelled"
        journal({**p, "exit_date": f"{self.today}", "exit_price": price, "exit_reason": reason,
                 "sessions_held": self.cal.sessions_between(date.fromisoformat(p["entry_date"]), self.today),
                 "gross_pnl": round(gross, 2), "return_pct": round((price / p["entry_price"] - 1) * 100, 2),
                 "est_charges": ch, "net_pnl": round(gross - ch, 2), **(p.get("bt") or {})})
        log.info("SOLD %s @ %.2f (%s): %+.2f (%+.2f%%)", p["symbol"], price, reason, gross,
                 (price / p["entry_price"] - 1) * 100)

    def reconcile(self):
        """Apply the order book: fills open / close positions, rejections are logged."""
        if self.dry:
            return
        book = self.fy.orders()
        by_tag = {o["tag"]: oid for oid, o in book.items() if o["tag"]}
        for oid in list(self.st["orders"]):
            o = self.st["orders"][oid]
            if o["status"] not in ("open", "uncertain", "cancelling"):
                continue
            if oid.startswith("UNCERTAIN-"):
                real = by_tag.get(o["tag"])
                if not real:
                    if (now() - datetime.fromisoformat(o["placed"])).total_seconds() > 120:
                        o["status"] = "lost"
                        log.warning("order %s never reached Fyers - dropped", o["tag"])
                    continue
                self.st["orders"][real] = self.st["orders"].pop(oid)
                oid, o = real, self.st["orders"][real]
                o["status"] = "open"
            b = book.get(oid)
            if not b or b["status"] in ("pending", "transit"):
                continue
            if o["kind"] == "entry" and b["status"] in ("cancelled", "expired") and b.get("filled", 0) > 0:
                log.warning("entry %s %s: partly filled (%d of %d) before it was %s - holding %d", o["symbol"],
                            o["tag"], b["filled"], o["qty"], b["status"], b["filled"])
                b = {**b, "status": "filled"}
            if o["kind"] == "entry" and b["status"] == "filled" and 0 < b.get("filled", 0) < o["qty"]:
                o["qty"] = b["filled"]
            if b["status"] == "filled":
                o["status"], o["fill"] = "filled", b["price"]
                if o["kind"] == "entry":
                    if o.get("armed"):
                        self.st["armed"].pop(o["armed"], None)
                    pid = self.open_position(o, b["price"])
                    if now() >= at(self.today, 9, 15):
                        self.place_stop(pid)
                elif o["pid"] in self.st["positions"]:
                    self.close_position(o["pid"], b["price"], o.get("reason") or o["kind"])
            elif b["status"] in ("rejected", "cancelled", "expired"):
                was = o["status"]
                o["status"] = b["status"]
                if b["status"] == "rejected":
                    log.error("Fyers rejected %s %s %s: %s", o["kind"], o["symbol"], o["tag"], b["message"])
                    self.learn_band(o["symbol"], b["message"])
                    self.fails[(o["kind"], o["symbol"])] = self.fails.get((o["kind"], o["symbol"]), 0) + 1
                elif was != "cancelling":
                    log.info("%s %s %s: %s", o["kind"], o["symbol"], o["tag"], b["status"])
        self.save()

    def check_accounts(self):
        """Every share the trader thinks it holds must be in the account; otherwise stop managing it."""
        if self.dry or not self.st["positions"]:
            return
        have = self.fy.holdings()
        need = {}
        for p in self.st["positions"].values():
            need[p["ticker"]] = need.get(p["ticker"], 0) + p["qty"]
        for t, n in need.items():
            if have.get(t, 0) < n:
                for pid in [k for k, p in self.st["positions"].items() if p["ticker"] == t]:
                    p = self.st["positions"].pop(pid)
                    self.st.setdefault("orphaned", []).append({**p, "found": have.get(t, 0), "on": f"{self.today}"})
                    log.error("%s: the account has %d share(s), the trader expected %d - no longer managed "
                              "(sold outside the trader?). Check it yourself.", p["symbol"], have.get(t, 0), n)
        self.save()

    def risk_checks(self):
        total, _ = self.pnl()
        f, cfg = self.st["flags"], self.cfg
        marks = self.st["equity_marks"]
        before = [k for k in marks if k < f"{self.today}"]
        prev = marks[max(before)] if before else 0.0
        if total <= -cfg["flatten_drawdown"] and not f.get("flatten"):
            f.update(flatten=True, killed=True, dd_block=f"P&L {total:,.0f}")
            log.critical("P&L %.0f reached -%s: SELLING EVERYTHING and stopping (python swing_trader.py resume to "
                         "restart)", total, cfg["flatten_drawdown"])
        elif total <= -cfg["stop_entries_drawdown"] and not f.get("dd_block"):
            f["dd_block"] = f"P&L {total:,.0f}"
            log.critical("P&L %.0f reached -%s: no new entries until `resume`", total, cfg["stop_entries_drawdown"])
        if total - prev <= -cfg["daily_loss_limit"] and f.get("day_block") != f"{self.today}":
            f["day_block"] = f"{self.today}"
            log.warning("today's P&L %.0f reached the daily loss limit - no new entries today", total - prev)
        if KILL_FILE.exists():
            body = KILL_FILE.read_text(encoding="utf-8", errors="ignore").lower()
            if "flatten" in body and not f.get("flatten"):
                f.update(flatten=True, killed=True)
                log.critical("kill switch with flatten: selling everything")
            elif not f.get("killed"):
                f["killed"] = True
                log.warning("kill switch: no new entries")
        if self.entries_blocked():
            for oid, o in self.st["orders"].items():           # pull unfilled buys
                if o["kind"] == "entry" and o["status"] == "open" and self.fy.cancel(oid):
                    o["status"] = "cancelling"
            self.st["armed"].clear()
        if f.get("flatten"):
            for pid in list(self.st["positions"]):
                self.exit_now(pid, "flatten")
        self.save()

    # ---------------------------------------------------------------------------------------------------- session
    def refresh_quotes(self, extra=()):
        tick = [p["ticker"] for p in self.st["positions"].values()] + list(extra)
        if tick:
            try:
                self.quotes.update(self.fy.quotes(tick))
            except Exception as ex:
                log.warning("quotes: %s", ex)

    def morning(self, keep_running=True):
        t = now()
        if not self.cal.is_session(self.today):
            log.info("%s is not a trading session - nothing to do", self.today)
            return
        if t >= at(self.today, 15, 30) and not self.dry:
            log.info("market closed for today")
            return
        self.connect()
        plan = self.load_plan(self.today)
        if plan is None:
            log.warning("no signal files for %s yet - running the evening scan now", self.today)
            self.run_scan()
            plan = self.load_plan(self.today)
        if plan and (plan["meta"].get("stale") or plan["meta"].get("session") != f"{self.today}"):
            log.error("signal files are stale (%s) - managing positions only, no new entries", plan["meta"])
            plan["signals"] = []
        if plan is None:
            log.error("no signals for today - managing positions only")
        sess = self.st["sessions"].setdefault(f"{self.today}", {})
        self.reconcile()
        self.check_accounts()
        # ---- phase A: exits and limit entries (pre-open 09:00-09:08 or immediately) -----------------------------
        while not self.dry and now() < at(self.today, 9, 0, 15):
            time.sleep(5)
        self.refresh_quotes([self.inst(r["symbol"])["ticker"] for r in (plan or {}).get("signals", [])
                             if self.inst(r["symbol"])])
        self.risk_checks()
        if not sess.get("phase_a"):
            cash = None
            try:
                cash = self.fy.funds_available()
                log.info("cash available on Fyers: Rs %s", f"{cash:,.0f}")
            except Exception as ex:
                log.warning("funds: %s - using the capital limit only", ex)
            for pid, p in list(self.st["positions"].items()):
                why = self.exit_reason(p, plan)
                if why:
                    self.exit_now(pid, why)
            take, skipped = self.pick_entries(plan, cash) if plan else ([], [])
            for r in take:
                ins = self.inst(r["symbol"])
                if r["order"] == "BUY-STOP-LIMIT":              # PEAD: armed buy-stop, placed at 09:15
                    key = f"{r['symbol']}_{r['strategy']}"
                    trig = ceil_tick(fnum(r["trigger_or_limit"]), ins["tick"])
                    self.st["armed"][key] = {"symbol": r["symbol"], "trigger": trig,
                                             "limit": max(trig, floor_tick(fnum(r["limit_price"]), ins["tick"])),
                                             "sessions_left": int(fnum(r.get("valid_sessions"), 3)),
                                             "qty": r["qty"],
                                             "signal": {**r, "signal_date": plan["meta"].get("signal_date")}}
                    log.info("armed buy-stop %s @ %.2f (limit %.2f) for %s sessions", r["symbol"],
                             self.st["armed"][key]["trigger"], self.st["armed"][key]["limit"],
                             self.st["armed"][key]["sessions_left"])
                    continue
                px = floor_tick(fnum(r["trigger_or_limit"]) * (1 + self.cfg["entry_chase_pct"] / 100), ins["tick"])
                self.send("entry", r["symbol"], 1, 1, px, 0, None,
                          {"qty": r["qty"], "signal": {**r, "signal_date": (plan["meta"] or {}).get("signal_date")}})
            for sym, strat, why in skipped:
                log.info("skip %-12s %-28s %s", sym, strat, why)
            log.info("phase A: %d entries placed, %d skipped, %d positions held", len(take), len(skipped),
                     len(self.st["positions"]))
            sess["phase_a"] = True
            self.save()
        if self.dry:
            self.dry_phase_b()
            return

        # ---- phase B (09:15): stops + armed buy-stops -----------------------------------------------------------
        while now() < at(self.today, 9, 15, 5):
            time.sleep(5)
        self.reconcile()
        if not sess.get("phase_b"):
            self.refresh_quotes([self.inst(a["symbol"])["ticker"] for a in self.st["armed"].values()])
            for pid in list(self.st["positions"]):
                self.place_stop(pid)
            self.place_armed()
            sess["phase_b"] = True
            self.save()
        if not sess.get("protection_check"):
            time.sleep(25)                              # let Fyers accept / reject the new stops
            self.reconcile()
            self.refresh_quotes()
            for pid in list(self.st["positions"]):      # one more try with the band learned from a rejection
                self.place_stop(pid)
            time.sleep(10)
            self.reconcile()
            self.check_protection()
            sess["protection_check"] = True
            self.save()

        # ---- phase C: monitor until the close ------------------------------------------------------------------
        while keep_running and now() < at(self.today, 15, 30):
            try:
                self.reconcile()
                self.refresh_quotes()
                self.risk_checks()
                self.watch_stops()
                self.watch_targets()
                for pid in list(self.st["positions"]):       # re-protect anything left without a stop
                    if not any(self.live_order(pid, k) for k in ("stop", "exit", "target")):
                        self.place_stop(pid)
                self.write_status()
            except (requests.RequestException, TraderError) as ex:
                log.warning("monitor: %s", ex)
            time.sleep(self.cfg["poll_seconds"])
        if keep_running:
            time.sleep(60)
            self.end_of_day()

    def dry_phase_b(self):
        for pid, p in self.st["positions"].items():
            log.info("would protect %s with a stop at %.2f", p["symbol"], p["stop"])
        for a in self.st["armed"].values():
            log.info("would arm buy-stop %s trigger %.2f limit %.2f", a["symbol"], a["trigger"], a["limit"])
        log.info("DRY RUN complete - nothing was sent, state not changed")

    def place_armed(self):
        for key, a in list(self.st["armed"].items()):
            if a.get("placed_on") == f"{self.today}":
                continue
            ins = self.inst(a["symbol"])
            q = self.quotes.get(ins["ticker"], {})
            ltp = q.get("ltp", 0)
            if ltp and ltp > a["limit"]:
                log.info("buy-stop %s: price %.2f already above the limit %.2f - not today", a["symbol"], ltp, a["limit"])
            elif ltp and ltp >= a["trigger"]:
                self.send("entry", a["symbol"], 1, 1, a["limit"], 0, None,
                          {"qty": a["qty"], "signal": a["signal"], "armed": key})
            else:
                self.send("entry", a["symbol"], 1, 4, a["limit"], a["trigger"], None,
                          {"qty": a["qty"], "signal": a["signal"], "armed": key})
            a["placed_on"] = f"{self.today}"

    def watch_stops(self):
        """A stop-limit that the price fell straight through (below its limit) stays unfilled: sell at the market
        price instead, so a position is never left unprotected after a crash.  SOFTWARE STOP: a position with no
        live broker stop (rejected, cancelled at 15:15, stop below the circuit) is sold when the price reaches its
        stop."""
        for pid, p in list(self.st["positions"].items()):
            oid = self.live_order(pid, "stop")
            q = self.quotes.get(p["ticker"])
            if not oid and q and p.get("stop", 0) > 0 and q["ltp"] <= p["stop"] and not self.live_order(pid, "exit"):
                log.warning("SOFTWARE STOP %s: %.2f <= stop %.2f (no live broker stop) - selling now", p["symbol"],
                            q["ltp"], p["stop"])
                self.exit_now(pid, "stop")
                continue
            if oid and q and q["ltp"] < self.st["orders"][oid]["price"]:
                log.warning("%s fell through its stop-limit (%.2f < %.2f) - selling now", p["symbol"], q["ltp"],
                            self.st["orders"][oid]["price"])
                self.exit_now(pid, "stop")

    def watch_targets(self):
        for pid, p in list(self.st["positions"].items()):
            if p["exit_mode"] != EXIT_TARGET or self.live_order(pid, "target") or self.live_order(pid, "exit"):
                continue
            q = self.quotes.get(p["ticker"])
            if not q or q["ltp"] < p["target"]:
                continue
            stop = self.live_order(pid, "stop")
            if stop:
                if not self.fy.cancel(stop):
                    continue
                self.st["orders"][stop]["status"] = "cancelling"
                time.sleep(1.0)
                self.reconcile()
                if pid not in self.st["positions"]:            # the stop filled meanwhile
                    continue
            ins = self.inst(p["symbol"])
            self.send("target", p["symbol"], -1, 1, ceil_tick(p["target"], ins["tick"]), 0, pid,
                      {"reason": "target", "qty": p["qty"]})

    def end_of_day(self):
        self.reconcile()
        for oid, o in self.st["orders"].items():
            if o["status"] in ("open", "uncertain", "cancelling"):
                o["status"] = "expired"
        for key, a in list(self.st["armed"].items()):
            filled = any(o.get("armed") == key and o["status"] == "filled" for o in self.st["orders"].values())
            a["sessions_left"] -= 1
            if filled or a["sessions_left"] <= 0:
                self.st["armed"].pop(key)
        # keep 10 days of order history
        cutoff = f"{self.today - timedelta(days=10)}"
        self.st["orders"] = {k: o for k, o in self.st["orders"].items() if o["day"] >= cutoff}
        self.st["sessions"] = {k: v for k, v in self.st["sessions"].items() if k >= cutoff}
        self.refresh_quotes()
        total, open_pnl = self.pnl()
        self.st["equity_marks"][f"{self.today}"] = round(total, 2)
        self.save()
        self.write_status()
        log.info("end of day: %d positions, P&L total %+.0f (open %+.0f, realised %+.0f)", len(self.st["positions"]),
                 total, open_pnl, self.st["realized"])

    def check_protection(self) -> list:
        """Every holding must have a broker stop, a working exit, or the software stop (needs a stop level and a
        live price). Anything else -> ALERT."""
        bad = []
        for pid, p in self.st["positions"].items():
            if self.live_order(pid, "stop"):
                o = self.st["orders"][self.live_order(pid, "stop")]
                how = f"broker stop {o.get('stop', 0):.2f} / limit {o['price']:.2f}"
            elif self.live_order(pid, "exit"):
                how = "exit order working"
            elif p.get("stop", 0) > 0 and self.quotes.get(p["ticker"]):
                how = f"SOFTWARE stop {p['stop']:.2f} (only while the trader runs)"
            else:
                how = "NONE"
                bad.append(p["symbol"])
            self.protection[pid] = how
            log.info("protection %-12s %s", p["symbol"], how)
        if bad:
            self.alert(f"NO STOP PROTECTION for {', '.join(bad)} - check them in the Fyers app")
        return bad

    def alert(self, text: str) -> None:
        log.critical("ALERT: %s", text)
        try:
            with (STATE_DIR / "ALERTS.txt").open("a", encoding="utf-8") as f:
                f.write(f"{now():%Y-%m-%d %H:%M:%S}  {text}\n")
        except OSError:
            pass
        if os.name == "nt" and not self.dry:
            try:                                        # Windows pop-up; msg.exe is missing on Home editions
                subprocess.run(["msg", "*", "/TIME:0", f"NSEUIENGINE swing trader: {text}"], timeout=10,
                               capture_output=True)
            except Exception:
                pass

    def write_status(self):
        total, open_pnl = self.pnl()
        atomic_write(STATUS_FILE, json.dumps({
            "at": now().isoformat(timespec="seconds"), "live": not self.dry, "positions": len(self.st["positions"]),
            "committed": round(self.committed(), 2), "pnl_total": round(total, 2), "pnl_open": round(open_pnl, 2),
            "realized": round(self.st["realized"], 2), "flags": self.st["flags"],
            "entries_blocked": self.entries_blocked(),
            "protection": {p["symbol"]: self.protection.get(pid, "?") for pid, p in self.st["positions"].items()}},
            indent=2))

    # ---------------------------------------------------------------------------------------------------- evening
    def run_scan(self):
        dh = data_home(self.cfg)
        log.info("running NSEDATAENGINE scan: %s nse_signals.py", self.cfg["data_python"])
        r = subprocess.run([self.cfg["data_python"], "nse_signals.py"], cwd=str(dh), timeout=3600)
        if r.returncode:
            log.error("nse_signals.py exited with %s", r.returncode)
        self.cal = Calendar(self.sig_dir)

    def evening(self, scan=True):
        if scan:
            self.run_scan()
        if self.cal.is_session(self.today) and now() >= at(self.today, 15, 35):
            try:
                self.fy = Fyers(self.cfg, True)
                self.refresh_quotes()
                total, _ = self.pnl()
                self.st["equity_marks"][f"{self.today}"] = round(total, 2)
                save_state(self.st)
            except Exception as ex:
                log.warning("equity mark: %s", ex)
        nxt = self.today + timedelta(days=1)
        while not self.cal.is_session(nxt):
            nxt += timedelta(days=1)
        plan = self.load_plan(nxt)
        if plan is None:
            log.error("no signal files for %s", nxt)
            return
        self.today_backup, self.today = self.today, nxt
        try:
            self.master = symbol_master()
            take, skipped = self.pick_entries(plan)
            exits = [(p["symbol"], self.exit_reason(p, plan)) for p in self.st["positions"].values()]
        finally:
            self.today = self.today_backup
        print(f"\nPlan for {nxt:%a %d %b %Y}  (regime {plan['meta'].get('regime')}, signals from "
              f"{plan['meta'].get('signal_date')}, mode {'LIVE' if self.cfg.get('live') else 'DRY RUN'})")
        print(f"  sell at the open: {', '.join(f'{s} ({w})' for s, w in exits if w) or 'nothing'}")
        print(f"  buy ({len(take)}):")
        print(f"    {'Symbol':12s} {'Strategy':28s} {'Order':15s} {'Price':>9s} {'Qty':>4s} {'Value':>8s} {'Stop':>9s}"
              f" {'Risk Rs':>8s} {'Target':>9s}  Also signalled by")
        cost = risk = 0.0
        for r in take:
            px, q = fnum(r["limit_price"] or r["trigger_or_limit"]), r["qty"]
            rk = (px - fnum(r["stop"])) * q
            cost, risk = cost + px * q, risk + rk
            others = [x for x in r.get("agree", []) if x != r["strategy"]]
            print(f"    {r['symbol']:12s} {r['strategy']:28s} {r['order']:15s} {px:>9.2f} {q:>4d} {px * q:>8,.0f} "
                  f"{fnum(r['stop']):>9.2f} {rk:>8,.0f} {fnum(r['target']) if r.get('variant', '').startswith('target') else 0:>9.2f}"
                  f"  {', '.join(others) or '-'}")
        print(f"  cost if all fill: Rs {cost:,.0f}   new risk: Rs {risk:,.0f}   open risk now: Rs {self.heat():,.0f} "
              f"(cap Rs {self.cfg['capital'] * self.cfg['max_portfolio_heat_pct'] / 100:,.0f})   capital: "
              f"Rs {self.cfg['capital']:,.0f}")
        reasons = {}
        for _, _, w in skipped:
            reasons[w.split(" (")[0]] = reasons.get(w.split(" (")[0], 0) + 1
        print(f"  skipped: {len(skipped)}  " + ", ".join(f"{k}: {v}" for k, v in sorted(reasons.items())) + "\n")


# =================================================================================================================
# status / report / kill
# =================================================================================================================
def cmd_status(cfg):
    st = load_state()
    print(f"mode: {'LIVE' if cfg.get('live') else 'DRY RUN'}   capital Rs {cfg['capital']:,}   realised P&L "
          f"{st['realized']:+,.2f}   flags: {st['flags'] or 'none'}   kill file: {KILL_FILE.exists()}")
    quotes = {}
    if st["positions"]:
        try:
            quotes = Fyers(cfg, True).quotes([p["ticker"] for p in st["positions"].values()])
        except Exception as ex:
            print(f"(no live prices: {ex})")
    print(f"\n{'Symbol':12s} {'Strategy':28s} {'Since':10s} {'Entry':>9s} {'LTP':>9s} {'P&L':>8s} {'Stop':>9s} "
          f"{'Target':>9s} Exit")
    tot = 0.0
    for p in sorted(st["positions"].values(), key=lambda x: x["entry_date"]):
        ltp = quotes.get(p["ticker"], {}).get("ltp", 0)
        pnl = (ltp - p["entry_price"]) * p["qty"] if ltp else 0
        tot += pnl
        print(f"{p['symbol']:12s} {p['strategy']:28s} {p['entry_date']:10s} {p['entry_price']:>9.2f} {ltp:>9.2f} "
              f"{pnl:>+8.2f} {p['stop']:>9.2f} {p['target'] if p['exit_mode'] == 'target' else 0:>9.2f} "
              f"{p['exit_mode']}")
    print(f"\n{len(st['positions'])} positions, open P&L {tot:+,.2f}; armed buy-stops: {len(st['armed'])}")
    if STATUS_FILE.exists():
        print(f"last session status: {STATUS_FILE.read_text(encoding='utf-8')}")


def cmd_report():
    if not JOURNAL_FILE.exists():
        print("no closed trades yet")
        return
    rows = list(csv.DictReader(JOURNAL_FILE.open(encoding="utf-8")))
    by = {}
    for r in rows:
        by.setdefault(r["strategy"], []).append(r)
    print(f"{'Strategy':28s} {'Trades':>6s} {'Win%':>6s} {'Avg%':>7s} {'PF':>6s} {'Net Rs':>9s}   backtest win/avg/PF")
    for s, rs in sorted(by.items()):
        ret = [fnum(r["return_pct"]) for r in rs]
        wins, losses = sum(x for x in ret if x > 0), -sum(x for x in ret if x < 0)
        pf = wins / losses if losses else float("inf")
        bt = rs[-1]
        print(f"{s:28s} {len(rs):>6d} {100 * sum(x > 0 for x in ret) / len(ret):>6.1f} {sum(ret) / len(ret):>+7.2f} "
              f"{pf:>6.2f} {sum(fnum(r['net_pnl']) for r in rs):>+9.2f}   {bt.get('bt_win_pct', '')}/"
              f"{bt.get('bt_avg_ret_pct', '')}/{bt.get('bt_profit_factor', '')}")


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")
    fh = logging.handlers.RotatingFileHandler(LOG_DIR / "swing_trader.log", maxBytes=5_000_000, backupCount=10,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if sys.stdout is not None:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        log.addHandler(sh)
    log.setLevel(logging.INFO)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("morning", help="run the trading session")
    p.add_argument("--dry-run", action="store_true", help="show the orders; send nothing, change nothing")
    p = sub.add_parser("evening", help="scan + plan for the next session")
    p.add_argument("--no-scan", action="store_true", help="plan from the existing signal files")
    sub.add_parser("status")
    sub.add_parser("report")
    p = sub.add_parser("kill", help="stop new entries (--flatten: also sell every position)")
    p.add_argument("--flatten", action="store_true")
    sub.add_parser("resume", help="clear kill / drawdown blocks")
    a = ap.parse_args(argv)
    setup_logging()
    cfg = load_config()
    try:
        if a.cmd == "morning":
            log.info("=" * 30 + f" swing_trader {__version__} morning " + "=" * 30)
            Trader(cfg, a.dry_run).morning()
        elif a.cmd == "evening":
            log.info("=" * 30 + f" swing_trader {__version__} evening " + "=" * 30)
            Trader(cfg, True).evening(scan=not a.no_scan)
        elif a.cmd == "status":
            cmd_status(cfg)
        elif a.cmd == "report":
            cmd_report()
        elif a.cmd == "kill":
            atomic_write(KILL_FILE, "flatten" if a.flatten else "stop entries")
            print(f"kill switch set ({'sell everything' if a.flatten else 'no new entries'}); the running session "
                  f"acts on it within {cfg['poll_seconds']}s. Undo: python swing_trader.py resume")
        elif a.cmd == "resume":
            st = load_state()
            st["flags"] = {}
            save_state(st)
            KILL_FILE.unlink(missing_ok=True)
            print("kill / drawdown blocks cleared")
        return 0
    except (TraderError, FyersTokenMissing, requests.RequestException) as ex:
        log.error("%s", ex)
        return 1
    except Exception as ex:
        log.exception("unexpected: %s", ex)
        return 1


if __name__ == "__main__":
    sys.exit(main())
