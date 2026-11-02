"""Workbook for overnight_sizing_backtest.py (values, no formulas: the sizing is an optimisation Excel cannot redo)."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter as L

F = "Arial"
f_norm, f_bold = Font(name=F, size=10), Font(name=F, size=10, bold=True)
f_title, f_note = Font(name=F, size=13, bold=True), Font(name=F, size=9, italic=True, color="595959")
f_hdr = Font(name=F, size=10, bold=True, color="FFFFFF")
fill_hdr, fill_opt = PatternFill("solid", fgColor="1F3864"), PatternFill("solid", fgColor="E2EFDA")
RS, BPS, PCT, NUM1, DT = '#,##0;[Red]-#,##0;0', '0.0;[Red]-0.0;0.0', '0.0%;[Red]-0.0%;0.0%', '0.0', "dd-mmm-yyyy"
MAIN = ["EQ top 1", "EQ top 5", "EQ top 9", "EQ top 18", "VOL top 5", "VOL top 18", "OPT"]
LABEL = {"EQ top 1": "Equal split, top 1", "EQ top 5": "Equal split, top 5", "EQ top 9": "Equal split, top 9",
         "EQ top 18": "Equal split, top 18", "VOL top 5": "Equal but volume-capped, top 5",
         "VOL top 18": "Equal but volume-capped, top 18", "OPT top 5": "Optimized split, top 5",
         "OPT top 9": "Optimized split, top 9", "OPT top 18": "Optimized split, top 18",
         "OPT": "Model picks from top 40"}


def _lab(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns=LABEL)


def _hdr(ws, row, col, values, width=None):
    for j, v in enumerate(values):
        c = ws.cell(row, col + j, v)
        c.font, c.fill = f_hdr, fill_hdr
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        if width:
            ws.column_dimensions[L(col + j)].width = width


def _table(ws, row, col, df: pd.DataFrame, fmts: dict, width=12, hilite=None):
    _hdr(ws, row, col, list(df.columns), width)
    for i, r in enumerate(df.itertuples(index=False), start=row + 1):
        for j, v in enumerate(r):
            if isinstance(v, (np.floating, float)) and not np.isfinite(v):
                v = None
            elif isinstance(v, np.generic):
                v = v.item()
            elif isinstance(v, pd.Timestamp):
                v = v.to_pydatetime()
            c = ws.cell(i, col + j, v)
            c.font = f_norm
            fm = fmts.get(df.columns[j])
            if fm:
                c.number_format = fm
            if hilite and df.columns[j] == hilite:
                c.fill = fill_opt
    return row + len(df) + 1


def write(path: Path, summ: pd.DataFrame, nightly: pd.DataFrame, positions: pd.DataFrame, c: pd.DataFrame,
          table: pd.DataFrame, edges: pd.DataFrame, base, text: str) -> None:
    wb = Workbook()
    ev = summ[summ["scenario"] == "every night"]
    sk = summ[summ["scenario"] == "skip Friday"]
    nights = int(ev["nights"].max())
    first, last = pd.Timestamp(nightly["buy_day"].min()), pd.Timestamp(nightly["buy_day"].max())

    # ---- Split top N (what the question was) ----
    ws = wb.active
    ws.title = "Split top N"
    ws["A1"] = "Splitting the money among the top N stocks: equal split vs optimized split (same stocks)"
    ws["A1"].font = f_title
    intro = [f"{nights} nights, bought {first:%d-%b-%Y} .. {last:%d-%b-%Y}. Every number is an average per night in rupees, "
             "after all Shoonya charges.",
             "Equal split = the same rupees in each of the top N (e.g. Rs 1 lakh x 5). Optimized split = the rupees per stock "
             "set from its price, traded volume, volatility and expected move (nsealgo size-overnight --top N).",
             "Not filled = rupees ordered in the 15:40-16:00 post-close session that found no seller. Slippage = rupees lost "
             "because your own sale pushed the next opening price down."]
    for i, n in enumerate(intro):
        ws.cell(2 + i, 1, n).font = f_note
    row = 6
    for n_ in (5, 9, 18):
        ws.cell(row, 1, f"Top {n_}").font = f_bold
        e = ev[ev["strategy"] == f"EQ top {n_}"].set_index("capital_lakh")
        o = ev[ev["strategy"] == f"OPT top {n_}"].set_index("capital_lakh")
        t = pd.DataFrame({"Capital (Rs)": e.index * 100_000,
                          "Equal split: profit per night": e["rs_per_night"].to_numpy(),
                          "Equal split: not filled per night": (e["avg_ordered_rs"] - e["avg_filled_rs"]).to_numpy(),
                          "Equal split: slippage per night": e["impact_rs_per_night"].to_numpy(),
                          "Optimized split: profit per night": o["rs_per_night"].to_numpy(),
                          "Optimized split: money invested per night": o["avg_filled_rs"].to_numpy(),
                          "Optimized split: slippage per night": o["impact_rs_per_night"].to_numpy(),
                          "Extra profit per night from optimizing": (o["rs_per_night"] - e["rs_per_night"]).to_numpy()})
        row = _table(ws, row + 1, 1, t, {cname: RS for cname in t.columns}, width=17,
                     hilite="Optimized split: profit per night") + 1
    ws.column_dimensions["A"].width = 14
    ws.row_dimensions[7].height = 45

    # ---- Top 5 at Rs 5 lakh, night by night ----
    p5 = positions[(positions["capital_lakh"] == 5) & positions["strategy"].isin(["EQ top 5", "OPT top 5"])].copy()
    if len(p5):
        ws5 = wb.create_sheet("Top 5 at Rs 5 lakh")
        ws5["A1"] = "Rs 5 lakh in the top 5, every night: equal split vs optimized split, stock by stock (actual results)"
        ws5["A1"].font = f_title
        ws5["A2"] = ("ordered = rupees asked for at 15:40; bought = what the post-close session filled; slippage = loss from "
                     "your own sale pushing the open down; profit = after all charges. Filter by date.")
        ws5["A2"].font = f_note
        p5["slip_rs"] = p5["filled_rs"] * (1 + p5["gross_bps"] / 1e4) * p5["impact_bps"] / 1e4
        k = ["buy_day", "sell_day", "rank", "company_id", "gross_bps"]
        e5 = p5[p5["strategy"] == "EQ top 5"].set_index(k)[["ordered_rs", "filled_rs", "slip_rs", "pnl_rs"]]
        o5 = p5[p5["strategy"] == "OPT top 5"].set_index(k)[["ordered_rs", "filled_rs", "slip_rs", "pnl_rs"]]
        j = e5.join(o5, how="outer", lsuffix="_eq", rsuffix="_opt").fillna(0.0).reset_index().sort_values(["buy_day", "rank"])
        j = j[["buy_day", "sell_day", "rank", "company_id", "gross_bps", "ordered_rs_eq", "filled_rs_eq", "slip_rs_eq",
               "pnl_rs_eq", "ordered_rs_opt", "filled_rs_opt", "slip_rs_opt", "pnl_rs_opt"]]
        j["gross_bps"] = j["gross_bps"] / 1e4
        j.columns = ["buy date", "sell date", "rank", "stock", "overnight move", "equal: ordered", "equal: bought",
                     "equal: slippage", "equal: profit", "optimized: ordered", "optimized: bought", "optimized: slippage",
                     "optimized: profit"]
        _table(ws5, 4, 1, j, {"buy date": DT, "sell date": DT, "overnight move": PCT,
                              **{cname: RS for cname in j.columns if ":" in cname}}, width=13)
        ws5.freeze_panes = "E5"
        ws5.auto_filter.ref = f"A4:{L(j.shape[1])}{4 + len(j)}"

    # ---- Summary ----
    ws = wb.create_sheet("All strategies")
    ws["A1"].font = f_title
    ws["A1"] = "All ways of sizing compared"
    notes = [f"{nights} nights, bought {first:%d-%b-%Y} .. {last:%d-%b-%Y}. Buy in the 15:40-16:00 post-close session at "
             "the close, sell in the next pre-open auction. Rs per night after all Shoonya charges, the post-close fill "
             "limit and the push of our own sale on the opening price.",
             "Equal split = equal rupees in the top N (blind to volume). Equal but volume-capped = equal rupees, none "
             "above the volume limits. Model picks from top 40 = the model chooses the stocks AND the amounts.",
             "Read across a row: which way of sizing earns most at that capital. Read down OPT: how its profit grows "
             "with capital while the others turn negative (their orders outgrow the two thin sessions)."]
    for i, n in enumerate(notes):
        ws.cell(2 + i, 1, n).font = f_note
    row = 6
    ws.cell(row, 1, "Rs per night - every night").font = f_bold
    piv = _lab(ev.pivot(index="capital_lakh", columns="strategy", values="rs_per_night")[MAIN].reset_index())
    piv.insert(0, "capital (Rs)", piv.pop("capital_lakh") * 100_000)
    end = _table(ws, row + 1, 1, piv, {cname: RS for cname in piv.columns}, width=15, hilite=LABEL["OPT"])
    chart = LineChart()
    chart.title, chart.y_axis.title, chart.x_axis.title = "Rs per night vs capital", "Rs per night", "capital"
    chart.height, chart.width = 9, 18
    data = Reference(ws, min_col=2, max_col=1 + len(MAIN), min_row=row + 1, max_row=end - 1)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=row + 2, max_row=end - 1))
    ws.add_chart(chart, f"{L(len(MAIN) + 3)}{row}")
    row = end + 1
    ws.cell(row, 1, "% a year on the capital - every night").font = f_bold
    ret = ev.pivot(index="capital_lakh", columns="strategy", values="return_pct_pa")[MAIN].reset_index()
    ret.insert(0, "capital (Rs)", ret.pop("capital_lakh") * 100_000)
    ret[MAIN] = ret[MAIN] / 100
    ret = _lab(ret)
    row = _table(ws, row + 1, 1, ret, {cname: (RS if cname.startswith("capital") else PCT) for cname in ret.columns},
                 hilite=LABEL["OPT"]) + 1
    ws.cell(row, 1, "Skip Friday (no Friday-close -> Monday-open trade): Rs per night over all nights").font = f_bold
    pk = _lab(sk.pivot(index="capital_lakh", columns="strategy", values="rs_per_night")[MAIN].reset_index())
    pk.insert(0, "capital (Rs)", pk.pop("capital_lakh") * 100_000)
    row = _table(ws, row + 1, 1, pk, {cname: RS for cname in pk.columns}, hilite=LABEL["OPT"]) + 1
    ws.cell(row, 1, "Worst night (Rs) - every night  /  skip Friday").font = f_bold
    w1 = ev.pivot(index="capital_lakh", columns="strategy", values="worst_night_rs")[MAIN]
    w2 = sk.pivot(index="capital_lakh", columns="strategy", values="worst_night_rs")[MAIN]
    w = pd.concat([_lab(w1), _lab(w2).add_suffix(" (skip Fri)")], axis=1).reset_index()
    w.insert(0, "capital (Rs)", w.pop("capital_lakh") * 100_000)
    row = _table(ws, row + 1, 1, w, {cname: RS for cname in w.columns})
    ws.column_dimensions["A"].width = 14
    ws.freeze_panes = "B8"

    # ---- OPT detail ----
    ws = wb.create_sheet("OPT detail")
    ws["A1"] = "Model picks from the top 40 (stocks and amounts), per capital level"
    ws["A1"].font = f_title
    ws["A2"] = ("stocks = average number bought a night; deployed = rupees actually bought / capital; filled = bought / "
                "ordered; impact = our sale's push on the open, bps of the sale; net bps = profit after everything per "
                "rupee bought; t = average nightly profit / its standard error.")
    ws["A2"].font = f_note
    row = 4
    for scen, d in (("every night", ev), ("skip Friday", sk)):
        ws.cell(row, 1, scen).font = f_bold
        o = d[d["strategy"] == "OPT"][["capital_lakh", "rs_per_night", "return_pct_pa", "avg_stocks", "deployed_pct",
                                       "fill_pct", "impact_bps", "net_bps_on_filled", "worst_night_rs", "best_night_rs",
                                       "pct_nights_up", "t"]].copy()
        o["capital_lakh"] = o["capital_lakh"] * 100_000
        for col in ("return_pct_pa", "deployed_pct", "fill_pct", "pct_nights_up"):
            o[col] = o[col] / 100
        o.columns = ["capital (Rs)", "Rs per night", "% a year", "stocks", "deployed", "filled", "impact bps",
                     "net bps", "worst night", "best night", "nights up", "t"]
        row = _table(ws, row + 1, 1, o, {"capital (Rs)": RS, "Rs per night": RS, "% a year": PCT, "stocks": NUM1,
                                          "deployed": PCT, "filled": PCT, "impact bps": BPS, "net bps": BPS,
                                          "worst night": RS, "best night": RS, "nights up": PCT, "t": NUM1}, width=12) + 1
    ws.cell(row, 1, "Equal amounts (EQ) - how much of the order really filled in the post-close session").font = f_bold
    fp = ev[ev["strategy"].str.startswith("EQ")].pivot(index="capital_lakh", columns="strategy", values="fill_pct")
    fp = (fp[[m for m in MAIN if m.startswith("EQ")]] / 100).reset_index()
    fp.insert(0, "capital (Rs)", fp.pop("capital_lakh") * 100_000)
    _table(ws, row + 1, 1, fp, {cname: (RS if cname.startswith("capital") else PCT) for cname in fp.columns})

    # ---- Robustness ----
    ws = wb.create_sheet("Robustness")
    ws["A1"] = "Model picks from the top 40, when the market is less kind than assumed - Rs per night"
    ws["A1"].font = f_title
    ws["A2"] = ("auction impact x2: our sale moves the open twice as much as planned.  half the post-close fills: other "
                "buyers ahead of us in the queue.  caps 25% / 100%: plan on a quarter / all of the stock's usual session.")
    ws["A2"].font = f_note
    var = [m for m in ev["strategy"].unique() if m.startswith("OPT")]
    rb = ev.pivot(index="capital_lakh", columns="strategy", values="rs_per_night")[var].reset_index()
    rb.insert(0, "capital (Rs)", rb.pop("capital_lakh") * 100_000)
    _table(ws, 4, 1, rb, {cname: RS for cname in rb.columns}, width=16, hilite="OPT")

    # ---- By year ----
    ws = wb.create_sheet("By year")
    ws["A1"] = "Rs per night by year (every night)"
    ws["A1"].font = f_title
    ny = nightly.assign(year=pd.to_datetime(nightly["buy_day"]).dt.year)
    row = 3
    for cap_l in (1, 5, 20, 100):
        ws.cell(row, 1, f"capital Rs {cap_l} lakh").font = f_bold
        y = ny[ny["capital_lakh"] == cap_l].groupby(["year", "strategy"])["pnl_rs"].mean().unstack()[MAIN].reset_index()
        y["nights"] = ny[(ny["capital_lakh"] == cap_l) & (ny["strategy"] == "OPT")].groupby("year").size().to_numpy()
        row = _table(ws, row + 1, 1, y, {m: RS for m in MAIN}, width=12, hilite="OPT") + 1

    # ---- Nightly ----
    ws = wb.create_sheet("Nightly")
    ws["A1"] = "Profit per night (Rs) after everything, for three capital levels"
    ws["A1"].font = f_title
    caps = [5, 20, 100]
    wide = None
    for cap_l in caps:
        p = nightly[nightly["capital_lakh"] == cap_l].pivot(index="buy_day", columns="strategy", values="pnl_rs")[MAIN]
        p.columns = [f"{m} @ {cap_l}L" for m in MAIN]
        wide = p if wide is None else wide.join(p)
    wide = wide.reset_index().rename(columns={"buy_day": "buy date (close)"})
    _table(ws, 3, 1, wide, {**{cname: RS for cname in wide.columns}, "buy date (close)": DT}, width=11)
    ws.freeze_panes = "B4"
    ws.auto_filter.ref = f"A3:{L(wide.shape[1])}{3 + len(wide)}"

    # ---- OPT positions ----
    ws = wb.create_sheet("OPT positions")
    ws["A1"] = "Every OPT position at Rs 5 lakh and Rs 20 lakh (filter the capital column)"
    ws["A1"].font = f_title
    ps = positions[(positions["strategy"] == "OPT") & positions["capital_lakh"].isin([5, 20])].copy()
    ps["capital_lakh"] = ps["capital_lakh"] * 100_000
    ps = ps[["buy_day", "sell_day", "capital_lakh", "rank", "company_id", "pred_bps", "edge_bps", "cap_rs", "cap_by",
             "ordered_rs", "filled_rs", "impact_bps", "gross_bps", "pnl_rs", "postclose_volume_from", "auction_volume_from"]]
    ps.columns = ["buy date", "sell date", "capital", "rank", "stock", "predicted gap bps", "expected move bps",
                  "cap Rs", "cap set by", "ordered Rs", "filled Rs", "impact bps", "actual move bps", "profit Rs",
                  "post-close volume from", "auction volume from"]
    _table(ws, 3, 1, ps, {"buy date": DT, "sell date": DT, "capital": RS, "predicted gap bps": BPS,
                          "expected move bps": BPS, "cap Rs": RS, "ordered Rs": RS, "filled Rs": RS, "impact bps": BPS,
                          "actual move bps": BPS, "profit Rs": RS}, width=12)
    ws.freeze_panes = "F4"
    ws.auto_filter.ref = f"A3:{L(ps.shape[1])}{3 + len(ps)}"

    # ---- Liquidity ----
    ws = wb.create_sheet("Liquidity")
    ws["A1"] = "How much the two sessions usually take, per stock (stocks that were candidates)"
    ws["A1"].font = f_title
    ws["A2"] = ("post-close / auction share = the session's volume as a share of the stock's daily volume (median of its "
                "observations, shrunk toward the market median when few). Rs = share x the stock's median daily turnover "
                "on its candidate nights.")
    ws["A2"].font = f_note
    lq = c.groupby("company_id").agg(nights=("buy_day", "nunique"), pc_rs=("pc_rs", "median"), pre_rs=("pre_rs", "median"),
                                     tv20=("tv20", "median")).reset_index()
    lq = lq.merge(table[["company_id", "pc_share", "n_pc", "pre_share", "n_pre"]], on="company_id", how="left")
    lq = lq.sort_values("nights", ascending=False)
    lq = lq[["company_id", "nights", "tv20", "pc_share", "n_pc", "pc_rs", "pre_share", "n_pre", "pre_rs"]]
    lq.columns = ["stock", "candidate nights", "daily turnover Rs", "post-close share", "post-close obs",
                  "usual post-close Rs", "auction share", "auction obs", "usual auction Rs"]
    _table(ws, 4, 1, lq, {"daily turnover Rs": RS, "post-close share": '0.000%', "usual post-close Rs": RS,
                          "auction share": '0.000%', "usual auction Rs": RS}, width=14)
    ws.freeze_panes = "B5"
    ws.auto_filter.ref = f"A4:{L(lq.shape[1])}{4 + len(lq)}"

    # ---- Edge model ----
    ws = wb.create_sheet("Edge model")
    ws["A1"] = "Expected overnight move before costs = a + b x predicted gap (bps), refitted every night on earlier nights"
    ws["A1"].font = f_title
    e = edges.rename(columns={"buy_day": "night", "nights_used": "nights used"})
    _table(ws, 3, 1, e, {"night": DT, "a": BPS, "b": '0.00'}, width=12)

    # ---- Method ----
    ws = wb.create_sheet("Method")
    ws.column_dimensions["A"].width = 150
    for i, line in enumerate(text.split("\n"), start=1):
        ws.cell(i, 1, line).font = Font(name="Consolas", size=9)
    wb.save(path)
