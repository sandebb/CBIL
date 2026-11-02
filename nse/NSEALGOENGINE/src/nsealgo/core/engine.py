"""The engine: ONE event loop shared by backtest, replay, forward test and live trading.

Per session: prefetch data -> regime & crowding (precomputed timelines in backtests) -> strategies start
-> loop (market data -> paper matching -> fill sync -> strategy callbacks -> timers -> risk monitor) ->
end-of-day persistence. Only the clock, the feed and the execution adapter differ between modes.

Performance design:
- data is fetched in bulk and cached: full daily history per instrument once, minute history as a
  rolling per-day cache (a new session loads one new day, not the whole lookback), cross-sectional
  panels (dates x symbols) for universe screens;
- intraday aggregates are incremental: session VWAP/sigma and 5-minute bars with band snapshots;
- regime and crowding states come from timelines computed once per backtest (HMM fits in parallel);
- an `entry_window` lets a period of a backtest run on its own core (see nsealgo.parallel);
- in live/forward mode, I/O-bound preparation (SQLite reads, broker history) runs on a thread pool.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import logging
import math
from collections import defaultdict, namedtuple
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from ..config import resolve_path
from ..data.events import EventStore
from ..data.history import HistoryStore
from ..data.universe import Universe
from .bars import BarBuilder
from .clock import IST, NSECalendar, RealClock, SimClock, at, hm
from .context import Context
from .costs import CostModel
from .crowding import CrowdingMonitor
from .models import Bar, default_instrument, eq, parse_key
from .notifier import Notifier
from .oms import OrderManager
from .portfolio import Portfolio
from .regime import RegimeEngine
from .risk import RiskManager
from .storage import Storage

log = logging.getLogger("nsealgo.engine")
Bar5 = namedtuple("Bar5", "ts open high low close volume vwap sd")


class Engine:
    def __init__(self, cfg: dict, *, strategies, execution, data=None, feed=None, clock=None, history=None,
                 storage=None, mode: str = "paper", granularity: str = "minute", notifier=None, run_dir=None,
                 regime_timeline: dict | None = None, crowding_timeline: dict | None = None,
                 entry_window: tuple | None = None, sizing: str = "dynamic", learn: bool = True,
                 breaker: bool = True, io_threads: int | None = None, fragility_timeline: dict | None = None):
        self.cfg, self.mode, self.granularity = cfg, mode, granularity
        self.clock = clock or (SimClock() if mode in ("backtest", "replay") else RealClock())
        self.calendar = NSECalendar(resolve_path(cfg, "holidays_file"))
        self.run_dir = Path(run_dir) if run_dir else resolve_path(cfg, "runs_dir") / mode
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.storage = storage or Storage(self.run_dir / "state.db")
        self.history = history or HistoryStore(resolve_path(cfg, "data_dir") / "history.db")
        self.execution, self.data, self.feed = execution, data or execution, feed
        self.notifier = notifier or Notifier(cfg.get("notifier"))
        self.costs = CostModel(cfg["costs"])
        self.portfolio = Portfolio(cfg["capital"])
        self.portfolio.restore(self.storage.kv_get("engine", "portfolio"))
        self.strategies = list(strategies)
        self.by_name = {s.name: s for s in self.strategies}
        self.universe = Universe(resolve_path(cfg, "universe_dir"))
        self.events = EventStore(resolve_path(cfg, "events_dir"), self.calendar)
        self.risk = RiskManager(cfg["risk"], self.portfolio, self.storage,
                                priors={s.name: s.prior for s in self.strategies},
                                horizons={s.name: s.horizon for s in self.strategies},
                                kill_file=self.run_dir / "KILL", sector_of=self.universe.sector,
                                sizing=sizing, learn=learn)
        self.oms = OrderManager(self.execution, self.risk, self.portfolio, self.storage, self.clock, cfg["oms"],
                                self.costs, self.instrument, self.last_price, self.notifier, quote=self.quotes_get)
        self.oms.listeners.append(self._on_fill)
        self.oms.trade_listeners.append(self._on_trade)
        self.regime = RegimeEngine(cfg["regime"])
        self.crowding = CrowdingMonitor(cfg["crowding"], breaker=breaker)
        self.regime_timeline, self.crowding_timeline = regime_timeline or {}, crowding_timeline or {}
        self.entry_window = entry_window
        self.fragility_timeline = fragility_timeline or {}
        self.fragility: dict = {}
        if io_threads is None:
            from ..parallel.hardware import workers_for
            pc = cfg.get("parallel", {})
            io_threads = workers_for("io", pc.get("io_threads")) if mode in ("forward", "live") else 1
        self.io_threads = max(1, int(io_threads))
        self.ctx = Context(self)
        from .book import BookSignals
        from .journal import SignalJournal
        self.book = BookSignals(cfg.get("book") or {})
        self.journal = SignalJournal()                  # signal -> order -> fill, for the desk's strategy windows
        self.disabled: set[str] = set(self.storage.kv_get("engine", "disabled", []) or [])
        self._states: dict[str, dict] = {}
        self._inst: dict = {}
        self._daily_full: dict[str, pd.DataFrame] = {}
        self._daily_cache: dict = {}
        self._panels: dict = {}
        self._mday: dict = {}                 # (key, day) -> that day's minute bars (rolling cache)
        self._minute_cache: dict = {}
        self._sessions_cache: dict = {}
        self._bars_df_cache: dict = {}
        self._fno: set[str] | None = None
        self._subs: set[str] = set()
        self._timers: list = []
        self._fired: set[int] = set()
        self._last_reconcile: datetime | None = None
        self._depth_at: datetime | None = None
        self.day: date | None = None
        self.quotes: dict = {}
        self.today_bars: dict[str, list[Bar]] = defaultdict(list)
        self.day_bars: dict[str, Bar] = {}
        self.vwap_acc: dict[str, list[float]] = {}
        self.bars5: dict[str, list[Bar5]] = defaultdict(list)
        self._agg5: dict[str, list] = {}
        self.bars = BarBuilder()
        if hasattr(self.execution, "seed_positions"):
            self.execution.seed_positions(self.portfolio.positions())

    def quotes_get(self, key: str):
        return self.quotes.get(key)

    # ---- instruments & prices --------------------------------------------------------------------
    def instrument(self, key: str):
        inst = self._inst.get(key)
        if inst is not None:
            return inst
        m = getattr(self.data, "master", None)
        if m is not None and key in m:
            inst = m.get(key)
        else:
            try:
                inst = self.execution.instrument(key)
            except Exception:
                inst = default_instrument(key)
        self._inst[key] = inst
        return inst

    def last_price(self, key: str) -> float | None:
        q = self.quotes.get(key)
        if q is not None and q.ltp:
            return float(q.ltp)
        bars = self.today_bars.get(key)
        if bars:
            return float(bars[-1].close)
        b = self.day_bars.get(key)
        if b is not None:
            return float(b.close)
        d = self.daily_df(key)
        return float(d["close"].iloc[-1]) if len(d) else None

    # ---- daily data -----------------------------------------------------------------------------
    def prefetch_daily(self, keys) -> None:
        missing = [k for k in dict.fromkeys(keys) if k not in self._daily_full]
        if missing:
            self._daily_full.update(self.history.daily_many(missing))

    def daily_df(self, key: str) -> pd.DataFrame:
        d = self._daily_cache.get(key)
        if d is not None:
            return d
        full = self._daily_full.get(key)
        if full is None:
            full = self.history.daily(key)
            self._daily_full[key] = full
        if self.day:
            end = pd.Timestamp(self.calendar.prev_trading_day(self.day))
            d = full.iloc[: full.index.searchsorted(end, side="right")]
        else:
            d = full
        self._daily_cache[key] = d
        return d

    def panel_full(self, keys, field: str) -> pd.DataFrame:
        ck = (field, tuple(keys))
        P = self._panels.get(ck)
        if P is None:
            P = self.history.daily_panel(list(keys), field)
            self._panels[ck] = P
        return P

    def panel(self, keys, field: str = "close", tail: int | None = None, with_today: bool = True) -> pd.DataFrame:
        """Dates x instruments matrix up to the previous session (+ today's provisional row)."""
        keys = list(keys)
        P = self.panel_full(keys, field)
        end = pd.Timestamp(self.calendar.prev_trading_day(self.day))
        i = P.index.searchsorted(end, side="right")
        sub = P.iloc[max(0, i - tail) if tail else 0: i]
        if with_today:
            row = {}
            for k in keys:
                tb = self.today_bar(k)
                if tb:
                    row[k] = tb[field]
            if row:
                sub = pd.concat([sub, pd.DataFrame([row], index=pd.DatetimeIndex([pd.Timestamp(self.day)]),
                                                   columns=sub.columns)])
        return sub

    # ---- minute data (rolling per-day cache) ------------------------------------------------------
    def prev_sessions(self, n: int) -> list[date]:
        c = self._sessions_cache.get(n)
        if c is None:
            out, d = [], self.day
            for _ in range(n):
                d = self.calendar.prev_trading_day(d)
                out.append(d)
            c = self._sessions_cache[n] = sorted(out)
        return c

    def prefetch_minute(self, keys, days: int) -> None:
        want = self.prev_sessions(days)
        todo = [k for k in dict.fromkeys(keys) if any((k, d) not in self._mday for d in want)]
        if not todo:
            return
        first = min(d for d in want if any((k, d) not in self._mday for k in todo))

        def load(chunk):
            return self.history.minute_many(chunk, first, want[-1])

        from ..parallel.pool import thread_map
        n = self.io_threads
        chunks = [todo[i::n] for i in range(n)] if n > 1 and len(todo) >= 2 * n else [todo]
        for got in thread_map(load, chunks, n):
            for k, df in got.items():
                days_idx = df.index.date if len(df) else []
                for d in want:
                    if (k, d) not in self._mday and d >= first:
                        self._mday[(k, d)] = df[days_idx == d] if len(df) else df

    def minute_hist(self, key: str, days: int) -> pd.DataFrame:
        ck = (key, days)
        if ck not in self._minute_cache:
            want = self.prev_sessions(days)
            if any((key, d) not in self._mday for d in want):
                self.prefetch_minute([key], days)
            frames = [self._mday[(key, d)] for d in want if len(self._mday.get((key, d), ()))]
            self._minute_cache[ck] = pd.concat(frames) if frames else pd.DataFrame(
                columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex([], tz=IST, name="ts"))
        return self._minute_cache[ck]

    def bars_df(self, key: str) -> pd.DataFrame:
        bars = self.today_bars.get(key) or []
        c = self._bars_df_cache.get(key)
        if c is not None and c[0] == len(bars):
            return c[1]
        df = pd.DataFrame([(b.ts, b.open, b.high, b.low, b.close, b.volume, b.ofi) for b in bars],
                          columns=["ts", "open", "high", "low", "close", "volume", "ofi"]).set_index("ts")
        self._bars_df_cache[key] = (len(bars), df)
        return df

    def today_bar(self, key: str) -> dict | None:
        b = self.day_bars.get(key)
        if b is not None:
            return {"open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
        q = self.quotes.get(key)
        if q is not None and q.open:
            return {"open": q.open, "high": q.high or q.ltp, "low": q.low or q.ltp, "close": q.ltp,
                    "volume": q.volume or 0.0}
        bars = self.today_bars.get(key)
        if bars:
            return {"open": bars[0].open, "high": max(x.high for x in bars), "low": min(x.low for x in bars),
                    "close": bars[-1].close, "volume": sum(x.volume for x in bars)}
        return None

    def snapshot(self, keys: list[str]) -> None:
        """Make today's provisional bar available for keys the feed isn't streaming."""
        missing = [k for k in keys if k not in self.quotes and k not in self.day_bars and not self.today_bars.get(k)]
        if not missing:
            return
        if self.mode in ("forward", "live"):
            try:
                for q in self.data.get_quotes(missing):
                    self.quotes[q.key] = q
                    self.portfolio.mark(q.key, q.ltp)
            except Exception as ex:
                log.warning("snapshot quotes failed: %s", ex)
        elif self.granularity == "daily":
            self.prefetch_daily(missing)             # full histories are cached in memory: no query per day
            ts = pd.Timestamp(self.day)
            for k in missing:
                full = self._daily_full.get(k)
                i = full.index.searchsorted(ts) if full is not None else 0
                if full is not None and i < len(full) and full.index[i] == ts:
                    o, h, l, c, v = full.iloc[i][["open", "high", "low", "close", "volume"]].to_numpy(float)
                    self.day_bars[k] = Bar(k, at(self.day, "09:15"), o, h, l, c, v, 0.0, "1d")
        else:
            now = self.clock.now()
            for k, m in self.history.minute_many(missing, self.day, self.day).items():
                m = m[m.index + pd.Timedelta(minutes=1) <= now]
                if len(m):
                    self.day_bars[k] = Bar(k, at(self.day, "09:15"), float(m["open"].iloc[0]), float(m["high"].max()),
                                           float(m["low"].min()), float(m["close"].iloc[-1]), float(m["volume"].sum()),
                                           0.0, "1d")

    # ---- universes & instruments -------------------------------------------------------------------
    def universe_keys(self, name) -> list[str]:
        if isinstance(name, (list, tuple)):
            return [k if ":" in k else eq(k) for k in name]
        if name == "fno":
            return sorted(self.fno_keys())
        return self.universe.load(name)

    def fno_keys(self) -> set[str]:
        if self._fno is None:
            self._fno = set(self.universe.load("fno"))       # liquid-stock list (config/universes/fno.csv)
        return self._fno

    def is_cas(self, key: str) -> bool:
        if parse_key(key)["kind"] != "EQ":
            return False
        fno = self.fno_keys()
        return key in fno if fno else True

    def ensure_subscribed(self, key: str) -> None:
        if key not in self._subs and self.feed is not None:
            self._subs.add(key)
            self.feed.subscribe(sorted(self._subs | set(self._watch_keys())))

    def can_enter(self) -> bool:
        if not self.entry_window:
            return True
        a, b = self.entry_window
        return a <= self.day <= b

    # ---- strategy state --------------------------------------------------------------------------
    def strategy_state(self, name: str) -> dict:
        if name not in self._states:
            self._states[name] = self.storage.kv_get("strategy", name, {}) or {}
        return self._states[name]

    def save_states(self) -> None:
        for name, st in self._states.items():
            self.storage.kv_set("strategy", name, st)

    def disable(self, name: str, reason: str) -> None:
        if name in self.disabled:
            return
        self.disabled.add(name)
        self.storage.kv_set("engine", "disabled", sorted(self.disabled))
        self.storage.log_event(self.clock.now(), "WARNING", "engine", f"strategy {name} disabled: {reason}")
        log.warning("strategy %s disabled: %s", name, reason)
        self.notifier.send(f"Strategy {name} disabled: {reason}")

    def active(self) -> list:
        return [s for s in self.strategies if s.name not in self.disabled
                and not (self.granularity == "daily" and s.horizon == "intraday")]

    def _guard(self, strategy, fn, *args) -> None:
        try:
            fn(self.ctx, *args)
        except Exception as ex:
            log.exception("strategy %s error in %s: %s", strategy.name, getattr(fn, "__name__", fn), ex)
            self.storage.log_event(self.clock.now(), "ERROR", strategy.name, f"{type(ex).__name__}: {ex}")

    # ---- session ---------------------------------------------------------------------------------
    def run_day(self, day: date) -> bool:
        if not self.calendar.is_trading_day(day):
            log.info("%s is not a trading day", day)
            return False
        s = self.cfg["session"]
        eod, open_t = at(day, s["eod"]), hm(s["open"])
        if self.clock.is_sim:
            self.clock.set(at(day, s["pre_market"]))
        self._begin_day(day)
        while True:
            if self.clock.is_sim:
                step = self.feed.step(self.clock)
                if step is None:
                    break
                quotes, bars = step
            else:
                now = self.clock.now()
                if now >= eod:
                    break
                if now.time() < open_t:
                    self._fire_timers(now)
                    self.clock.sleep(min(30.0, (at(day, open_t) - now).total_seconds()))
                    continue
                quotes, bars = self.feed.poll(now)
            now = self.clock.now()
            self._on_market(quotes, bars, now)
            self._fire_timers(now)
            self._housekeeping(now)
            if not self.clock.is_sim:
                self.storage.flush()
                self.clock.sleep(float(s.get("poll_seconds", 1.0)))
        if self.clock.is_sim:
            self.clock.set(eod)
        self._fire_timers(eod)
        self._end_day(day)
        return True

    def _begin_day(self, day: date) -> None:
        self.book.reset()
        jc = self.cfg.get("journal") or {}
        if jc.get("save", True) and self.mode in jc.get("modes", ("forward", "live", "replay")):
            self.journal.open_day(self.run_dir / "journal", day)       # reloads today's file after a restart
        else:
            self.journal.reset()
        self._journal_at = None
        self.day = day
        self._fired, self.quotes, self.day_bars, self.vwap_acc, self._agg5 = set(), {}, {}, {}, {}
        self.today_bars, self.bars5 = defaultdict(list), defaultdict(list)
        self.bars = BarBuilder()
        self._daily_cache, self._minute_cache, self._bars_df_cache, self._sessions_cache = {}, {}, {}, {}
        if hasattr(self.execution, "day_start"):
            self.execution.day_start(day)
        self.risk.day_start()
        active = self.active()
        needs = self._collect_needs(active)
        if self.mode in ("forward", "live"):
            self._refresh_history(day, needs)
        self._prefetch(needs)
        self._update_regime(day)
        self._update_fragility(day)
        self._update_crowding(day, active)
        for st in active:
            self._guard(st, st.on_session_start)
        subs = {p.key for p in self.portfolio.positions()}
        for st in active:
            try:
                st._subscribed = set(st.live_keys(self.ctx)) | {p.key for p in self.portfolio.positions(st.name)}
            except Exception as ex:
                log.exception("live_keys %s: %s", st.name, ex)
                st._subscribed = set()
            subs |= st._subscribed
        if hasattr(self.execution, "cas_keys"):
            fno = self.fno_keys()
            self.execution.cas_keys = set(fno) if fno else None
        self._subs = set(subs)
        if self.feed is not None:
            self.feed.subscribe(sorted(self._subs | set(self._watch_keys())))
        self._build_timers(active)
        self._update_depth_focus(self.clock.now(), force=True)
        log.info("session %s | regime=%s | equity=%.0f | strategies=%s | watching %d instruments", day,
                 self.regime.state, self.portfolio.equity, ",".join(s.name for s in active), len(self._subs))

    def _collect_needs(self, active) -> dict:
        n = {"daily": set(), "minute": set(), "daily_days": 60, "minute_days": 0}
        rc = self.cfg["regime"]
        n["daily"] |= {rc["index_key"], rc["vix_key"]}
        for st in active:
            try:
                d = st.data_needs(self.ctx) or {}
            except Exception as ex:
                log.warning("data_needs %s: %s", st.name, ex)
                continue
            n["daily"] |= set(d.get("daily", []))
            n["minute"] |= set(d.get("minute", []))
            n["daily_days"] = max(n["daily_days"], int(d.get("daily_days", 0)))
            n["minute_days"] = max(n["minute_days"], int(d.get("minute_days", 0)))
        n["daily"] |= {p.key for p in self.portfolio.positions()}
        return n

    def _prefetch(self, needs: dict) -> None:
        self.prefetch_daily(needs["daily"])
        md = needs["minute_days"]
        if needs["minute"] and md:
            keep = set(self.prev_sessions(md))
            self._mday = {k: v for k, v in self._mday.items() if k[1] in keep}   # evict days out of the window
            self.prefetch_minute(sorted(needs["minute"]), md)

    def _refresh_history(self, day: date, needs: dict) -> None:
        end = self.calendar.prev_trading_day(day)
        threads = int(self.cfg.get("parallel", {}).get("history_threads", 4))
        log.info("refreshing history: %d daily keys, %d minute keys", len(needs["daily"]), len(needs["minute"]))
        self.history.ensure_daily(self.data, sorted(needs["daily"]), max(needs["daily_days"], 400), end,
                                  threads=threads)
        if needs["minute"] and needs["minute_days"]:
            self.history.ensure_minute(self.data, sorted(needs["minute"]), needs["minute_days"], end, self.calendar,
                                       threads=threads)
        self._daily_full.clear()
        self._panels.clear()

    def _update_regime(self, day: date) -> None:
        res = self.regime_timeline.get(day.isoformat())
        if res is not None:
            self.regime.state, self.regime.last = res["state"], res
        else:
            rc = self.cfg["regime"]
            close = self.daily_df(rc["index_key"])
            if not len(close):
                close = self.daily_df("IDX:NIFTY50")
            vix = self.daily_df(rc["vix_key"])
            if len(close):
                res = self.regime.update(self.calendar.prev_trading_day(day), close["close"],
                                         vix["close"] if len(vix) else None)
            else:
                res = self.regime.last
                log.warning("no index history for regime detection; using default posture '%s'", self.regime.state)
        self.storage.add_regime(day, res["state"], res.get("probs"), res.get("source", ""))

    def _update_fragility(self, day: date) -> None:
        fc = self.cfg.get("fragility") or {}
        prev = self.fragility.get("fragile")
        if not fc.get("enabled"):
            self.fragility = {}
            return
        f = self.fragility_timeline.get(day.isoformat())
        if f is None and not self.fragility_timeline:
            from . import fragility as fr
            keys = self.universe_keys(fc.get("universe", "nifty50"))
            if keys:
                n = int(fc.get("long", 252)) + int(fc.get("short", 15)) + int(fc.get("window", 60)) + 5
                f = fr.evaluate(fc, fr.series(fc, self.panel(keys, "close", tail=n, with_today=False)))
        self.fragility = dict(f or {})
        if self.fragility.get("fragile") != prev and self.fragility.get("fragile") is not None and prev is not None:
            msg = (f"market fragility {'ON' if self.fragility['fragile'] else 'off'}: absorption-ratio shift "
                   f"{self.fragility.get('ar_shift')} (action: {fc.get('action', 'report')})")
            log.warning(msg)
            self.storage.log_event(self.clock.now(), "WARNING", "fragility", msg)

    def _update_crowding(self, day: date, active) -> None:
        mult = self.crowding_timeline.get(day.isoformat())
        if mult is not None:
            self.crowding.fixed_momentum_mult = mult
            return
        mom = next((s for s in active if s.name == "momentum"), None)
        prev = self.calendar.prev_trading_day(day)
        if mom is None or not self.crowding.due(prev):
            return
        keys = self.universe_keys(mom.p.get("universe", "nifty500"))
        closes = self.panel(keys, "close", with_today=False)
        closes = closes.loc[:, closes.notna().sum() > 260]
        mk = self.daily_df(self.cfg["regime"]["index_key"])
        if closes.shape[1] >= 20 and len(mk):
            self.crowding.update_comomentum(prev, closes, mk["close"])

    # ---- timers ----------------------------------------------------------------------------------
    def _build_timers(self, active) -> None:
        s = self.cfg["session"]
        t = [(s["intraday_exit_cas"], "__intraday_exit", None), (s["safety_flatten"], "__safety_flatten", None),
             (s["intraday_exit_noncas"], "__final_flatten", None), (s.get("close_fill", "15:31"), "__close", None)]
        for st in active:
            for hmv in st.timers():
                t.append((hmv, st.name, hmv))
        self._timers = sorted(t, key=lambda x: hm(x[0]))

    def _fire_timers(self, now: datetime) -> None:
        for i, (hmv, name, arg) in enumerate(self._timers):
            if i in self._fired:
                continue
            if at(self.day, hmv) > now:
                break
            self._fired.add(i)
            if name == "__intraday_exit":
                for st in self.active():
                    if st.horizon == "intraday":
                        self._guard(st, st.on_intraday_exit)
            elif name == "__safety_flatten":
                self.flatten_mis("safety flatten before closing auction", cas_only=True)
            elif name == "__final_flatten":
                self.flatten_mis("final intraday flatten", cas_only=False)
            elif name == "__close":
                self._on_close(now)
            else:
                st = self.by_name.get(name)
                if st and st.name not in self.disabled:
                    self._guard(st, st.on_timer, arg)

    def flatten_mis(self, reason: str, cas_only: bool = False) -> None:
        for p in self.portfolio.positions(product="MIS"):
            if cas_only and parse_key(p.key)["kind"] == "EQ" and not self.is_cas(p.key):
                continue
            st = self.by_name.get(p.strategy)
            if st is not None:
                self._guard(st, lambda ctx, k=p.key: st.exit(ctx, k, reason=reason))

    def _on_close(self, now: datetime) -> None:
        if hasattr(self.execution, "on_close"):
            keys = {o.req.key for o in self.oms.active()} | {p.key for p in self.portfolio.positions()}
            closes = {k: self.last_price(k) for k in keys}
            self.execution.on_close({k: v for k, v in closes.items() if v}, now)
            self.oms.sync(now, force=True)

    # ---- market data -----------------------------------------------------------------------------
    def _add_bar(self, b: Bar) -> None:
        self.today_bars[b.key].append(b)
        tp = (b.high + b.low + b.close) / 3.0
        acc = self.vwap_acc.setdefault(b.key, [0.0, 0.0, 0.0])
        acc[0] += tp * b.volume
        acc[1] += b.volume
        acc[2] += tp * tp * b.volume
        start5 = b.ts.replace(minute=b.ts.minute - b.ts.minute % 5, second=0, microsecond=0)
        agg = self._agg5.get(b.key)
        if agg is None or agg[0] != start5:
            agg = self._agg5[b.key] = [start5, b.open, b.high, b.low, b.close, b.volume]
        else:
            agg[2], agg[3], agg[4], agg[5] = max(agg[2], b.high), min(agg[3], b.low), b.close, agg[5] + b.volume
        if b.ts.minute % 5 == 4:          # 5-minute bucket complete: store it with the VWAP band snapshot
            vw = acc[0] / acc[1] if acc[1] > 0 else b.close
            sd = math.sqrt(max(acc[2] / acc[1] - vw * vw, 0.0)) if acc[1] > 0 else 0.0
            self.bars5[b.key].append(Bar5(*agg, vw, sd))

    def _on_market(self, quotes, bars, now: datetime) -> None:
        s = self.cfg["session"]
        open_t, close_t = hm(s["open"]), hm(s["close"])
        closed: list[Bar] = []
        for q in quotes:
            self.quotes[q.key] = q
            self.portfolio.mark(q.key, q.ltp)
            if open_t <= q.ts.time() < close_t:
                closed += self.bars.on_quote(q)
        closed += self.bars.flush(now)
        if quotes:
            self.book.update(quotes)                    # deep-book signals for instruments streaming depth
        for b in closed:
            self._add_bar(b)
        minute_bars = list(closed)
        for b in bars:
            self.portfolio.mark(b.key, b.close)
            if b.interval == "1d":
                self.day_bars[b.key] = b
            else:
                self._add_bar(b)
                minute_bars.append(b)
        if hasattr(self.execution, "on_market"):
            self.execution.on_market(quotes, bars, now)
        self.oms.sync(now, force=self.clock.is_sim)
        if not quotes and not minute_bars:
            return
        for st in self.active():
            keys = getattr(st, "_subscribed", set())
            for q in quotes:
                if q.key in keys:
                    self._guard(st, st.on_quote, q)
            for b in minute_bars:
                if b.key in keys:
                    self._guard(st, st.on_bar, b)

    def _on_fill(self, fill, order) -> None:
        self.journal.on_fill(order.client_id, fill.ts, fill.price, fill.qty)
        rel = getattr(self.execution, "release", None)    # multi-broker: a flat, idle position frees its account
        if rel is not None and self.portfolio.get(order.req.strategy, fill.key) is None \
                and not self.oms.active(strategy=order.req.strategy, key=fill.key):
            rel(order.req.strategy, fill.key)
        st = self.by_name.get(order.req.strategy)
        if st is not None:
            st._subscribed = getattr(st, "_subscribed", set()) | {fill.key}
            self._guard(st, st.on_fill, fill, order)
        if self.mode == "live":
            self.notifier.send(f"{fill.strategy} {fill.side.value} {fill.qty} {fill.key} @ {fill.price:.2f} ({fill.intent})")

    def _on_trade(self, trade) -> None:
        log.info("trade closed %s %s qty=%s net=%.0f R=%s", trade.strategy, trade.key, trade.qty, trade.pnl_net,
                 "n/a" if not math.isfinite(trade.r_multiple) else f"{trade.r_multiple:.2f}")

    def _watch_keys(self) -> list[str]:
        """feeds.watch: stream more than the strategies need. none | a universe name | all (every NSE stock in
        the data source's instrument list): universe-wide book signals and recordings."""
        cached = getattr(self, "_watch_cache", None)
        if cached is not None:
            return cached
        from ..data.universe import watch_keys
        self._watch_cache = watch_keys(self.cfg, self.mode, getattr(self.data, "master", None), self.universe)
        return self._watch_cache

    def _depth_targets(self) -> list:
        out, seen, obj = [], set(), self.feed
        while obj is not None and id(obj) not in seen:          # walk wrapper feeds (recording, overlay, ...)
            seen.add(id(obj))
            if hasattr(obj, "set_depth_focus"):
                out.append(obj)
            obj = getattr(obj, "inner", None)
        if hasattr(self.data, "set_depth_focus") and id(self.data) not in seen:
            out.append(self.data)
        return out

    def _update_depth_focus(self, now: datetime, force: bool = False) -> None:
        """Deep-book slots follow open positions first, then each strategy's current candidates."""
        every = float((self.cfg.get("feeds") or {}).get("depth_focus_seconds", 60))
        if self.mode not in ("forward", "live") or every <= 0:
            return
        if not force and self._depth_at is not None and (now - self._depth_at).total_seconds() < every:
            return
        self._depth_at = now
        targets = self._depth_targets()
        if not targets:
            return
        from ..data.depth import rank_depth_focus
        lists = []
        for st in self.active():
            try:
                lists.append(list(st.depth_focus(self.ctx) or []))
            except Exception as ex:
                log.warning("depth_focus %s: %s", st.name, ex)
        focus = rank_depth_focus([p.key for p in self.portfolio.positions()], lists)
        for t in targets:
            try:
                t.set_depth_focus(focus)
            except Exception as ex:
                log.warning("depth focus on %s failed: %s", type(t).__name__, ex)

    def _save_journal(self) -> None:
        self.journal.sync(self.oms.orders)
        self.journal.save_bars(self.today_bars)

    def _housekeeping(self, now: datetime) -> None:
        self.oms.chase_exits(now)
        frac = float((self.cfg.get("oms") or {}).get("min_partial_fraction", 0.0))
        for o in self.oms.expire_entries(now):            # partly filled intraday entries
            if frac > 0 and o.filled_qty < frac * o.req.qty:
                st = self.by_name.get(o.req.strategy)
                if st is not None:
                    self._guard(st, lambda ctx, k=o.req.key: st.exit(
                        ctx, k, reason=f"partial entry {o.filled_qty}/{o.req.qty} below min_partial_fraction"))
        self._update_depth_focus(now)
        if self.journal.path is not None and (getattr(self, "_journal_at", None) is None
                                              or (now - self._journal_at).total_seconds() >= 300):
            self._journal_at = now
            self._save_journal()
        for st in self.active():                          # e.g. market makers pull quotes when data goes stale
            hb = getattr(st, "on_heartbeat", None)
            if hb is not None:
                self._guard(st, hb, now)
        act = self.risk.monitor()
        if act in ("flatten_intraday", "flatten_all"):
            log.error("risk action: %s (%s)", act, self.risk.intraday_reason or self.risk.halt_reason)
            if act == "flatten_all":
                self.oms.cancel_all()
            else:
                for o in self.oms.active():
                    if o.req.product.value == "MIS" and o.req.intent == "entry":
                        self.oms.cancel(o.client_id)
            self.flatten_mis(f"risk: {act}", cas_only=False)
            self.notifier.send(f"Risk action {act}: {self.risk.intraday_reason or self.risk.halt_reason}")
        if self.mode in ("live", "forward"):
            every = float(self.cfg["oms"].get("reconcile_seconds", 60))
            if self._last_reconcile is None or (now - self._last_reconcile).total_seconds() >= every:
                self._last_reconcile = now
                issues = self.oms.reconcile(now)
                if issues:
                    log.error("position mismatch: %s", "; ".join(issues[:5]))
                    if self.mode == "live" and self.cfg["oms"].get("halt_on_mismatch", True):
                        self.risk.halt("position mismatch with broker: " + "; ".join(issues[:3]))
                        self.notifier.send("HALTED: position mismatch with broker")

    # ---- end of day ------------------------------------------------------------------------------
    def _end_day(self, day: date) -> None:
        now = self.clock.now()
        self.oms.sync(now, force=True)
        if hasattr(self.execution, "end_of_day"):
            self.execution.end_of_day(now)
            self.oms.sync(now, force=True)
        self.oms.expire_day_orders()
        for st in self.active():
            self._guard(st, st.on_session_end)
        if self.mode in ("forward", "live"):
            self._persist_today(day)
        if self.journal.path is not None:
            self._save_journal()
        eq_now = self.portfolio.equity
        self.storage.add_equity(at(day, "15:30"), eq_now, self.portfolio.realized, self.portfolio.unrealized(),
                                self.portfolio.costs)
        if self.crowding.breaker:
            self._drawdown_checks(day)
        self.storage.kv_set("engine", "portfolio", self.portfolio.snapshot())
        self.save_states()
        self.storage.flush()
        msg = (f"{day} closed | equity {eq_now:,.0f} | open positions {len(self.portfolio.positions())} | "
               f"regime {self.regime.state}")
        log.info(msg)
        if self.mode in ("forward", "live"):
            self.notifier.send(msg)

    def _persist_today(self, day: date) -> None:
        try:
            self.history.put_minute_bars(self.today_bars)
            for k in set(self.quotes) | set(self.today_bars):
                tb = self.today_bar(k)
                if tb:
                    self.history.put_daily(k, pd.DataFrame([tb], index=pd.DatetimeIndex([pd.Timestamp(day)])))
            self._daily_full.clear()
            self._panels.clear()
        except Exception as ex:
            log.warning("could not persist today's bars: %s", ex)

    def _drawdown_checks(self, day: date) -> None:
        t = self.storage.trades_df()
        if t.empty:
            return
        for name, g in t.groupby("strategy"):
            st = self.by_name.get(name)
            if st is None:
                continue
            limit = float(st.p.get("max_dd", 0.05 if st.horizon == "intraday" else 0.08)) * self.portfolio.capital
            cum = g["pnl_net"].cumsum()
            if float((cum.cummax() - cum).iloc[-1]) >= limit:
                self.crowding.record_breach(name, day)
        if self.crowding.evaluate_breaker(day, self.calendar):
            log.warning("correlated drawdown breaker: risk halved until %s", self.crowding.cooldown_until)
