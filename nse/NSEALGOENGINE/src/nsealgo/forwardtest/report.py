"""Performance metrics, promotion gates and the HTML/Markdown/JSON report for any run directory.

Promotion gate (paper -> live at quarter size), per strategy:
  >= 100 trades, >= 20 sessions, 90% bootstrap lower bound of expectancy > 0,
  drawdown <= 1.5 x the backtest's (if a backtest summary is supplied) and no drift below the
  backtest's expectancy confidence band. A strategy whose 90% upper bound is below zero after
  30+ trades is marked for retirement.
"""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import html
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

VERDICT_TEXT = {"ready": "Ready for live trading at quarter size", "hold": "Keep paper trading",
                "retire": "Retire it", "idle": "No trades yet"}


def bootstrap_ci(x, n: int = 2000, alpha: float = 0.10, seed: int = 0) -> tuple[float, float]:
    x = np.asarray([v for v in x if v is not None and np.isfinite(v)], float)
    if len(x) < 5:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = rng.choice(x, (n, len(x))).mean(axis=1)
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def _num(x, nd=2):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), nd)


def strategy_metrics(t: pd.DataFrame, results: pd.DataFrame, capital: float, alpha: float = 0.10) -> dict:
    n = len(t)
    pnl = t["pnl_net"].astype(float) if n else pd.Series(dtype=float)
    r = t["r_multiple"].dropna().astype(float) if n else pd.Series(dtype=float)
    if results is not None and len(results):
        r = pd.concat([r, results["r"].astype(float)])
    sessions = t["exit_ts"].str[:10].nunique() if n else 0
    gains, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    cum = pnl.cumsum()
    dd = float((cum.cummax().clip(lower=0) - cum).max()) if n else 0.0
    use_r = len(r) >= max(5, n // 2)
    series = r if use_r else (pnl / capital * 100)
    lo, hi = bootstrap_ci(series, alpha=alpha)
    hold = None
    if n:
        hold = float((pd.to_datetime(t["exit_ts"], utc=True) - pd.to_datetime(t["entry_ts"], utc=True)).dt.total_seconds().mean() / 86400)
    return {"trades": int(n), "sessions": int(sessions), "win_rate": _num(float((pnl > 0).mean()) if n else None, 3),
            "net_pnl": _num(pnl.sum(), 0), "costs": _num(t["costs"].sum() if n else 0, 0),
            "profit_factor": _num(gains / losses if losses > 0 else (math.inf if gains > 0 else None)),
            "expectancy": _num(series.mean() if len(series) else None, 3), "exp_unit": "R" if use_r else "% of capital",
            "exp_ci": [_num(lo, 3), _num(hi, 3)], "max_dd_rupees": _num(dd, 0), "max_dd_pct": _num(dd / capital * 100, 2),
            "avg_hold_days": _num(hold, 2)}


def equity_metrics(eq: pd.DataFrame, capital: float) -> dict:
    if eq is None or eq.empty:
        return {"final_equity": capital, "return_pct": 0.0, "max_dd_pct": 0.0, "sharpe": None, "cagr_pct": None}
    e = eq["equity"].astype(float)
    peak = e.cummax()
    rets = e.pct_change().dropna()
    days = len(e)
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if len(rets) > 5 and rets.std() > 0 else None
    total = float(e.iloc[-1] / capital - 1)
    cagr = float((e.iloc[-1] / capital) ** (252 / max(days, 1)) - 1) if days >= 126 else None
    return {"final_equity": _num(e.iloc[-1], 0), "return_pct": _num(total * 100), "max_dd_pct": _num(((peak - e) / peak).max() * 100),
            "sharpe": _num(sharpe), "cagr_pct": _num(cagr * 100 if cagr is not None else None), "sessions": days}


def gate(m: dict, gates: dict, bench: dict | None = None) -> tuple[str, str]:
    lo, hi = m["exp_ci"]
    if m["trades"] == 0:
        return "idle", "no signals yet: check the regime, the universe files and the data feed."
    if m["trades"] >= 30 and hi is not None and hi < 0:
        return "retire", "after costs the edge is negative with 90% confidence."
    reasons = []
    if m["trades"] < gates["min_trades"]:
        reasons.append(f"{m['trades']} of {gates['min_trades']} trades")
    if m["sessions"] < gates["min_sessions"]:
        reasons.append(f"{m['sessions']} of {gates['min_sessions']} sessions")
    if lo is None or lo <= 0:
        reasons.append("the expectancy's lower confidence bound is not above zero yet")
    if bench:
        b = bench.get("strategies", {}).get(m.get("name", ""), {})
        if b.get("max_dd_pct") and m["max_dd_pct"] and m["max_dd_pct"] > gates["max_dd_multiple"] * b["max_dd_pct"]:
            reasons.append(f"drawdown {m['max_dd_pct']}% exceeds {gates['max_dd_multiple']}x the backtest's {b['max_dd_pct']}%")
        blo = (b.get("exp_ci") or [None, None])[0]
        if blo is not None and m["expectancy"] is not None and b.get("exp_unit") == m["exp_unit"] and m["expectancy"] < blo:
            reasons.append("live expectancy has drifted below the backtest's confidence band")
    if reasons:
        return "hold", "; ".join(reasons) + "."
    return "ready", "every gate passed."


def build_report(storage, cfg: dict, out_dir: str | Path, title: str, mode: str = "backtest",
                 benchmark: str | Path | None = None, names: list[str] | None = None) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    capital = float(cfg["capital"])
    gates = cfg.get("gates", {"min_trades": 100, "min_sessions": 20, "max_dd_multiple": 1.5, "ci_alpha": 0.1})
    bench = json.loads(Path(benchmark).read_text(encoding="utf-8")) if benchmark and Path(benchmark).exists() else None
    trades, eq, res = storage.trades_df(), storage.equity_df(), storage.results_df()
    ran = set(names) if names is not None else {k for k, v in (cfg.get("strategies") or {}).items()
                                                   if (v or {}).get("enabled", True)}
    names = sorted(set(trades["strategy"]) | set(res["strategy"] if len(res) else []) | ran)
    strat = {}
    for name in names:
        t = trades[trades["strategy"] == name]
        r = res[res["strategy"] == name] if len(res) else None
        m = strategy_metrics(t, r, capital, gates.get("ci_alpha", 0.1))
        m["name"] = name
        m["verdict"], m["reason"] = gate(m, gates, bench)
        strat[name] = m
    summary = {"title": title, "mode": mode, "capital": capital, "overall": equity_metrics(eq, capital),
               "trades": int(len(trades)), "strategies": strat,
               "errors": int(len(storage.events_df("ERROR"))),
               "regime_days": storage.regime_df()["state"].value_counts().to_dict() if len(storage.regime_df()) else {}}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (out / "summary.md").write_text(_markdown(summary), encoding="utf-8")
    (out / "report.html").write_text(_html(summary, trades, eq), encoding="utf-8")
    if len(trades):
        trades.to_csv(out / "trades.csv", index=False)
    return summary


def _markdown(s: dict) -> str:
    o = s["overall"]
    lines = [f"# {s['title']}", "",
             f"Final equity {o['final_equity']:,} on capital {s['capital']:,.0f} ({o['return_pct']}%), max drawdown "
             f"{o['max_dd_pct']}%, Sharpe {o['sharpe']}, trades {s['trades']}, logged errors {s['errors']}.", "",
             "| Strategy | Verdict | Trades | Win rate | Expectancy (90% CI) | Net P&L | Max DD |",
             "|---|---|---|---|---|---|---|"]
    for n, m in s["strategies"].items():
        ci = m["exp_ci"]
        lines.append(f"| {n} | {VERDICT_TEXT[m['verdict']]}: {m['reason']} | {m['trades']} | {m['win_rate']} | "
                     f"{m['expectancy']} {m['exp_unit']} ({ci[0]}, {ci[1]}) | {m['net_pnl']} | {m['max_dd_pct']}% |")
    return "\n".join(lines) + "\n"


CSS = """
:root{--ink:#1C2433;--muted:#5A6475;--rule:#DCE1E8;--panel:#F5F7FA;--bg:#FFFFFF;--gain:#0E7A4F;--loss:#B42318;
--ready:#2E3AC8;--hold:#A15C07;--retire:#B42318;--idle:#5A6475}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font-family:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;font-variant-numeric:tabular-nums;line-height:1.5}
main{max-width:980px;margin:0 auto;padding:40px 24px 64px}h1{font-size:28px;font-weight:600;margin:0 0 6px;letter-spacing:-.01em}
h2{font-size:18px;font-weight:600;margin:40px 0 12px}.lede{color:var(--muted);margin:0 0 28px;max-width:70ch}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;background:var(--rule);border:1px solid var(--rule)}
.kpi{background:var(--bg);padding:14px 16px}.kpi b{display:block;font-size:22px;font-weight:600}.kpi span{color:var(--muted);font-size:13px}
.gate{display:grid;grid-template-columns:150px 1fr 170px;gap:16px;align-items:center;padding:14px 0;border-bottom:1px solid var(--rule)}
.gate .name{font-weight:600}.gate .v{font-weight:600}.gate .why{color:var(--muted);font-size:14px}
.v.ready{color:var(--ready)}.v.hold{color:var(--hold)}.v.retire{color:var(--retire)}.v.idle{color:var(--idle)}
.meter{height:8px;background:var(--panel);border:1px solid var(--rule)}.meter i{display:block;height:100%;background:var(--ink)}
.meter-l{font-size:12px;color:var(--muted);margin-top:4px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:right;padding:8px 10px;border-bottom:1px solid var(--rule)}
th:first-child,td:first-child{text-align:left}th{font-weight:600;color:var(--muted)}.pos{color:var(--gain)}.neg{color:var(--loss)}
.note{color:var(--muted);font-size:13px;margin-top:28px;max-width:75ch}
@media (max-width:640px){.gate{grid-template-columns:1fr}}
"""


def _html(s: dict, trades: pd.DataFrame, eq: pd.DataFrame) -> str:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    o, esc = s["overall"], html.escape
    ready = [n for n, m in s["strategies"].items() if m["verdict"] == "ready"]
    active = [n for n, m in s["strategies"].items() if m["trades"] > 0]
    lede = (f"{len(ready)} of {len(active)} strategies that traded pass every promotion gate."
            if active else "No strategy traded in this run.")
    kpi = lambda v, l: f'<div class="kpi"><b>{esc(str(v))}</b><span>{esc(l)}</span></div>'
    kpis = "".join([kpi(f"{o['final_equity']:,}", "Final equity (₹)"), kpi(f"{o['return_pct']}%", "Return"),
                    kpi(f"{o['max_dd_pct']}%", "Max drawdown"), kpi(o["sharpe"], "Sharpe (daily)"),
                    kpi(s["trades"], "Round-trip trades"), kpi(s["errors"], "Logged errors")])
    rows = []
    for n, m in s["strategies"].items():
        pct = min(100, int(100 * m["trades"] / 100))
        rows.append(f'<div class="gate"><div class="name">{esc(n)}</div><div><div class="v {m["verdict"]}">'
                    f'{esc(VERDICT_TEXT[m["verdict"]])}</div><div class="why">{esc(m["reason"])}</div></div>'
                    f'<div><div class="meter"><i style="width:{pct}%"></i></div><div class="meter-l">{m["trades"]} of 100 trades'
                    f'</div></div></div>')
    charts = ""
    if eq is not None and len(eq):
        e = eq.copy()
        e["ts"] = pd.to_datetime(e["ts"])
        dd = (e["equity"] / e["equity"].cummax() - 1) * 100
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.06)
        fig.add_trace(go.Scatter(x=e["ts"], y=e["equity"], line=dict(color="#1C2433", width=2), name="Equity"), 1, 1)
        fig.add_trace(go.Scatter(x=e["ts"], y=dd, fill="tozeroy", line=dict(color="#B42318", width=1), name="Drawdown %"), 2, 1)
        fig.update_layout(height=420, margin=dict(l=40, r=10, t=10, b=30), showlegend=False, paper_bgcolor="white",
                          plot_bgcolor="white", font=dict(family="IBM Plex Sans, sans-serif", color="#1C2433"))
        fig.update_yaxes(gridcolor="#DCE1E8")
        charts += '<h2>Equity and drawdown</h2>' + fig.to_html(full_html=False, include_plotlyjs="cdn")
    if len(trades) and trades["r_multiple"].notna().any():
        fig2 = go.Figure()
        for n, g in trades.groupby("strategy"):
            r = g["r_multiple"].dropna()
            if len(r):
                fig2.add_trace(go.Box(y=r, name=n, marker_color="#2E3AC8", boxmean=True))
        fig2.update_layout(height=320, margin=dict(l=40, r=10, t=10, b=30), showlegend=False, paper_bgcolor="white",
                           plot_bgcolor="white", font=dict(family="IBM Plex Sans, sans-serif", color="#1C2433"),
                           yaxis_title="R-multiple per trade")
        fig2.update_yaxes(gridcolor="#DCE1E8", zerolinecolor="#5A6475")
        charts += '<h2>Trade outcomes in R</h2>' + fig2.to_html(full_html=False, include_plotlyjs=False)
    trs = []
    for n, m in s["strategies"].items():
        cls = "pos" if (m["net_pnl"] or 0) > 0 else "neg" if (m["net_pnl"] or 0) < 0 else ""
        ci = m["exp_ci"]
        trs.append(f"<tr><td>{esc(n)}</td><td>{m['trades']}</td><td>{m['win_rate']}</td>"
                   f"<td>{m['expectancy']} {esc(m['exp_unit'])}</td><td>{ci[0]} to {ci[1]}</td>"
                   f"<td>{m['profit_factor']}</td><td class='{cls}'>{m['net_pnl']}</td><td>{m['costs']}</td>"
                   f"<td>{m['max_dd_pct']}%</td></tr>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(s['title'])}</title><link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&display=swap" rel="stylesheet"><style>{CSS}</style></head>
<body><main><h1>{esc(s['title'])}</h1><p class="lede">{esc(lede)}</p><div class="kpis">{kpis}</div>
<h2>Promotion gates</h2>{''.join(rows)}{charts}
<h2>Strategy detail</h2><table><thead><tr><th>Strategy</th><th>Trades</th><th>Win rate</th><th>Expectancy</th><th>90% CI</th>
<th>Profit factor</th><th>Net P&amp;L (₹)</th><th>Costs (₹)</th><th>Max DD</th></tr></thead><tbody>{''.join(trs)}</tbody></table>
<p class="note">All figures are net of modelled STT, exchange, SEBI, stamp duty, GST, brokerage and DP charges plus simulated
slippage. Paper fills are deliberately conservative, but no simulation captures queue position, partial fills in thin books or
your own market impact exactly. Not investment advice.</p></main></body></html>"""
