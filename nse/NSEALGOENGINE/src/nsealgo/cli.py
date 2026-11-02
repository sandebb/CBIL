"""nsealgo command line. Run `nsealgo --help` or `nsealgo <command> --help`."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import importlib.metadata as md
import importlib.util
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import typer

from .core.models import eq
from .config import load_config, resolve_path

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Broker-agnostic, regime-aware NSE trading system: backtest, forward test, live.")
STATE: dict = {}


def cfg() -> dict:
    return STATE["cfg"]


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def _names(s: str | None) -> list[str] | None:
    return [x.strip() for x in s.split(",") if x.strip()] if s else None


@app.callback()
def main(config: Path = typer.Option(Path("config/system.yaml"), "--config", "-c", help="Config YAML"),
         verbose: bool = typer.Option(False, "--verbose", "-v")):
    from .system import console_utf8
    console_utf8()
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("urllib3", "hmmlearn", "matplotlib", "websocket", "kiteconnect"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    STATE["cfg"] = load_config(config if config.exists() else None)
    STATE["config_path"] = config


@app.command()
def doctor():
    """Check Python, installed broker SDKs, stdlib-shadowing packages, config and data."""
    from .brokers.registry import ADAPTERS, INSTALL, SDK_MODULE
    ok = True
    print(f"Python {sys.version.split()[0]}", "OK" if sys.version_info >= (3, 10) else "NEEDS >= 3.10")
    for dist in ("asyncio", "uuid"):   # obsolete PyPI packages that shadow the standard library
        try:
            v = md.version(dist)
            print(f"PROBLEM: PyPI package '{dist}' {v} is installed and shadows the standard library -> "
                  f"pip uninstall -y {dist}")
            ok = False
        except md.PackageNotFoundError:
            pass
    print("\nBroker SDKs:")
    for name in ADAPTERS:
        mod = SDK_MODULE.get(name)
        if not mod:
            continue
        found = importlib.util.find_spec(mod) is not None
        print(f"  {name:10s} {'installed' if found else 'not installed':14s} {'' if found else INSTALL.get(name, '')}")
    c = cfg()
    print("\nConfig:", STATE["config_path"] if STATE["config_path"].exists() else "defaults (no config file)")
    for key in ("holidays_file", "universe_dir", "events_dir"):
        p = resolve_path(c, key)
        print(f"  {key:14s} {p} {'OK' if p.exists() else 'MISSING'}")
    from .data.history import HistoryStore
    h = resolve_path(c, "data_dir") / "history.db"
    if h.exists():
        hs = HistoryStore(h)
        print(f"  history.db     {len(hs.keys('daily'))} daily keys, {len(hs.keys('minute'))} minute keys")
    else:
        print("  history.db     none yet (run fetch-history or demo)")
    for role in ("execution", "data"):
        b = c["broker"][role]
        creds = (c["broker"].get("brokers") or {}).get(b, {})
        optional = {"algo_id", "master_url", "session_token", "api_secret", "totp_secret"} if b == "groww" else \
            {"algo_id", "master_url", "session_token"}
        empty = [k for k, v in creds.items() if v in ("", None) and k not in optional] if b != "paper" else []
        print(f"  {role:10s} broker: {b}" + (f"  (empty credentials: {', '.join(empty)})" if empty else ""))
    from .system import clock_offset, windows_checks
    notes = windows_checks()
    if notes:
        print("\nWindows:")
        for n in notes:
            print("  " + n)
    off = clock_offset()
    if off is None:
        print("\nClock: NTP not reachable (UDP 123) - check the clock manually; timers run on local time")
    else:
        print(f"\nClock: local time is off by {off:+.2f}s" + ("  -> resync: w32tm /resync (admin)" if abs(off) > 1 else "  OK"))
        ok = ok and abs(off) <= 1
    print("\nSEBI retail-algo checklist: static IP registered with the broker, daily 2FA login, "
          "order rate <= 10/s (system caps at 8), algo id if your broker requires one.")
    raise typer.Exit(0 if ok else 1)


@app.command()
def brokers():
    """List broker adapters, their maturity and install commands."""
    from .brokers.registry import ADAPTERS, INSTALL, MATURITY, SDK_MODULE
    for name in ADAPTERS:
        sdk = SDK_MODULE.get(name)
        inst = "built-in" if not sdk else ("installed" if importlib.util.find_spec(sdk) else "not installed")
        print(f"{name:10s} {inst:14s} {MATURITY.get(name, ''):45s} {INSTALL.get(name, '')}")


@app.command()
def login(broker: str):
    """Interactive daily login; caches today's session token (mode 0600) and the instrument master."""
    from .brokers.registry import make_broker
    b = make_broker(broker, cfg(), resolve_path(cfg(), "cache_dir"))
    b.login(interactive=True)
    m = b.load_instruments(force=True)
    print(f"{broker}: logged in; instrument master has {len(m)} rows "
          f"({len(m.keys('EQ'))} stocks, {len(m.keys('IDX'))} indices)")


@app.command("smoke-test")
def smoke_test(broker: str, symbol: str = "SBIN", place_test_order: bool = typer.Option(
        False, help="Place a far-from-market 1-share CNC buy limit and cancel it")):
    """End-to-end check of a broker adapter against your real account."""
    from .brokers.registry import make_broker
    from .core.models import OrderRequest, OrderType, Product, Side, eq
    b = make_broker(broker, cfg(), resolve_path(cfg(), "cache_dir"))
    steps = []

    def step(name, fn):
        try:
            r = fn()
            steps.append((name, True, r))
            print(f"PASS {name}: {r}")
            return r
        except Exception as ex:
            steps.append((name, False, ex))
            print(f"FAIL {name}: {type(ex).__name__}: {ex}")

    step("login", lambda: b.login(interactive=True) or "ok")
    step("instrument master", lambda: f"{len(b.load_instruments())} rows")
    key = eq(symbol)
    q = step("quote", lambda: b.get_quotes([key])[0])
    end = date.today()
    step("daily history", lambda: f"{len(b.get_history(key, '1d', end - timedelta(days=10), end))} bars")
    step("minute history", lambda: f"{len(b.get_history(key, '1m', end - timedelta(days=3), end))} bars")
    step("order book", lambda: f"{len(b.get_orders())} orders today")
    step("positions", lambda: f"{len(b.get_positions())} positions")
    if place_test_order and q:
        inst = b.instrument(key)
        px = round(round(q.ltp * 0.9 / inst.tick_size) * inst.tick_size, 2)
        oid = step("place far limit order", lambda: b.place_order(
            OrderRequest(key, Side.BUY, 1, OrderType.LIMIT, Product.CNC, price=px, strategy="smoke"), "SMOKE0001"))
        if oid:
            step("order visible in book", lambda: [o for o in b.get_orders() if o.broker_id == oid][0].status)
            step("cancel order", lambda: b.cancel_order(oid) or "cancelled")
    failed = [s for s in steps if not s[1]]
    print(f"\n{len(steps) - len(failed)}/{len(steps)} checks passed")
    raise typer.Exit(1 if failed else 0)


@app.command("fetch-history")
def fetch_history(broker: str = typer.Option(None, help="Data broker (default: broker.data)"),
                  universe: str = "nifty500", days: int = 800, minute_days: int = 20, minute_universe: str = "fno",
                  threads: int = typer.Option(None, help="Parallel downloads (default parallel.history_threads); "
                                                        "the broker's rate limit still paces the requests")):
    """Download daily (and 1-minute) history into data/history.db."""
    from .brokers.registry import make_broker
    from .core.clock import NSECalendar
    from .data.history import HistoryStore
    from .data.universe import Universe
    c = cfg()
    name = broker or c["broker"]["data"]
    b = make_broker(name, c, resolve_path(c, "cache_dir"))
    b.login(interactive=True)
    m = b.load_instruments()
    u = Universe(resolve_path(c, "universe_dir"))
    keys = u.load(universe)
    if not keys:
        raise typer.BadParameter(f"universe '{universe}' not found in {resolve_path(c, 'universe_dir')}")
    fno = u.load(minute_universe)
    cal = NSECalendar(resolve_path(c, "holidays_file"))
    end = cal.prev_trading_day(date.today() + timedelta(days=1))
    h = HistoryStore(resolve_path(c, "data_dir") / "history.db")
    extra = [c["regime"]["index_key"], c["regime"]["vix_key"], "IDX:NIFTY50"]
    th = int(threads or c.get("parallel", {}).get("history_threads", 4))
    h.ensure_daily(b, [k for k in extra + keys + fno if k in m], days, end, threads=th)
    if minute_days:
        h.ensure_minute(b, [k for k in fno if k in m], minute_days, end, cal, threads=th)
    print(f"done: {len(h.keys('daily'))} daily keys, {len(h.keys('minute'))} minute keys in {h.path}")


def _minute_archive_dir(c) -> "Path":
    import os
    from pathlib import Path
    env = os.environ.get("NSEALGOENGINE_MINUTE_ARCHIVE", "").strip().strip('"')
    return Path(env) if env else resolve_path(c, "data_dir") / "minute_archive"


def _oms_idle(runs_dir, poll_seconds: float = 5.0, max_wait: float = 120.0) -> bool:
    """Wait until the live session has no open orders (safe to call the Fyers data API), polling the session's
    monitor (runs/live/monitor.json -> GET /api/status). If there is no session, or the monitor is unreachable,
    treat it as idle (nothing is being sent). Returns True once idle; False if still busy after max_wait (the
    caller should then skip this round and let the next one catch up)."""
    import json, time, urllib.request
    mon = Path(runs_dir) / "live" / "monitor.json"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # localhost: never a system proxy
    deadline = time.time() + max_wait
    while True:
        try:
            info = json.loads(mon.read_text(encoding="utf-8"))
            req = urllib.request.Request(info["url"].rstrip("/") + "/api/status",
                                         headers={"X-Nsealgo-Token": info["token"]})
            with opener.open(req, timeout=2) as r:
                st = json.loads(r.read().decode("utf-8"))
            busy = any(int(s.get("open_orders", 0)) > 0 for s in st.get("strategies", []))
        except (OSError, ValueError, KeyError, TypeError):
            return True                        # no session / monitor down -> nothing is being sent
        if not busy:
            return True
        if time.time() >= deadline:
            return False                       # still busy after the grace window -> caller skips the round
        time.sleep(poll_seconds)


@app.command("fetch-minute-archive")
def fetch_minute_archive(universe: str = typer.Option("liquid_union_2018", help="Universe file in config/universes"),
                         start: str = typer.Option("2018-01-01", help="First day wanted (Fyers has 1-minute bars from ~Jan 2018)"),
                         broker: str = typer.Option(None, help="Data broker (default: broker.data; must be fyers)"),
                         threads: int = typer.Option(4, help="Parallel downloads (Fyers' rate limit still paces them)"),
                         unattended: bool = typer.Option(False, "--unattended", help="Scheduled task: use the day's token "
                                                         "from NSEUIENGINE's 08:00 login, never open a browser login")):
    """Full-day 1-minute bars for many years as compressed parquet (data/minute_archive, or NSEALGOENGINE_MINUTE_ARCHIVE).
    Resumable: run the same command again after an interruption, or later to add the newest days."""
    from .brokers.registry import make_broker
    from .core.clock import NSECalendar
    from .data.minute_archive import backfill
    from .data.universe import Universe
    c = cfg()
    name = broker or c["broker"]["data"]
    if name != "fyers":
        raise typer.BadParameter("the minute archive downloader talks to the Fyers API - use --broker fyers")
    b = make_broker(name, c, resolve_path(c, "cache_dir"))
    b.login(interactive=not unattended)
    m = b.load_instruments()
    keys = Universe(resolve_path(c, "universe_dir")).load(universe)
    if not keys:
        raise typer.BadParameter(f"universe '{universe}' not found in {resolve_path(c, 'universe_dir')}")
    cal = NSECalendar(resolve_path(c, "holidays_file"))
    # last complete session: today once the market has closed (Fyers has the day's bars by ~16:00), else the one before
    now = datetime.now()
    end = date.today() if (cal.is_trading_day(date.today()) and now.hour >= 16) else cal.prev_trading_day(date.today())
    from .data.minute_archive import resolve_symbols
    symbols, lost = resolve_symbols(b, keys, m, end)
    alt = sorted(k.split(":")[-1] + " (" + v.rsplit("-", 1)[-1] + ")" for k, v in symbols.items() if not v.endswith("-EQ"))
    if alt:
        print(f"{len(alt)} stock(s) now trade in the BE/BZ series - downloaded under that symbol: {', '.join(alt)}")
    if lost:
        print(f"{len(lost)} symbol(s) not available at Fyers (renamed / merged / delisted) - skipped: {', '.join(lost)}")
    root = _minute_archive_dir(c)
    backfill(b, symbols, root, _d(start), end, threads=threads)
    from .data.minute_archive import clean_partials
    clean_partials(root)        # today's in-progress partials are now inside the full span - drop them


@app.command("prefetch-today")
def prefetch_today(universe: str = typer.Option("liquid_union_2018", help="Universe file in config/universes"),
                   broker: str = typer.Option(None, help="Data broker (default: broker.data; must be fyers)"),
                   threads: int = typer.Option(4, help="Parallel downloads (Fyers' rate limit still paces them)"),
                   unattended: bool = typer.Option(False, "--unattended", help="Scheduled task: use the day's token "
                                                       "from NSEUIENGINE's 08:00 login, never open a browser login"),
                   wait_idle: bool = typer.Option(True, "--wait-idle/--no-wait-idle",
                                                  help="Poll the live session for open orders before fetching"),
                   idle_timeout: float = typer.Option(120.0, help="Max seconds to wait for the OMS to go idle")):
    """Fetch TODAY's 1-minute bars (up to now) into <SYM>/partial_<date>.parquet - the intraday prefetch that lets
    the 15:37 ALT-INTRADAY forecast run without waiting for the 19:05 full archive. Light: one small Fyers request
    per stock. Idempotent - the scheduled task runs it every ~15 min while the market is open."""
    from .brokers.registry import make_broker
    from .core.clock import NSECalendar
    from .data.minute_archive import prefetch_today as _pt, resolve_symbols
    from .data.universe import Universe
    c = cfg()
    name = broker or c["broker"]["data"]
    if name != "fyers":
        raise typer.BadParameter("the prefetch talks to the Fyers API - use --broker fyers")
    today = date.today()
    cal = NSECalendar(resolve_path(c, "holidays_file"))
    if not cal.is_trading_day(today):
        print(f"not a trading day ({today}) - nothing to prefetch")
        return
    if wait_idle and not _oms_idle(resolve_path(c, "runs_dir"), max_wait=idle_timeout):
        raise SystemExit("OMS still has open orders after the grace window - skipping this prefetch round")
    b = make_broker(name, c, resolve_path(c, "cache_dir"))
    b.login(interactive=not unattended)
    m = b.load_instruments()
    keys = Universe(resolve_path(c, "universe_dir")).load(universe)
    if not keys:
        raise typer.BadParameter(f"universe '{universe}' not found in {resolve_path(c, 'universe_dir')}")
    symbols, lost = resolve_symbols(b, keys, m, today)
    if lost:
        print(f"{len(lost)} symbol(s) not available at Fyers - skipped: {', '.join(lost)}")
    _pt(b, symbols, _minute_archive_dir(c), today, threads=threads)


@app.command("minute-archive-status")
def minute_archive_status():
    """Size and coverage of the 1-minute parquet archive."""
    from .data.minute_archive import status
    print(status(_minute_archive_dir(cfg())))


@app.command("liquidity-table")
def liquidity_table():
    """How much the post-close session and the pre-open auction usually take, per stock -> data/liquidity_table.parquet
    (from the 1-minute archive, read incrementally, the pre-open recorder and the at-close snapshots)."""
    from .sizing import liquidity as L
    c = cfg()
    L.build(Path(__file__).resolve().parents[2], _minute_archive_dir(c), resolve_path(c, "data_dir") / "liquidity_table.parquet")


@app.command("size-overnight")
def size_overnight(capital: float = typer.Option(..., help="Rupees for tonight's overnight trade"),
                   top: int = typer.Option(None, help="Split the capital among the top N ranked stocks only (e.g. 5); "
                                                      "shows the equal split of the same stocks next to it"),
                   ml_work: str = typer.Option("work", help="NSEMLENGINE folder of the forecasting model: work (original), "
                                                            "work_alt_close, work_alt_link, ..."),
                   forecast: Path = typer.Option(None, help="Forecast file (default: <NSEMLENGINE>/<ml-work>/predictions/latest_close.csv)"),
                   day: str = typer.Option(None, help="YYYY-MM-DD (default: today)"),
                   max_weight: float = typer.Option(0.5, help="Most of the capital in one stock"),
                   pc_part: float = typer.Option(0.5, help="Share of a stock's expected post-close session to buy"),
                   auction_part: float = typer.Option(0.10, help="Most of its expected pre-open auction to sell into"),
                   kappa: float = typer.Option(0.5, help="Auction impact: kappa x daily sigma x (sale / auction volume)"),
                   min_price: float = typer.Option(0.0, help="Price floor in Rs (0 = none; it goes back to 50 by itself "
                                                             "when the ASM / GSM list cannot be checked)")):
    """Tonight's overnight-trade sizes from the 15:37 forecast: rupees and shares per stock that fit the post-close
    session (buy, 15:40-16:00) and the next pre-open auction (sell), where the expected profit after charges and
    slippage is highest. --top 5: split among the top 5 only. Writes runs/overnight_sizing_<day>.csv and an order list to
    review. Places NO orders."""
    from .sizing import overnight as O
    c = cfg()
    runs = resolve_path(c, "runs_dir")
    p = O.Params(pc_part=pc_part, auction_part=auction_part, kappa=kappa, max_weight=max_weight)
    plan, s = O.plan_tonight(Path(__file__).resolve().parents[2], _minute_archive_dir(c), resolve_path(c, "data_dir"),
                             runs, capital, p, forecast=forecast, ml_work=ml_work, day=day, top=top,
                             min_price=min_price)
    lk = lambda v: f"{v / 1e5:,.2f}"
    who = f"the top {top}" if top else "the best of the top 40"
    print(f"\nRs {capital:,.0f} split among {who} for the close of {s['day']}  (forecast {Path(s['forecast']).name}; "
          f"expected move = {s['edge_model']})")
    show = plan if top else plan[plan["qty"] > 0]
    print("\nWHAT THE MODEL KNOWS ABOUT EACH STOCK")
    print(f"{'rank':>4s} {'stock':12s} {'price Rs':>9s} {'predicted':>9s} {'expected move':>13s} {'traded today':>12s} "
          f"{'post-close session':>18s} {'morning auction':>15s}")
    print(f"{'':4s} {'':12s} {'':9s} {'gap':>9s} {'after charges':>13s} {'Rs crore':>12s} {'Rs lakh, expected':>18s} "
          f"{'Rs lakh, expected':>15s}")
    for r in show.itertuples():
        print(f"{r.rank:4d} {r.company_id:12s} {r.price:9.2f} {r.pred_bps / 100:+8.2f}% {r.net_edge_bps / 100:+12.2f}% "
              f"{r.tv_today / 1e7:12,.1f} {lk(r.pc_rs):>18s} {lk(r.pre_rs):>15s}")
    if top:
        print("\nEQUAL SPLIT vs OPTIMIZED SPLIT of the same stocks   (expected; slippage = how much your own sale pushes "
              "the opening price down)")
        print(f"{'stock':12s} | {'equal Rs':>9s} {'fills Rs':>9s} {'slippage':>8s} {'profit':>7s} | "
              f"{'optimized Rs':>12s} {'shares':>6s} {'slippage':>8s} {'profit':>7s}")
        whys = []
        for r in show.itertuples():
            eq_rs = r.eq_qty * r.price
            print(f"{r.company_id:12s} | {eq_rs:9,.0f} {r.eq_fill_rs:9,.0f} {r.eq_fill_rs * r.eq_impact_bps / 1e4:8,.0f} "
                  f"{r.eq_profit_rs:+7,.0f} | {r.x_rs:12,.0f} {r.qty:6d} {r.opt_fill_rs * r.opt_impact_bps / 1e4:8,.0f} "
                  f"{r.opt_profit_rs:+7,.0f}")
            whys.append((r.company_id, {
                "post-close cap": "half its expected post-close session - more may not find sellers",
                "auction cap": "10% of its expected morning auction - more would push the open down",
                "max weight": "half the capital at most in one stock",
                "money shared out": "the money ran out: its last rupee earns the same as in the other stocks",
                "edge vs impact": "more would cost more in slippage than it is expected to earn",
                "move < costs": "not bought: expected move is below the charges",
                "cap < 1 share": "not bought: one share is more than its session can take",
                "others better / DP": "not bought: the amount it would get cannot cover the Rs 10.62 DP charge",
            }.get(r.limited_by, r.limited_by)))
        print("\nwhy these amounts:")
        for cid, why in whys:
            print(f"  {cid:12s} {why}")
        e, o = s["equal"], s["optimized"]
        print(f"\nequal split:     Rs {e['ordered']:,.0f} ordered, Rs {e['filled']:,.0f} expected to fill (Rs {e['unfilled']:,.0f} "
              f"would hang unfilled), slippage Rs {e['slippage']:,.0f}, expected profit Rs {e['profit']:+,.0f}")
        print(f"optimized split: Rs {o['ordered']:,.0f} ordered, Rs {o['filled']:,.0f} expected to fill, slippage Rs "
              f"{o['slippage']:,.0f}, expected profit Rs {o['profit']:+,.0f}")
    else:
        buy = plan[plan["qty"] > 0]
        print(f"\n{'rank':>4s} {'stock':12s} {'buy Rs':>10s} {'shares':>6s} {'slippage':>8s} {'profit':>8s}  limited by")
        for r in buy.itertuples():
            print(f"{r.rank:4d} {r.company_id:12s} {r.x_rs:10,.0f} {r.qty:6d} {r.x_rs * r.impact_bps / 1e4:8,.0f} "
                  f"{r.exp_net_rs:+8,.0f}  {r.limited_by}")
    fl = f"price floor Rs {s['min_price']:.0f}" if s["min_price"] else "no price floor"
    chk = "ASM / GSM list and trade-for-trade series checked" if s["surveillance_checked"] else \
        "ASM / GSM list NOT checked (trade-for-trade series only)"
    print(f"\nuniverse: {fl}; {chk}")
    if s["removed_untradable"]:
        print("left out - cannot be bought tonight and sold tomorrow morning (would have ranked):")
        for cid, rk, why in s["removed_untradable"]:
            print(f"  #{rk:<3d} {cid:12s} {why}")
    print(f"\n{s['stocks']} stocks, Rs {s['invested']:,.0f} invested ({100 * s['invested'] / capital:.0f}% of the capital); "
          f"{s['locked_excluded']} stock(s) locked at the upper band were left out")
    if s.get("why_cash"):
        print(s["why_cash"])
    if s["friday"]:
        print("Friday: this trade holds over the weekend - the worst nights in the backtest were Friday -> Monday.")
    for n in s["notes"]:
        print("note: " + n)
    runs.mkdir(parents=True, exist_ok=True)
    out = runs / f"overnight_sizing_{s['day']}.csv"
    plan.to_csv(out, index=False)
    buy = plan[plan["qty"] > 0]
    orders = buy.assign(exchange="NSE", side="B", product="C", price_type="MKT", retention="DAY")[
        ["tradingsymbol", "exchange", "side", "product", "price_type", "qty", "retention", "x_rs"]]
    ofile = runs / f"overnight_orders_{s['day']}.csv"
    orders.to_csv(ofile, index=False)
    print(f"plan -> {out}\norders to review -> {ofile}  (nothing was sent to the broker)")
    try:
        from .sizing import overnight_exit as X
        t = X.thresholds(plan)
        print(f"\nTOMORROW MORNING'S SALE (rule: hold only if the ML's predicted close is above the open; then limit at open + "
              f"{X.FRAC:.0%} of the way to the predicted high, rest at market at {X.CUTOFF}):")
        print(f"  {'stock':12s} {'shares':>6s} {'bought':>9s} {'pred high':>9s} {'pred close':>10s}   at 09:06-09:07")
        for r in t.itertuples():
            print(f"  {r.company_id:12s} {r.qty:6d} {r.price:9.2f} {r.high_p50:9.2f} {r.close_p50:10.2f}   indicative open "
                  f"BELOW {r.close_p50:.2f} -> hold with a limit; at or above -> sell in the auction")
        print("  run at 09:06:30:  .venv\\Scripts\\python -m nsealgo exit-overnight")
    except SystemExit as e:
        print(f"(no morning-sale preview: {e})")


@app.command("exit-overnight")
def exit_overnight(day: str = typer.Option(None, help="YYYY-MM-DD of the SALE (default: today)"),
                   buy_plan: Path = typer.Option(None, help="The evening's runs/overnight_sizing_<day>.csv (default: the "
                                                            "newest before today)"),
                   price: str = typer.Option(None, help="Opening prices typed in, e.g. TBZ=612.5,HFCL=101.2 (override the "
                                                        "pre-open recorder)"),
                   positions: Path = typer.Option(None, help="CSV with what was really bought: tradingsymbol (or "
                                                             "company_id), qty"),
                   frac: float = typer.Option(0.75, help="Target: open + frac x (predicted high - open)"),
                   cutoff: str = typer.Option("12:00", help="Sell what the limit has not sold at this time, at market")):
    """This morning's SALE of the overnight trade. Per stock: if the ML's predicted close (15:37 forecast) is above the
    indicative open, LIMIT SELL at open + 75% of the way to the predicted high from 09:15 and sell the rest at market at
    12:00; otherwise sell in the pre-open auction. Run at ~09:06:30 (the auction order window closes 09:07-09:08).
    Writes runs/overnight_exit_<day>.csv and runs/overnight_exit_orders_<day>.csv. Places NO orders."""
    import numpy as np
    from .sizing import liquidity as L
    from .sizing import overnight_exit as X
    c = cfg()
    runs = resolve_path(c, "runs_dir")
    pre = L.paths(Path(__file__).resolve().parents[2], _minute_archive_dir(c))["preopen"]
    px = {}
    for kv in (price or "").split(","):
        if "=" in kv:
            k, v = kv.split("=", 1)
            px[k.strip().upper()] = float(v)
    e, s = X.plan_exit(runs, pre, day=day, buy_plan=buy_plan, prices=px, positions=positions, frac=frac, cutoff=cutoff)
    print(f"\nSALE of {s['day']}  (bought per {Path(s['buy_plan']).name}; rule: hold if the predicted close > open, "
          f"limit at open + {frac:.0%} to the predicted high, rest at market at {cutoff})")
    print(f"{'stock':12s} {'shares':>6s} {'bought':>9s} {'open':>9s} {'pred close':>10s} {'pred high':>9s}  "
          f"{'DO THIS':34s} price from")
    for r in e.itertuples():
        do = (f"LIMIT SELL @ {r.limit_price:.2f} ({r.target_vs_open_pct:+.1f}%)" if r.action == "LIMIT"
              else "SELL in the auction (MKT) NOW")
        op = f"{r.open:9.2f}" if np.isfinite(r.open) else f"{'-':>9s}"
        print(f"{r.company_id:12s} {r.qty:6d} {r.bought_at:9.2f} {op} {r.pred_close:10.2f} {r.pred_high:9.2f}  {do:34s} "
              f"{r.price_from}")
    print("\nwhy:")
    for r in e.itertuples():
        print(f"  {r.company_id:12s} {r.why}")
    if s["n_limit"]:
        print(f"\nAt {cutoff}: cancel the unfilled LIMIT orders and sell the remaining shares at market.")
    if s["no_price"]:
        print(f"{s['no_price']} stock(s) have no pre-open price yet (recorder not running?) - pass --price SYMBOL=PRICE "
              "or they sell in the auction")
    runs.mkdir(parents=True, exist_ok=True)
    out = runs / f"overnight_exit_{s['day']}.csv"
    e.to_csv(out, index=False)
    of = runs / f"overnight_exit_orders_{s['day']}.csv"
    X.orders(e).to_csv(of, index=False)
    print(f"plan -> {out}\norders to review -> {of}  (nothing was sent to the broker)")


@app.command()
def backtest(start: str, end: str, granularity: str = typer.Option("daily", help="daily | minute"),
             strategies: str = typer.Option(None, help="Comma list (default: enabled in config)"),
             master_from: str = typer.Option(None, help="Broker name or CSV with an instrument master (futures, ticks)"),
             workers: str = typer.Option("auto", help="Worker processes: auto = one per physical core, or a number"),
             split: str = typer.Option(None, help="auto | strategy (one job per strategy) | none"),
             faithful: bool = typer.Option(False, "--faithful", help="Single engine exactly like live: compounding, "
                                           "Kelly learning, shared caps, drawdown breaker (one core)")):
    """Backtest on stored history through the live engine, spread across all cores (research mode)."""
    from .backtest.runner import load_master, run_backtest
    s = run_backtest(cfg(), start=_d(start), end=_d(end), granularity=granularity, names=_names(strategies),
                     master=load_master(master_from, cfg()), workers=workers, split=split,
                     mode="faithful" if faithful else None)
    _print_summary(s)


@app.command()
def walkforward(strategy: str, start: str, end: str, params: str = typer.Option(None, help="Comma list of params"),
                granularity: str = "daily", master_from: str = None,
                workers: str = typer.Option("auto", help="Variants run in parallel, one per worker")):
    """Parameter-perturbation robustness test (+/-20%), all variants in parallel."""
    from .backtest.runner import load_master
    from .backtest.walkforward import perturbation
    r = perturbation(cfg(), strategy, _d(start), _d(end), _names(params), granularity,
                     master=load_master(master_from, cfg()), workers=workers)
    for row in r["rows"]:
        print(f"{row['variant']:28s} trades {row['trades']:4d}  expectancy {row['expectancy']} {row['exp_unit']}  "
              f"net {row['net_pnl']}  maxDD {row['max_dd_pct']}%")
    print("verdict:", r["verdict"])


@app.command()
def feeds():
    """Which brokers give free live market data, their stream caps and how they log in."""
    from .brokers.registry import FEEDS, SDK_MODULE
    print(f"{'broker':10s} {'market data':34s} {'login':24s} {'cap':>5s}  {'SDK':13s} notes")
    for name, f in FEEDS.items():
        from .brokers.registry import adapter_class
        cap = getattr(adapter_class(name), "stream_capacity", 0) if f["cost"].startswith("free") or name == "zerodha" else 0
        sdk = "installed" if importlib.util.find_spec(SDK_MODULE[name]) else "not installed"
        print(f"{name:10s} {f['cost']:34s} {f['login']:24s} {cap or '-':>5}  {sdk:13s} {f['note']}")
    print("\nUse several free feeds together: nsealgo forwardtest --data-broker shoonya,angelone,fyers")


@app.command("feed-check")
def feed_check(brokers: str = typer.Option(..., help="Comma list, e.g. shoonya,angelone,fyers,upstox"),
               universe: str = typer.Option("nifty50", help="Universe CSV name, or a comma list of symbols"),
               minutes: float = typer.Option(10.0, help="How long to measure (run during market hours)")):
    """Stream the same instruments from several brokers and compare speed, completeness and stability."""
    from .data.universe import Universe
    from .forwardtest.feedcheck import run_feed_check, to_markdown
    c = cfg()
    keys = Universe(resolve_path(c, "universe_dir")).load(universe) if "," not in universe else \
        [eq(x) for x in universe.split(",") if x.strip()]
    if not keys:
        raise typer.BadParameter(f"universe '{universe}' not found")
    r = run_feed_check(c, brokers, keys + [c["regime"]["index_key"], "IDX:NIFTY50"], minutes)
    print(to_markdown(r))
    print("Report:", r["out_dir"])


@app.command()
def forwardtest(data_broker: str = typer.Option(None, help="Live data source(s): one broker or a comma list for "
                                                           "the multi-broker tick mesh (default: broker.data)"),
                daemon: bool = typer.Option(False, help="Run every trading day until stopped"),
                no_record: bool = False, strategies: str = None,
                benchmark: Path = typer.Option(None, help="Backtest summary.json for drift and drawdown gates")):
    """Paper-trade on live market data with the conservative simulated exchange."""
    from .forwardtest.runner import run_session_loop
    name = data_broker or cfg()["broker"]["data"]
    if name == "paper" or not name:
        raise typer.BadParameter("forward testing needs live data: set broker.data or --data-broker "
                                 "(e.g. shoonya,angelone,fyers)")
    run_session_loop(cfg(), mode="forward", execution_name="paper", data_name=name, names=_names(strategies),
                     daemon=daemon, record=not no_record, benchmark=benchmark)


@app.command("tbt-record")
def tbt_record(symbols: str = typer.Option(..., help="Comma list of NSE stocks, at most 15, e.g. SBIN,RELIANCE,INFY"),
               until: str = typer.Option("15:31", help="Stop at this IST time"),
               minutes: float = typer.Option(None, help="Or stop after this many minutes")):
    """Record Fyers TBT 50-level depth for up to 15 stocks to parquet (paths.tbt_dir), without running any strategy.
    Uses today's Fyers token from NSEUIENGINE's 08:00 task (or `nsealgo login fyers`)."""
    import time as _t
    from .brokers.registry import make_broker
    from .core.clock import IST, at
    from .core.models import eq
    from .data.fyers_tbt import FyersTbtFeed
    from .data.tbt_recorder import TbtRecorder
    c = cfg()
    fy = make_broker("fyers", c, resolve_path(c, "cache_dir"))
    fy.login(interactive=False)
    fy.load_instruments()
    keys = [eq(s) for s in symbols.split(",") if s.strip()][:15]
    rec = TbtRecorder(resolve_path(c, "tbt_dir"))
    oc = (c.get("feeds") or {}).get("depth_overlay") or {}
    feed = FyersTbtFeed(((c.get("broker") or {}).get("brokers") or {}).get("fyers", {}).get("client_id"), fy._token,
                        lambda k: fy.instrument(k).broker_symbol, connections=int(oc.get("tbt_connections", 3)),
                        per_connection=int(oc.get("tbt_per_connection", 5)), recorder=rec)
    feed.set_keys(keys)
    end = datetime.now(IST).timestamp() + minutes * 60 if minutes else at(datetime.now(IST).date(), until).timestamp()
    print(f"recording {len(keys)} stocks to {rec.dir} until {datetime.fromtimestamp(end, IST):%H:%M} (Ctrl+C stops)")
    try:
        while datetime.now(IST).timestamp() < end:
            _t.sleep(5)
            rec.flush()
            if feed.errors:
                print("Fyers:", feed.errors[-1])
    except KeyboardInterrupt:
        pass
    feed.close()
    print(f"{rec.n_rows:,} messages in {rec.n_files} file(s). Check: nsealgo tbt-check")


@app.command("tbt-check")
def tbt_check(day: str = typer.Argument(None, help="YYYY-MM-DD (default: the latest recorded day)")):
    """What a TBT recording holds: messages, how often ltq / vtt_diff come, latency percentiles."""
    from .data.tbt_recorder import check
    root = resolve_path(cfg(), "tbt_dir")
    days = sorted(p for p in root.glob("????-??-??") if p.is_dir())
    if not days:
        print(f"no TBT recordings in {root}")
        raise typer.Exit(1)
    print(check(root / day if day else days[-1]))


@app.command()
def replay(recording: Path, strategies: str = None, master_from: str = None):
    """Replay a recorded session (recordings/YYYY-MM-DD.jsonl.gz) through the full stack."""
    from .backtest.runner import load_master
    from .forwardtest.runner import run_replay
    _print_summary(run_replay(cfg(), recording, _names(strategies), load_master(master_from, cfg())))


@app.command()
def live(broker: str = typer.Option(None, help="Send ALL orders to this broker (default: broker.orders)"),
         data_broker: str = typer.Option(None, help="Data broker (default: broker.data or the execution broker)"),
         strategies: str = None, daemon: bool = False,
         i_understand_the_risk: bool = typer.Option(False, "--i-understand-the-risk")):
    """Trade real money. Requires the explicit risk flag."""
    from .forwardtest.runner import run_session_loop
    from .brokers.base import BrokerError
    from .brokers.routing import describe, order_brokers
    c = cfg()
    try:
        routes = order_brokers(c, broker)
    except BrokerError as ex:
        raise typer.BadParameter(str(ex))
    print(describe(c, broker))
    if not i_understand_the_risk:
        print("Live trading places real orders. Promote only strategies that passed the forward-test gates, start at a "
              "quarter of normal risk, and keep `nsealgo kill` handy. Re-run with --i-understand-the-risk.")
        raise typer.Exit(2)
    data = data_broker or (c["broker"]["data"] if c["broker"]["data"] not in ("paper", None) else routes["MIS"])
    run_session_loop(c, mode="live", execution_name=broker or "auto", data_name=data, names=_names(strategies),
                     daemon=daemon)


@app.command()
def report(run_dir: Path, benchmark: Path = None):
    """Rebuild the HTML/Markdown report for a run directory (containing state.db)."""
    from .core.storage import Storage
    from .forwardtest.report import build_report
    s = build_report(Storage(run_dir / "state.db"), cfg(), run_dir, f"Report: {run_dir.name}", "report", benchmark)
    _print_summary(s)
    print("HTML:", run_dir / "report.html")


@app.command()
def kill(mode: str = "live", book: str = typer.Option("all", help="all | intraday | swing")):
    """Emergency stop. all: no new entries, intraday flattened. intraday: no new intraday entries, intraday
    flattened. swing: no new swing entries (open swing positions keep their stops)."""
    if book not in ("all", "intraday", "swing"):
        raise typer.BadParameter("book must be all, intraday or swing")
    p = resolve_path(cfg(), "runs_dir") / mode / ("KILL" if book == "all" else f"KILL_{book.upper()}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(datetime.now().isoformat(), encoding="utf-8")
    print(f"kill switch set: {p}  (remove with `nsealgo resume --mode {mode}`)")


@app.command()
def resume(mode: str = "live", book: str = typer.Option("all", help="all | intraday | swing")):
    """Remove the kill switch(es) (restart the process to clear an in-memory halt)."""
    d = resolve_path(cfg(), "runs_dir") / mode
    names = ["KILL", "KILL_INTRADAY", "KILL_SWING"] if book == "all" else [f"KILL_{book.upper()}"]
    for n in names:
        (d / n).unlink(missing_ok=True)
    print("kill switch cleared: " + ", ".join(names))


@app.command()
def demo(directory: Path = typer.Option(Path("demo_run"), "--dir"), days: int = 560, swing_days: int = 250,
         intraday_days: int = 8, workers: str = typer.Option("auto", help="Worker processes for the backtests")):
    """Offline end-to-end demo on a SYNTHETIC market: daily + minute backtests and a quote-level replay."""
    from .backtest.runner import load_master, run_backtest
    from .config import deep_merge
    from .core.clock import NSECalendar
    from .data.synthetic import build_synthetic, write_recording_from_bars
    from .forwardtest.runner import run_replay
    directory = directory.resolve()
    cal = NSECalendar()
    end = cal.prev_trading_day(date.today())
    print(f"Building a synthetic market in {directory} (synthetic results say nothing about real profitability) ...")
    info = build_synthetic(directory, end=end, days=days)
    c = deep_merge(cfg(), demo_overrides(directory))
    c["_root"] = str(directory)
    master = load_master(str(directory / "data" / "synthetic_master.csv"), c)
    d = info["days"]
    swing = run_backtest(c, start=d[-swing_days], end=d[-1], granularity="daily", history=info["history"],
                         master=master, title="Demo: swing strategies, daily bars (synthetic)", workers=workers)
    _print_summary(swing)
    intra = run_backtest(c, start=d[-intraday_days], end=d[-1], granularity="minute", history=info["history"],
                         master=master, names=["orb", "vwap_fade", "compression_breakout"], workers=workers,
                         title="Demo: intraday strategies, 1-minute bars (synthetic)")
    _print_summary(intra)
    keys = [f"NSE:SYN{i:02d}" for i in range(30)] + ["NSE:SYNETF"]
    rec = write_recording_from_bars(info["history"], keys, d[-1], directory / "recordings" / f"{d[-1]}.jsonl.gz")
    rp = run_replay(c, rec, ["orb", "vwap_fade"], master, info["history"])
    _print_summary(rp)
    print(f"\nReports:\n  {swing['run_dir']}/report.html\n  {intra['run_dir']}/report.html\n  {rp['run_dir']}/report.html")


from .data.synthetic import demo_overrides  # noqa: E402  (kept importable from nsealgo.cli)


@app.command()
def anatomy(symbol: str, days: int = typer.Option(250, help="Sessions of daily history to use"),
            move: float = typer.Option(0.01, help="Event size, e.g. 0.01 = 1%")):
    """Bar anatomy of one stock: gap / excursions / drift, empirical tail rates vs the Edgeworth expansion."""
    from .data.history import HistoryStore
    from .research import anatomy as _anatomy
    c = cfg()
    key = symbol if ":" in symbol else f"NSE:{symbol.upper()}"
    d = HistoryStore(resolve_path(c, "data_dir") / "history.db").daily(key).iloc[-(days + 1):]
    if len(d) < 60:
        print(f"not enough daily history for {key} ({len(d)} bars): run fetch-history first")
        raise typer.Exit(1)
    r = _anatomy(d, move)
    print(f"{key}: {r['sessions']} sessions to {r['end']} (log components; gap + drift = close-to-close)\n")
    print(f"  {'component':9s} {'mean':>8s} {'std':>7s} {'skew':>6s} {'exkurt':>7s}  {'event':22s} {'empirical':>9s} {'Edgeworth':>9s}")
    for comp, x in r["components"].items():
        edge = f"{x['p_edgeworth']:.3f}" if x["p_edgeworth"] is not None else "invalid"
        print(f"  {comp:9s} {x['mean']*100:7.3f}% {x['std']*100:6.2f}% {x['skew']:6.2f} {x['exkurt']:7.2f}  "
              f"{x['event']:22s} {x['p_empirical']:9.3f} {edge:>9s}")
    s98 = r["stop_noise_98"]
    cf = f"{s98['cornish_fisher']:.2%}" if s98["cornish_fisher"] is not None else "invalid for this distribution"
    print(f"\n  2%-noise stop distance below the open: empirical {s98['empirical']:.2%}, Cornish-Fisher {cf}")
    print("  Empirical rates are the reference; the expansion only shows how far a skew/kurtosis fit can be trusted.")


def _minute_sessions(h, key: str, days: int):
    dates = sorted(h.minute_dates(key))[-days:]
    return h.minute(key, dates[0], dates[-1]) if dates else None


@app.command()
def leadlag(leader: str, follower: str, days: int = typer.Option(20, help="Most recent sessions of minute bars"),
            lags: str = typer.Option("1,2,3,5", help="Lags in minutes")):
    """Measure minute-level lead-lag (transfer entropy + lagged correlation) from stored 1-minute bars."""
    from .data.history import HistoryStore
    from .research import lead_lag
    h = HistoryStore(resolve_path(cfg(), "data_dir") / "history.db")
    kx, ky = (s if ":" in s else f"NSE:{s.upper()}" for s in (leader, follower))
    x, y = _minute_sessions(h, kx, days), _minute_sessions(h, ky, days)
    if x is None or y is None or not len(x) or not len(y):
        print(f"no minute history for {kx if x is None or not len(x) else ky}: run fetch-history --minute-days N")
        raise typer.Exit(1)
    r = lead_lag(x, y, [int(v) for v in lags.split(",")])
    if not r["rows"]:
        print(r["note"])
        raise typer.Exit(1)
    print(f"{kx} -> {ky}: {r['n']} aligned 1-minute returns over {r['sessions']} sessions "
          f"(significant only if p < {r['bonferroni']:.4f}, Bonferroni over {2 * len(r['rows'])} tests)\n")
    print(f"  {'lag':>4s} {'TE x->y':>9s} {'p':>6s} {'TE y->x':>9s} {'p':>6s} {'corr x->y':>10s}")
    for x_ in r["rows"]:
        print(f"  {x_['lag_min']:>3d}m {x_['te_xy']:9.5f} {x_['p_xy']:6.3f} {x_['te_yx']:9.5f} {x_['p_yx']:6.3f} {x_['corr_xy']:10.3f}")
    print("\n  TE in bits after subtracting the shuffle baseline. Even a significant minute-level lead must survive"
          "\n  your order latency, the 10 orders/s cap and costs to be tradable; see docs/04-design/STRATEGY_RESEARCH.md.")


@app.command()
def fractal(symbol: str, days: int = typer.Option(20, help="Most recent sessions of minute bars")):
    """Trend vs mean-reversion diagnostics from stored 1-minute bars (variance ratios, DFA Hurst, MF-DFA)."""
    from .data.history import HistoryStore
    from .research import fractal as _fractal
    key = symbol if ":" in symbol else f"NSE:{symbol.upper()}"
    m = _minute_sessions(HistoryStore(resolve_path(cfg(), "data_dir") / "history.db"), key, days)
    if m is None or len(m) < 1000:
        print(f"not enough minute history for {key}: run fetch-history --minute-days N")
        raise typer.Exit(1)
    r = _fractal(m)
    print(f"{key}: {r['n']} one-minute returns over {r['sessions']} sessions\n")
    for horizon, v in r["variance_ratio"].items():
        verdict = "trending" if v["z"] >= 2 else "mean-reverting" if v["z"] <= -2 else "no significant structure"
        print(f"  variance ratio, {horizon:>2d}-minute horizon: VR {v['vr']:.3f}  z* {v['z']:+.2f}  -> {verdict}")
    print(f"  DFA Hurst (all sessions): {r['hurst']:.3f}   MF-DFA delta_h {r['delta_h']:.3f}  delta_alpha {r['delta_alpha']:.3f}")
    mh = r["morning_hurst"]
    print(f"  single-morning Hurst over {mh['n']} mornings: mean {mh['mean']:.2f}, range {mh['min']:.2f} to {mh['max']:.2f}"
          f"\n  (a spread this wide is estimation noise: one morning can't tell trending from reverting)")


def _redact(url: str | None) -> str:
    import re as _re
    return _re.sub(r"//[^@/]+@", "//***@", url) if url else ""


@app.command()
def netcheck(broker: str = typer.Argument(None, help="Broker to test (default: your intraday order broker)"),
             n: int = typer.Option(5, help="Requests per measurement"),
             url: str = typer.Option(None, help="REST base URL (default: the broker's)")):
    """Order path: egress IP through the configured proxy, and new-connection vs kept-alive latency."""
    import statistics
    from .brokers.transport import BrokerTransport
    c = cfg()
    if not broker:
        from .brokers.routing import order_brokers
        try:
            broker = order_brokers(c)["MIS"]
        except Exception:
            broker = "fyers"
    bcfg = ((c.get("broker") or {}).get("brokers") or {}).get(broker) or {}
    if not url:
        from .brokers.noren import HOSTS
        known = {"fyers": "https://api-t1.fyers.in/api/v3/"}
        url = bcfg.get("host") or known.get(broker) or (HOSTS[broker][0] if broker in HOSTS else None)
    if not url:
        print(f"no REST URL known for {broker}; pass --url")
        raise typer.Exit(1)
    t = BrokerTransport.from_config(broker, url, bcfg)
    print(f"{broker}: {url}   proxy: {_redact(t.proxy) or 'none (direct)'}")
    ok = True
    try:
        ip = t.egress_ip()
        if t.static_ip:
            ok = ip == t.static_ip
            print(f"egress IP {ip}: " + ("matches the registered static IP" if ok else f"NOT the static IP {t.static_ip}"))
        else:
            print(f"egress IP {ip} (set static_ip in config to enforce it)")
    except Exception as ex:
        ok = False
        print(f"egress IP: not reachable ({ex})")
    cold, warm = [], []
    for _ in range(max(n, 1)):
        x = BrokerTransport.from_config(broker, url, bcfg)
        ms = x.warm()
        x.close()
        if ms is not None:
            cold.append(ms)
    t.warm()
    for _ in range(max(n, 1)):
        ms = t.warm()
        if ms is not None:
            warm.append(ms)
    if cold:
        print(f"new connection per request (DNS+TCP+TLS{'+proxy' if t.proxy else ''}): median {statistics.median(cold):.0f} ms")
    if warm:
        print(f"kept-alive connection:                         median {statistics.median(warm):.0f} ms")
    if cold and warm:
        print(f"saved per order by keep-alive: ~{statistics.median(cold) - statistics.median(warm):.0f} ms")
    t.close()
    if not (ok and cold and warm):
        raise typer.Exit(1)


@app.command()
def desk(mode: str = typer.Option("forward", help="Session to watch: forward or live"),
         demo: bool = typer.Option(False, "--demo", help="Synthetic data: try the windows without a session"),
         review: bool = typer.Option(False, "--review", help="Chart saved session days (no session needed)"),
         day: str = typer.Option(None, "--date", help="Day to open in review, YYYY-MM-DD (default: the latest)")):
    """Trading desk: dashboard, tier-1 50-level depth, market watch. Read-only except the kill switch."""
    try:
        from .desk.app import run_desk
    except ImportError:
        print("the desk needs PySide6: pip install -e .[desk]   (or: pip install PySide6-Essentials)")
        raise typer.Exit(1)
    from .desk.client import DemoClient, MonitorClient, ReviewClient
    if demo:
        client = DemoClient()
    elif review:
        c = cfg()
        try:
            client = ReviewClient(resolve_path(c, "runs_dir") / mode, day,
                                  history_path=resolve_path(c, "data_dir") / "history.db")
        except (FileNotFoundError, ValueError) as ex:
            print(ex)
            raise typer.Exit(1)
    else:
        run_dir = resolve_path(cfg(), "runs_dir") / mode
        if not (run_dir / "monitor.json").exists():
            print(f"no running {mode} session found ({run_dir / 'monitor.json'} is missing): start "
                  f"`nsealgo {'live' if mode == 'live' else 'forwardtest'}` first, or try `nsealgo desk --demo`")
            raise typer.Exit(1)
        client = MonitorClient.from_run_dir(run_dir)
    raise typer.Exit(run_desk(client))


@app.command("book-study")
def book_study_cmd(recordings: list[Path] = typer.Argument(..., help="Recorded sessions with depth (recordings/*.jsonl.gz); "
                                                                    "several days are studied together in memory"),
                   horizons: str = typer.Option("5,30,60", help="Seconds ahead, comma-separated"),
                   levels: str = typer.Option("1,5,10,20", help="Depth levels to compare, comma-separated")):
    """Does deeper order-book data predict short-term moves better than the top level? On your recording."""
    import gzip
    import json
    from .compute import init_compute
    from .core.models import quote_from_dict
    from .research import book_study
    c = cfg()
    init_compute(c, resolve_path(c, "cache_dir"))
    quotes = []
    for rec in recordings:
        opener = gzip.open if str(rec).endswith(".gz") else open
        with opener(rec, "rt", encoding="utf-8") as f:
            quotes += [quote_from_dict(json.loads(line)) for line in f if line.strip()]
    print(f"{len(quotes):,} quotes from {len(recordings)} recording(s)")
    df = book_study(quotes, tuple(int(x) for x in horizons.split(",")), tuple(int(x) for x in levels.split(",")))
    if df.empty:
        print("no instruments with depth in this recording: set feeds.record_depth (e.g. 20) before recording")
        raise typer.Exit(1)
    print(df.to_string(index=False))
    print("ic = rank correlation with the forward mid return; compare rows by levels to see what depth adds")


@app.command()
def gpu(fresh: bool = typer.Option(False, "--fresh", help="Measure again instead of using cached decisions")):
    """GPU check: the PyTorch device, and per kernel NumPy-vs-GPU agreement, timing and the choice made."""
    from .compute.backend import bucket, init_compute
    c = cfg()
    cache = resolve_path(c, "cache_dir")
    if fresh:
        (cache / "compute_autotune.json").unlink(missing_ok=True)
    comp = init_compute(c, cache)
    if comp.gpu is None:
        import importlib.util
        why = ("PyTorch is not installed; run scripts\\windows\\setup.ps1 -Gpu" if importlib.util.find_spec("torch") is None
               else "PyTorch sees no usable GPU (torch.cuda.is_available() is False)")
        if str((c.get("compute") or {}).get("device", "auto")) == "cpu":
            why = "compute.device is cpu in config"
        print(f"GPU: none. {why}. Everything runs on the CPU with NumPy.")
    else:
        print(f"GPU: {comp.sig}")
    cases = [("fragility", (256, 60, 50)), ("fragility", (256, 60, 200)), ("fragility", (64, 60, 500)),
             ("book_features", (300, 30)), ("book_features", (65536, 30)), ("mlofi", (300, 30)), ("mlofi", (65536, 30))]
    print(f"{'kernel':14s} {'size bucket':>14s} {'cpu ms':>9s} {'gpu ms':>9s}  {'matches':7s}  uses")
    for k, shape in cases:
        d = comp.tune(k, shape)
        gms = f"{d['gpu_ms']:9.2f}" if d.get("gpu_ms") is not None else f"{'-':>9s}"
        par = {None: "-", True: "yes", False: "NO"}[d.get("parity")]
        print(f"{k:14s} {'x'.join(map(str, bucket(shape))):>14s} {d['cpu_ms']:9.2f} {gms}  {par:7s}  {d['choice']}"
              + (f"  ({d['error'][:60]})" if d.get("error") else ""))


@app.command()
def accounts(pin: list[str] = typer.Option(None, "--pin", help="SYMBOL=ACCOUNT: trade this stock at this account"),
             unpin: list[str] = typer.Option(None, "--unpin", help="SYMBOL: let the balancer place it again"),
             rebalance: bool = typer.Option(False, "--rebalance", help="Re-spread idle, unpinned stocks by capacity")):
    """Multi-broker allocation table: which account trades which stock."""
    from .brokers.multi import AllocationTable, account_capacity, state_path
    c = cfg()
    spec = c.get("accounts") or {}
    if not spec:
        print("no accounts configured: see the accounts/routing example in config/system.yaml")
        raise typer.Exit(1)
    cap = account_capacity(spec)
    t = AllocationTable(list(spec), cap, (c.get("routing") or {}).get("pins"), state_path(resolve_path(c, "cache_dir")))
    for p in pin or []:
        sym, _, acct = p.partition("=")
        t.pin(sym, acct.strip())
    for s in unpin or []:
        t.unpin(s)
    if rebalance:
        moves = t.rebalance()
        print(f"moved {len(moves)} idle stock(s)" + "".join(f"\n  {s}: {a} -> {b}" for s, a, b in moves))
    held = {}
    for k, a in t.owners.items():
        held[a] = held.get(a, 0) + 1
    for acct, syms in t.summary().items():
        pinned = sum(1 for s in syms if s in t.pins)
        print(f"{acct:12s} {cap[acct]:>4.0f}/s  {len(syms):4d} stocks ({pinned} pinned, {held.get(acct, 0)} open)  "
              + ", ".join(syms[:15]) + (" ..." if len(syms) > 15 else ""))


@app.command()
def hardware():
    """Detected cores/threads/RAM, the worker counts nsealgo will use, BLAS libraries and start method."""
    from .parallel.hardware import blas_info, detect, workers_for
    from .parallel.pool import mp_context
    hw, pc = detect(), cfg().get("parallel", {})
    print(f"CPU: {hw.physical} physical cores, {hw.logical} hardware threads (SMT {'on' if hw.smt else 'off'}); "
          f"usable here: {hw.usable} threads / {hw.usable_physical} cores")
    print(f"RAM: {hw.memory_gb} GB | {hw.system} | Python {hw.python}")
    print(f"backtest workers: {workers_for('cpu', pc.get('workers'), use_smt=bool(pc.get('use_smt')))} processes "
          f"(with SMT: {workers_for('cpu', None, use_smt=True)}) | I/O threads: {workers_for('io', pc.get('io_threads'))}")
    print(f"process start method: {mp_context().get_start_method()} | BLAS: {', '.join(blas_info()) or 'n/a'} "
          f"(pinned to 1 thread inside workers)")


@app.command()
def bench(directory: Path = typer.Option(Path("bench_run"), "--dir"), workers: str = typer.Option("auto"),
          swing_days: int = 1000, intraday_days: int = 10):
    """Measure the multi-core speedup on this machine (synthetic market; checks results are identical)."""
    from .parallel.bench import run_bench
    r = run_bench(directory, workers=workers, swing_days=swing_days, intraday_days=intraday_days)
    hw = r["hardware"]
    print(f"\n{hw['physical']} cores / {hw['logical']} threads, {hw['memory_gb']} GB RAM, {r['workers']} workers")
    for x in r["rows"]:
        print(f"  {x['case']:26s} {x['sessions']} sessions, {x['trades']} trades: 1 worker {x['sequential_s']}s -> "
              f"{x['workers']} workers {x['parallel_s']}s ({x['jobs']} jobs) = {x['speedup']}x, "
              f"identical trades: {x['identical_trades']}")


def _print_summary(s: dict) -> None:
    o = s["overall"]
    print(f"\n{s['title']}\n  equity {o['final_equity']:,} ({o['return_pct']}%), max DD {o['max_dd_pct']}%, "
          f"Sharpe {o['sharpe']}, trades {s['trades']}, logged errors {s['errors']}")
    for n, m in s["strategies"].items():
        if m["trades"]:
            print(f"  {n:12s} {m['trades']:4d} trades  win {m['win_rate']}  expectancy {m['expectancy']} {m['exp_unit']}"
                  f"  net ₹{m['net_pnl']:,}  -> {m['verdict']}")


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    app()
