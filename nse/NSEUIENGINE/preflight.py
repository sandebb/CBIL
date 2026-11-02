"""
NSEUIENGINE - preflight.py: is everything ready for live trading on the next session?

    python preflight.py proxy --csv <staticip.in csv>   find the working proxy URL for the static IP (prints JSON)
    python preflight.py check                           GO / NO-GO checklist for the next session

Read-only: places no orders.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import base64
import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fyers_token import IST, FyersTokenMissing, _read, get_access_token, get_client_id  # noqa: E402

API = "https://api-t1.fyers.in/api/v3"
OK, WARN, FAIL = "OK  ", "WARN", "FAIL"


def egress(proxy: str | None) -> str:
    px = {"http": proxy, "https": proxy} if proxy else None
    return requests.get("https://api.ipify.org", proxies=px, timeout=20).text.strip()


def cmd_proxy(a) -> int:
    with open(a.csv, newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f)]
    row = next((r for r in rows if r.get("broker_name", "").strip().lower() == "fyers"), rows[0] if rows else None)
    if not row:
        print("no row in the CSV", file=sys.stderr)
        return 1
    ip, user, pw = row["ip"].strip(), row["userid"].strip(), row["password"].strip()
    host, port = row["hostname"].strip(), row["port"].strip()
    print(f"static IP {ip} via {host}:{port} (valid until {row.get('validity', '?')})", file=sys.stderr)
    for scheme in ("http", "https"):
        url = f"{scheme}://{user}:{pw}@{host}:{port}"
        try:
            got = egress(url)
        except Exception as ex:
            print(f"  {scheme}:// proxy: {ex.__class__.__name__}: {str(ex)[:150]}", file=sys.stderr)
            continue
        print(f"  {scheme}:// proxy: traffic leaves from {got}", file=sys.stderr)
        if got == ip:
            print(json.dumps({"proxy": url, "ip": ip, "validity": row.get("validity", "")}))
            return 0
    print("the proxy did not return the static IP with either scheme", file=sys.stderr)
    return 1


def jwt_claims(tok: str) -> dict:
    try:
        part = tok.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return {}


def cmd_check(a) -> int:
    res = []

    def add(state, what, detail=""):
        res.append((state, what, detail))
        print(f"[{state}] {what}" + (f" - {detail}" if detail else ""), flush=True)

    import swing_trader as T
    cfg = T.load_config()
    tr = T.Trader(cfg, True)
    nxt = T.now().date()
    if T.now().hour >= 16 or not tr.cal.is_session(nxt):
        nxt += timedelta(days=1)
        while not tr.cal.is_session(nxt):
            nxt += timedelta(days=1)
    print(f"\nPre-flight for the session on {nxt:%a %d %b %Y}\n")

    # 1. clock
    try:
        r = requests.head("https://api-t1.fyers.in/", timeout=10)
        off = (datetime.now(timezone.utc) - parsedate_to_datetime(r.headers["Date"])).total_seconds()
        add(OK if abs(off) < 5 else FAIL, "PC clock", f"{off:+.1f} s vs Fyers (TOTP and the 09:00 pre-open need it)")
    except Exception as ex:
        add(WARN, "PC clock", f"could not check ({ex})")

    # 2. token + profile + DDPI
    s = requests.Session()
    tok = None
    try:
        tok = get_access_token(require_today=False)
        s.headers["Authorization"] = f"{get_client_id()}:{tok}"
        d = s.get(f"{API}/profile", timeout=15).json()
        if d.get("s") == "ok":
            p = d.get("data", {})
            add(OK, "Fyers login", f"{p.get('name', '')} ({p.get('fy_id', '')}), token dated {_read('FYERS_TOKEN_DATE')};"
                                   f" a fresh one is made at 08:00 on {nxt:%a}")
        else:
            add(FAIL, "Fyers login", str(d.get("message") or d))
        app = get_client_id()
        add(OK if app.endswith("-200") else FAIL, "App type", f"{app} ({'SEBI retail-algo app' if app.endswith('-200') else 'needs the new -200 app'})")
    except FyersTokenMissing as ex:
        add(FAIL, "Fyers login", str(ex))
    claims = jwt_claims(tok) if tok else {}
    ddpi = next((str(v) for k, v in claims.items() if "ddpi" in k.lower()), None)
    if ddpi is None:
        add(WARN, "DDPI", "could not read it from the token - check Fyers > Profile > DDPI")
    elif ddpi.upper() in ("Y", "TRUE", "1"):
        add(OK, "DDPI", "active - the API can sell from your demat (stops and exits)")
    else:
        add(WARN, "DDPI", "NOT active yet. Buys and same-day stops work, but once shares reach your demat (from about "
                          "Wednesday) stops and exits are rejected. Activate it on Fyers (Profile > DDPI) now, or "
                          "authorise eDIS (TPIN) for your holdings each morning")

    # 3. static IP
    proxy, ip = _read("STATICIP_PROXY"), _read("STATIC_IP")
    if not (proxy and ip):
        add(FAIL, "Static IP variables", "STATICIP_PROXY / STATIC_IP not set (run go_live.ps1)")
    else:
        try:
            got = egress(proxy)
            add(OK if got == ip else FAIL, "Static IP route", f"orders leave from {got}" + ("" if got == ip else f", expected {ip}"))
        except Exception as ex:
            add(FAIL, "Static IP route", f"proxy not working: {ex.__class__.__name__}")
        if tok:
            try:
                d = s.get(f"{API}/orders", timeout=20, proxies={"http": proxy, "https": proxy}).json()
                add(OK if d.get("s") == "ok" else WARN, "Fyers through the proxy",
                    "order book reachable" if d.get("s") == "ok" else str(d.get("message") or d)[:150])
            except Exception as ex:
                add(FAIL, "Fyers through the proxy", f"{ex.__class__.__name__}: {str(ex)[:120]}")
        add(WARN, "IP whitelisted on the app",
            f"cannot be tested without an order: make sure {ip} is saved on app {get_client_id()} at myapi.fyers.in")

    # 4. signals + plan + cash
    plan = tr.load_plan(nxt)
    if plan is None:
        add(FAIL, "Signal files", f"no signals/exits/session files for {nxt} - run: swing_trader.py evening")
    else:
        m = plan["meta"]
        good = m.get("session") == f"{nxt}" and not m.get("stale")
        add(OK if good else FAIL, "Signal files", f"from the {m.get('signal_date')} close, regime {m.get('regime')}")
        cash = None
        try:
            cash = T.Fyers(cfg, True).funds_available() if tok else None
        except Exception as ex:
            add(WARN, "Funds", f"could not read ({ex})")
        tr.master = T.symbol_master()
        tr.today = nxt
        take, _ = tr.pick_entries(plan, None)
        cost = sum(T.fnum(r["limit_price"] or r["trigger_or_limit"]) * r["qty"] for r in take)
        risk = sum((T.fnum(r["limit_price"] or r["trigger_or_limit"]) - T.fnum(r["stop"])) * r["qty"] for r in take)
        add(OK, "Plan", f"{len(take)} buys, Rs {cost:,.0f} if all fill, Rs {risk:,.0f} at risk to the stops: "
                        + ", ".join(f"{r['symbol']} x{r['qty']}" for r in take))
        if cash is not None:
            add(OK if cash >= cost * 1.01 else FAIL, "Funds", f"Rs {cash:,.0f} available for Rs {cost:,.0f} of buys")

    # 5. config + scheduled tasks
    add(OK if cfg.get("require_static_ip", True) else WARN, "require_static_ip", str(cfg.get("require_static_ip")))
    add(OK if cfg.get("live") else WARN, "Mode", "LIVE - real orders" if cfg.get("live") else
        'DRY RUN - set "live": true in state\\trader_config.json (or go_live.ps1 -GoLive) to trade')
    for t in ("NSEUIENGINE-FyersToken", "NSEUIENGINE-Trader-Morning", "NSEUIENGINE-Trader-Evening"):
        r = subprocess.run(["schtasks", "/query", "/tn", t], capture_output=True, text=True)
        add(OK if r.returncode == 0 else FAIL, f"Task {t}", "registered" if r.returncode == 0 else "missing")

    fails = [w for st, w, _ in res if st == FAIL]
    print("\n" + ("GO - ready for " + f"{nxt:%a %d %b}" if not fails else "NO-GO - fix: " + ", ".join(fails)))
    print("Reminder: keep the PC on, logged in and awake from 08:00 to 15:35 (Settings > Power > Sleep: Never).\n")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("proxy")
    p.add_argument("--csv", required=True)
    sub.add_parser("check")
    a = ap.parse_args(argv)
    return cmd_proxy(a) if a.cmd == "proxy" else cmd_check(a)


if __name__ == "__main__":
    sys.exit(main())
