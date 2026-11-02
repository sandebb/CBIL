"""Forward testing (paper trading on live data) and live trading.

Forward test = real market data from your broker + the PaperBroker as a conservative simulated
exchange + a flight recorder of every quote. The engine, strategies and risk layer are exactly the
ones that trade live, so promotion to live changes only the execution adapter.
"""
from __future__ import annotations

import logging
import time as _t
from datetime import datetime
from pathlib import Path

from ..brokers.base import AuthError
from ..brokers.paper import PaperBroker
from ..brokers.registry import make_broker
from ..config import resolve_path
from ..core.clock import IST, NSECalendar, RealClock, SimClock, at
from ..core.engine import Engine
from ..core.notifier import Notifier
from ..core.storage import Storage
from ..data.feeds import PollingFeed, RecordedReplayFeed, RecordingFeed, StreamingFeed
from ..data.history import HistoryStore
from ..strategies import build_strategies
from .report import build_report

log = logging.getLogger("nsealgo.forward")


def _login_waiting(broker, interactive: bool, notifier: Notifier, max_wait_s: float | None = None) -> bool:
    """Daily login. In daemon mode, wait (and nag once) until `nsealgo login <broker>` has been run.
    Returns False if max_wait_s elapses (a multi-broker mesh then runs without that source today)."""
    warned, t0 = False, _t.time()
    while True:
        try:
            broker.login(interactive=interactive)
            return True
        except AuthError as ex:
            if interactive:
                raise
            if not warned:
                log.warning("%s: %s; waiting for a fresh session", broker.name, ex)
                notifier.send(f"Action needed ({broker.name}): {ex}")
                warned = True
            if max_wait_s is not None and _t.time() - t0 > max_wait_s:
                return False
            _t.sleep(60)


def data_names(spec: str) -> list[str]:
    return [x.strip() for x in str(spec).split(",") if x.strip()]


def connect_sources(cfg: dict, names: list[str], interactive: bool, notifier: Notifier) -> list:
    """Log in to every listed data broker; a source that fails is skipped when others remain."""
    cache = resolve_path(cfg, "cache_dir")
    out = []
    for name in names:
        try:
            b = make_broker(name, cfg, cache)
            ok = _login_waiting(b, interactive=interactive, notifier=notifier,
                                max_wait_s=None if len(names) == 1 else 1200)
            if not ok:
                continue
            b.load_instruments()
            out.append(b)
            log.info("%s connected: %d instruments", name, len(b.master))
        except Exception as ex:
            if len(names) == 1:
                raise
            log.error("skipping data source %s: %s", name, ex)
    if not out:
        raise RuntimeError("no data source could be connected")
    return out


def _depth_source(name: str, cfg: dict, oc: dict, sources):
    brokers = (cfg.get("broker") or {}).get("brokers") or {}
    cache = resolve_path(cfg, "cache_dir")

    def logged_in(broker_name):
        b = next((s for s in sources if getattr(s, "name", "") == broker_name), None)
        if b is not None:
            return b, True
        b = make_broker(broker_name, cfg, cache)
        b.login(interactive=True)
        b.load_instruments()
        return b, False

    if name == "upstox":
        from ..data.upstox_depth import UpstoxDepthFeed
        up, is_main = logged_in("upstox")
        conns = int(oc.get("upstox_connections", 5)) - (1 if is_main else 0)   # 5 websockets per user in all
        return UpstoxDepthFeed(up, connections=max(conns, 1), per_connection=int(oc.get("upstox_per_connection", 50)),
                               max_symbols=int(oc.get("upstox_max_symbols", 250)),
                               verify_seconds=float(oc.get("verify_seconds", 60)))
    if name == "fyers_tbt":
        from ..data.fyers_tbt import FyersTbtFeed
        fy, _ = logged_in("fyers")
        rec = None
        if oc.get("tbt_record"):
            from ..config import resolve_path
            from ..data.tbt_recorder import TbtRecorder
            rec = TbtRecorder(resolve_path(cfg, "tbt_dir"))
        return FyersTbtFeed((brokers.get("fyers") or {}).get("client_id"), getattr(fy, "_token", None),
                            lambda k: fy.instrument(k).broker_symbol, max_symbols=int(oc.get("tbt_max_symbols", 15)),
                            connections=int(oc.get("tbt_connections", 3)),
                            per_connection=int(oc.get("tbt_per_connection", 5)), recorder=rec)
    raise ValueError(f"unknown feeds.depth_overlay source {name!r} (fyers_tbt, upstox)")


def _with_depth_overlay(cfg: dict, base, sources=()):
    """feeds.depth_overlay.source: fyers_tbt | upstox | fyers_tbt,upstox (deepest first): books from YOUR accounts
    merged onto the main feed's quotes."""
    oc = (cfg.get("feeds") or {}).get("depth_overlay") or {}
    names = [x.strip().lower() for x in str(oc.get("source") or "none").split(",") if x.strip().lower() not in ("", "none")]
    if not names:
        return base
    from ..data.depth import DepthOverlayFeed, TieredDepth
    srcs = [_depth_source(n, cfg, oc, sources) for n in names]
    src = srcs[0] if len(srcs) == 1 else TieredDepth(srcs)
    log.info("depth overlay on (%s): up to %d instruments", ",".join(names), src.capacity)
    return DepthOverlayFeed(base, src, max_age=float(oc.get("max_age", 2.0)))


def _feed(cfg: dict, sources: list, record: bool, run_dir: Path | None = None):
    poll = float(cfg["session"].get("poll_seconds", 1.0))
    if len(sources) > 1:
        from ..data.mesh import TickMesh
        fc = cfg.get("feeds", {})
        raw = (resolve_path(cfg, "recordings_dir") / "raw" / f"{datetime.now(IST):%Y-%m-%d}.jsonl.gz"
               if fc.get("record_raw") else None)
        base = TickMesh(sources, redundancy=int(fc.get("redundancy", 2)), stale_seconds=float(fc.get("stale_seconds", 20)),
                        limits=fc.get("limits") or {}, poll_interval=poll, raw_path=raw,
                        disagree_bps=float(fc.get("disagree_bps", 10)))
    else:
        d = sources[0]
        base = StreamingFeed(d, poll_interval=poll) if d.supports_streaming else PollingFeed(d, poll)
    base = _with_depth_overlay(cfg, base, sources)
    depth = (cfg.get("feeds") or {}).get("record_depth", 5)
    return RecordingFeed(base, resolve_path(cfg, "recordings_dir"), depth_levels=depth) if record else base


def _sleep_until(ts: datetime) -> None:
    while True:
        rem = (ts - datetime.now(IST)).total_seconds()
        if rem <= 0:
            return
        _t.sleep(min(rem, 300))


def run_session_loop(cfg: dict, *, mode: str, execution_name: str, data_name: str, names=None, daemon=False,
                     record=True, benchmark=None) -> None:
    from ..compute import init_compute
    init_compute(cfg, resolve_path(cfg, "cache_dir"))
    from ..system import apply_process_policy, keep_awake, keep_awake_mode
    apply_process_policy(cfg, "live")
    awake = keep_awake_mode(cfg)
    with keep_awake(enabled=awake == "always"):
        _session_loop(cfg, mode=mode, execution_name=execution_name, data_name=data_name, names=names, daemon=daemon,
                      record=record, benchmark=benchmark, awake=awake)


def _session_loop(cfg: dict, *, mode: str, execution_name: str, data_name: str, names, daemon, record, benchmark,
                  awake: str) -> None:
    from ..system import keep_awake
    notifier = Notifier(cfg.get("notifier"))
    cache = resolve_path(cfg, "cache_dir")
    cal = NSECalendar(resolve_path(cfg, "holidays_file"))
    names = data_names(data_name)
    sources = connect_sources(cfg, names, interactive=not daemon, notifier=notifier)
    if len(sources) > 1:
        from ..data.mesh import MultiSourceData
        data = MultiSourceData(sources)
        log.info("tick mesh over %s", ", ".join(s.name for s in sources))
    else:
        data = sources[0]
    clock = RealClock()
    if mode == "forward":
        execution = PaperBroker(cfg.get("paper"), cache, clock=clock, mode="quote", master=data.master)
    else:
        from ..brokers.routing import build_execution, describe
        override = execution_name if execution_name not in (None, "", "auto") else None

        def _login(b):
            _login_waiting(b, interactive=not daemon, notifier=notifier)
            b.load_instruments()

        execution = build_execution(cfg, sources, cache, override=override, login=_login)
        log.info("live orders: %s", describe(cfg, override))
    run_dir = resolve_path(cfg, "runs_dir") / mode
    storage = Storage(run_dir / "state.db")
    strategies = build_strategies(cfg, names)
    eng = Engine(cfg, strategies=strategies, execution=execution, data=data,
                 feed=_feed(cfg, sources, record, run_dir), clock=clock, storage=storage, mode=mode, granularity="minute",
                 notifier=notifier, run_dir=run_dir)
    from ..monitor import start_monitor
    monitor = start_monitor(eng, cfg, run_dir)        # read-only data for `nsealgo desk`
    while True:
        now = clock.now()
        today = now.date()
        eod = at(today, cfg["session"]["eod"])
        if cal.is_trading_day(today) and now < eod:
            pre = at(today, cfg["session"]["pre_market"])
            if now < pre:
                log.info("waiting for pre-market %s", pre)
                _sleep_until(pre)
            if daemon:
                for src in sources:
                    _login_waiting(src, interactive=False, notifier=notifier, max_wait_s=1200)
            with keep_awake(enabled=awake == "session"):     # a sleeping PC drops every broker WebSocket
                eng.run_day(today)
            if hasattr(eng.feed, "inner") and hasattr(eng.feed.inner, "report"):
                import json as _json
                (run_dir / "report").mkdir(parents=True, exist_ok=True)
                (run_dir / "report" / f"feeds_{today}.json").write_text(
                    _json.dumps(eng.feed.inner.report(), indent=2, default=str), encoding="utf-8")
            s = build_report(storage, cfg, run_dir / "report", f"{mode.title()} test, cumulative", mode, benchmark,
                             names=[x.name for x in strategies])
            log.info("report: %s (%s trades)", run_dir / "report" / "report.html", s["trades"])
        if not daemon:
            break
        nxt = cal.next_trading_day(today)
        log.info("next session %s", nxt)
        _sleep_until(at(nxt, cfg["session"]["pre_market"]))
    if monitor is not None:
        monitor.stop()
    eng.feed.close()
    storage.close()


def run_replay(cfg: dict, recording: str | Path, names=None, master=None, history=None, run_dir=None) -> dict:
    """Re-run a recorded session (or a synthetic quote stream) through the full stack, deterministically."""
    feed = RecordedReplayFeed(recording)
    clock = SimClock(at(feed.day, "08:00"))
    paper = PaperBroker(cfg.get("paper"), resolve_path(cfg, "cache_dir"), clock=clock, mode="quote", master=master)
    run_dir = Path(run_dir or resolve_path(cfg, "runs_dir") / f"replay_{feed.day}")
    storage = Storage(run_dir / "state.db")
    strategies = build_strategies(cfg, names)
    eng = Engine(cfg, strategies=strategies, execution=paper, data=paper, feed=feed, clock=clock,
                 history=history or HistoryStore(resolve_path(cfg, "data_dir") / "history.db"), storage=storage,
                 mode="replay", granularity="minute", notifier=Notifier({}), run_dir=run_dir)
    eng.run_day(feed.day)
    s = build_report(storage, cfg, run_dir, f"Replay of {feed.day}", "replay", names=[x.name for x in strategies])
    storage.close()
    s["run_dir"] = str(run_dir)
    s["signals"] = len(eng.journal.recs)                  # the desk's signal journal, for checks
    s["journal_trades"] = len(eng.journal.trades())
    return s
