"""
NSEUIENGINE - show current Fyers holdings.

    .venv\\Scripts\\python.exe holdings.py            table in the terminal + reports\\holdings_<date>.html
    .venv\\Scripts\\python.exe holdings.py --open     ...and open the HTML page in the browser
    .venv\\Scripts\\python.exe holdings.py --no-html  terminal only
    .venv\\Scripts\\python.exe holdings.py --csv      also write reports\\holdings_<date>.csv

Uses today's token from fyers_token_manager (FYERS_ACCESS_TOKEN / FYERS_CLIENT_ID), read fresh from the registry.
Fyers API v3: GET /api/v3/holdings (holdings + overall totals), GET /data/quotes (day change, 50 symbols per call).
Read-only: places no orders. The HTML file stays on this PC.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import argparse
import csv
import html
import json
import os
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fyers_token import IST, FyersTokenMissing, _read, data_dir, get_access_token, get_client_id  # noqa: E402

API = "https://api-t1.fyers.in/api/v3"
DATA = "https://api-t1.fyers.in/data"
HOME = Path(os.environ.get("NSEUIENGINE_HOME") or Path(__file__).resolve().parent)
REPORTS = data_dir() / "reports"


class FyersError(RuntimeError):
    pass


# ---------------------------------------------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------------------------------------------
class Fyers:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"{get_client_id()}:{get_access_token()}",
                               "Accept": "application/json", "User-Agent": "NSEUIENGINE/holdings"})

    def _get(self, url, **params) -> dict:
        r = self.s.get(url, params=params or None, timeout=20)
        try:
            d = r.json()
        except ValueError:
            raise FyersError(f"{url}: HTTP {r.status_code}, non-JSON reply")
        if d.get("s") != "ok":
            msg = d.get("message") or d
            if d.get("code") in (-8, -15, -16, -17, -300):
                msg = f"{msg} - token rejected; run fyers_token_manager.py --force"
            raise FyersError(f"{url.rsplit('/', 1)[-1]}: {msg}")
        return d

    def holdings(self) -> dict:
        return self._get(f"{API}/holdings")

    def quotes(self, symbols: list[str]) -> dict[str, dict]:
        out = {}
        for i in range(0, len(symbols), 50):
            d = self._get(f"{DATA}/quotes", symbols=",".join(symbols[i:i + 50]))
            for q in d.get("d", []):
                if q.get("s") == "ok" or q.get("v"):
                    out[q.get("n")] = q.get("v") or {}
        return out


# ---------------------------------------------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------------------------------------------
def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def build_rows(raw: dict, quotes: dict[str, dict]) -> list[dict]:
    rows = []
    for h in raw.get("holdings", []):
        sym = h.get("symbol", "")
        qty, t1 = int(_f(h.get("quantity"))), int(_f(h.get("qty_t1")))
        # Fyers: HLD = in demat, T1 = bought but not yet delivered. Count T1 shares once.
        total = max(qty, t1) if h.get("holdingType") == "T1" else qty + t1
        avg = _f(h.get("costPrice"))
        q = quotes.get(sym, {})
        ltp = _f(q.get("lp")) or _f(h.get("ltp"))
        invested = avg * total
        value = ltp * total if ltp else _f(h.get("marketVal"))
        pl = value - invested
        rows.append({
            "symbol": sym.split(":", 1)[-1].removesuffix("-EQ").removesuffix("-BE"),
            "fyers_symbol": sym, "isin": h.get("isin", ""), "type": h.get("holdingType", ""),
            "qty": total, "t1_qty": t1, "avg": avg, "ltp": ltp, "invested": invested, "value": value,
            "pl": pl, "pl_pct": (pl / invested * 100) if invested else 0.0,
            "day_chg": _f(q.get("ch")) * total, "day_chg_pct": _f(q.get("chp")),
            "pledged": int(_f(h.get("collateralQuantity"))), "quoted": bool(q.get("lp")),
        })
    rows.sort(key=lambda r: -r["value"])
    total_value = sum(r["value"] for r in rows) or 1
    for r in rows:
        r["weight"] = r["value"] / total_value * 100
    return rows


def totals(rows: list[dict]) -> dict:
    inv, val = sum(r["invested"] for r in rows), sum(r["value"] for r in rows)
    day = sum(r["day_chg"] for r in rows)
    prev = val - day
    return {"count": len(rows), "invested": inv, "value": val, "pl": val - inv,
            "pl_pct": (val - inv) / inv * 100 if inv else 0.0, "day": day,
            "day_pct": day / prev * 100 if prev else 0.0}


def inr(v: float, dec: int = 2) -> str:
    """Indian digit grouping: 12,34,567.89"""
    neg, v = v < 0, abs(v)
    whole, frac = f"{v:.{dec}f}".split(".") if dec else (f"{v:.0f}", "")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(([head] if head else []) + groups + [tail])
    return ("-" if neg else "") + whole + (f".{frac}" if dec else "")


def signed(v: float, dec: int = 2, pct: bool = False) -> str:
    s = f"{v:+.2f}%" if pct else ("+" if v > 0 else "") + inr(v, dec)
    return s


# ---------------------------------------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------------------------------------
def print_table(rows: list[dict], t: dict, asof: str) -> None:
    color = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    if os.name == "nt" and color:
        os.system("")                     # enable ANSI colours in the Windows console

    def c(text, v):
        if not color or not v:
            return text
        return f"\033[{'32' if v > 0 else '31'}m{text}\033[0m"

    head = ["Symbol", "Qty", "Avg", "LTP", "Invested", "Value", "P&L", "P&L %", "Day %", "Wt %"]
    data = [[r["symbol"] + (" (T1)" if r["type"] == "T1" or r["t1_qty"] else ""), str(r["qty"]), inr(r["avg"]),
             inr(r["ltp"]), inr(r["invested"], 0), inr(r["value"], 0), signed(r["pl"], 0),
             signed(r["pl_pct"], pct=True), signed(r["day_chg_pct"], pct=True) if r["quoted"] else "-", f"{r['weight']:.1f}"]
            for r in rows]
    w = [max(len(head[i]), *(len(d[i]) for d in data)) if data else len(head[i]) for i in range(len(head))]

    def line(cells, vals=None):
        out = []
        for i, cell in enumerate(cells):
            txt = cell.ljust(w[i]) if i == 0 else cell.rjust(w[i])
            out.append(c(txt, vals[i]) if vals else txt)
        return "  ".join(out)

    print(f"\nFyers holdings  {asof}\n")
    print(line(head))
    print("  ".join("-" * x for x in w))
    for r, d in zip(rows, data):
        print(line(d, [0, 0, 0, 0, 0, 0, r["pl"], r["pl"], r["day_chg_pct"], 0]))
    print("  ".join("-" * x for x in w))
    pl_txt = "{} ({:+.2f}%)".format(signed(t["pl"], 0), t["pl_pct"])
    day_txt = "{} ({:+.2f}%)".format(signed(t["day"], 0), t["day_pct"])
    print(f"{t['count']} holdings   Invested Rs {inr(t['invested'], 0)}   Value Rs {inr(t['value'], 0)}   "
          f"P&L {c(pl_txt, t['pl'])}   Today {c(day_txt, t['day'])}\n")


def write_csv(rows: list[dict], path: Path) -> None:
    keys = ["symbol", "fyers_symbol", "isin", "type", "qty", "t1_qty", "avg", "ltp", "invested", "value", "pl",
            "pl_pct", "day_chg", "day_chg_pct", "weight", "pledged"]
    with path.open("w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        wr.writeheader()
        for r in rows:
            wr.writerow({k: (round(r[k], 2) if isinstance(r[k], float) else r[k]) for k in keys})


HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fyers Holdings</title>
<style>
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1b;--mut:#6b6b66;--line:#e4e4df;--up:#0f7b4f;--dn:#c0392b;--bar:#d9d9d2}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--fg:#ecece8;--mut:#9a9a93;--line:#2e2e2b;
--up:#3fbf86;--dn:#ef6b5b;--bar:#3a3a36}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.45 "Segoe UI",system-ui,sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px}
h1{font-size:20px;margin:0 0 2px}.sub{color:var(--mut);font-size:12px;margin-bottom:18px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:18px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tile .k{color:var(--mut);font-size:12px}.tile .v{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums}
.tile .s{font-size:12px;font-variant-numeric:tabular-nums}
.wrap{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{font-size:12px;color:var(--mut);font-weight:600;cursor:pointer;user-select:none;position:sticky;top:0;background:var(--card)}
th:first-child,td:first-child{text-align:left}tr:last-child td{border-bottom:0}
th.sorted::after{content:" \\2193"}th.sorted.asc::after{content:" \\2191"}
.up{color:var(--up)}.dn{color:var(--dn)}.tag{font-size:10px;color:var(--mut);border:1px solid var(--line);
border-radius:4px;padding:0 4px;margin-left:6px}
.wt{display:flex;align-items:center;gap:6px;justify-content:flex-end}.wt i{display:block;height:6px;
background:var(--bar);border-radius:3px}
.foot{color:var(--mut);font-size:12px;margin-top:10px}
</style></head><body><main>
<h1>Fyers holdings</h1><div class="sub">__ASOF__ &middot; __ACCOUNT__ &middot; read-only snapshot, stored on this PC only</div>
<div class="tiles">__TILES__</div>
<div class="wrap"><table id="t"><thead><tr>
<th data-k="s">Symbol</th><th data-k="n">Qty</th><th data-k="n">Avg</th><th data-k="n">LTP</th>
<th data-k="n">Invested</th><th data-k="n">Value</th><th data-k="n">P&amp;L</th><th data-k="n">P&amp;L %</th>
<th data-k="n">Day %</th><th data-k="n" class="sorted">Weight</th></tr></thead><tbody>__ROWS__</tbody></table></div>
<div class="foot">Prices from Fyers quotes at generation time. T1 = bought, not yet delivered to demat.
Click a column to sort.</div>
</main><script>
document.querySelectorAll('#t th').forEach((th,i)=>th.onclick=()=>{
 const tb=document.querySelector('#t tbody'),rows=[...tb.rows],asc=th.classList.contains('sorted')&&!th.classList.contains('asc');
 document.querySelectorAll('#t th').forEach(h=>h.classList.remove('sorted','asc'));th.classList.add('sorted');if(asc)th.classList.add('asc');
 const num=th.dataset.k==='n';rows.sort((a,b)=>{const x=a.cells[i].dataset.v??a.cells[i].innerText,y=b.cells[i].dataset.v??b.cells[i].innerText;
 const r=num?(+x)-(+y):x.localeCompare(y);return asc?r:-r});rows.forEach(r=>tb.appendChild(r))});
</script></body></html>"""


def write_html(rows: list[dict], t: dict, asof: str, account: str, path: Path) -> None:
    def cls(v):
        return "up" if v > 0 else "dn" if v < 0 else ""

    def td(text, v, color=None):
        return f'<td data-v="{v:.4f}" class="{cls(color) if color is not None else ""}">{text}</td>'

    tiles = [("Current value", f"&#8377; {inr(t['value'], 0)}", f"{t['count']} holdings", None),
             ("Invested", f"&#8377; {inr(t['invested'], 0)}", "at average cost", None),
             ("Total P&amp;L", f"&#8377; {signed(t['pl'], 0)}", f"{t['pl_pct']:+.2f}%", t["pl"]),
             ("Today", f"&#8377; {signed(t['day'], 0)}", f"{t['day_pct']:+.2f}%", t["day"])]
    tiles_html = "".join(f'<div class="tile"><div class="k">{k}</div><div class="v {cls(c) if c is not None else ""}">'
                         f'{v}</div><div class="s {cls(c) if c is not None else ""}">{s}</div></div>'
                         for k, v, s, c in tiles)
    mx = max((r["weight"] for r in rows), default=1) or 1
    body = []
    for r in rows:
        tag = '<span class="tag">T1</span>' if r["type"] == "T1" or r["t1_qty"] else ""
        tag += f'<span class="tag">{r["pledged"]} pledged</span>' if r["pledged"] else ""
        body.append(
            f'<tr><td data-v="{html.escape(r["symbol"])}" title="{html.escape(r["isin"])}">'
            f'{html.escape(r["symbol"])}{tag}</td>'
            + td(str(r["qty"]), r["qty"]) + td(inr(r["avg"]), r["avg"]) + td(inr(r["ltp"]), r["ltp"])
            + td(inr(r["invested"], 0), r["invested"]) + td(inr(r["value"], 0), r["value"])
            + td(signed(r["pl"], 0), r["pl"], r["pl"]) + td(f"{r['pl_pct']:+.2f}%", r["pl_pct"], r["pl_pct"])
            + (td(f"{r['day_chg_pct']:+.2f}%", r["day_chg_pct"], r["day_chg_pct"]) if r["quoted"]
               else '<td data-v="0">&ndash;</td>')
            + f'<td data-v="{r["weight"]:.4f}"><div class="wt">{r["weight"]:.1f}%'
              f'<i style="width:{r["weight"] / mx * 60:.0f}px"></i></div></td></tr>')
    page = (HTML.replace("__ASOF__", html.escape(asof)).replace("__ACCOUNT__", html.escape(account))
            .replace("__TILES__", tiles_html).replace("__ROWS__", "".join(body) or
                                                       '<tr><td colspan="10">No holdings</td></tr>'))
    path.write_text(page, encoding="utf-8")


# ---------------------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Show current Fyers holdings")
    p.add_argument("--open", action="store_true", help="open the HTML page in the browser")
    p.add_argument("--no-html", action="store_true", help="terminal only")
    p.add_argument("--csv", action="store_true", help="also write a CSV")
    p.add_argument("--json", action="store_true", help="print the raw Fyers holdings reply (debug)")
    a = p.parse_args(argv)
    try:
        fy = Fyers()
        raw = fy.holdings()
        if a.json:
            print(json.dumps(raw, indent=2))
            return 0
        syms = [h["symbol"] for h in raw.get("holdings", []) if h.get("symbol")]
        try:
            quotes = fy.quotes(syms) if syms else {}
        except (FyersError, requests.RequestException) as ex:
            print(f"(live quotes unavailable, using Fyers holdings LTP: {ex})", file=sys.stderr)
            quotes = {}
    except FyersTokenMissing as ex:
        print(f"Token: {ex}", file=sys.stderr)
        return 2
    except (FyersError, requests.RequestException) as ex:
        print(f"Fyers: {ex}", file=sys.stderr)
        return 1

    rows, now = build_rows(raw, quotes), datetime.now(IST)
    t = totals(rows)
    asof = now.strftime("%d %b %Y, %H:%M IST")
    print_table(rows, t, asof)

    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M")
    if a.csv:
        cp = REPORTS / f"holdings_{stamp}.csv"
        write_csv(rows, cp)
        print(f"CSV:  {cp}")
    if not a.no_html:
        hp = REPORTS / f"holdings_{stamp}.html"
        write_html(rows, t, asof, os.environ.get("FYERS_ID", ""), hp)
        print(f"HTML: {hp}")
        if a.open:
            webbrowser.open(hp.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
