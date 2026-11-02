"""Risk manager: Bayesian fractional-Kelly sizing, hard caps, pre-trade checks and circuit breakers."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import OrderRequest, OrderType, Product, Side

log = logging.getLogger("nsealgo.risk")


@dataclass
class StratStats:
    trades: int = 0
    wins: int = 0
    sum_win_r: float = 0.0
    sum_loss_r: float = 0.0

    def add(self, r: float) -> None:
        if r is None or not math.isfinite(r):
            return
        self.trades += 1
        if r > 0:
            self.wins += 1
            self.sum_win_r += r
        else:
            self.sum_loss_r += -r


class RiskManager:
    def __init__(self, cfg: dict, portfolio, storage, priors: dict | None = None, horizons: dict | None = None,
                 kill_file: str | Path | None = None, sector_of=None, sizing: str = "dynamic", learn: bool = True):
        """sizing='dynamic' sizes off live equity (compounding, as in live trading); 'constant' sizes off the
        starting capital. learn=False freezes the Kelly posterior at its priors. Research backtests use
        constant + frozen so every period can be simulated independently (and in parallel)."""
        self.sizing, self.learn = sizing, learn
        self.cfg = cfg
        self.portfolio = portfolio
        self.storage = storage
        self.priors = priors or {}
        self.horizons = horizons or {}
        self.kill_file = Path(kill_file) if kill_file else None
        self.sector_of = sector_of or (lambda key: None)
        self.stats: dict[str, StratStats] = {}
        self.halted = False
        self.halt_reason = ""
        self.intraday_halted = False
        self.intraday_reason = ""
        self.orders_today = 0
        self.consecutive_losses = 0
        self.day_intraday_pnl = 0.0
        self.swing_halted = False
        self.swing_reason = ""
        self.day_swing_pnl = 0.0
        self.swing_unreal_open = 0.0
        self.equity_open = self.base_equity()
        self.intraday_open = self.book_equity("intraday")
        self.swing_open = self.book_equity("swing")
        if learn:
            self._load_stats()

    # ---- statistics & sizing ---------------------------------------------------------------
    def _load_stats(self) -> None:
        try:
            t = self.storage.trades_df()
            for s, r in zip(t.get("strategy", []), t.get("r_multiple", [])):
                self.stats.setdefault(s, StratStats()).add(r if r is not None else math.nan)
            res = self.storage.results_df()
            for s, r in zip(res.get("strategy", []), res.get("r", [])):
                self.stats.setdefault(s, StratStats()).add(r)
        except Exception as ex:
            log.debug("stats load: %s", ex)

    def stat(self, strategy: str) -> StratStats:
        return self.stats.setdefault(strategy, StratStats())

    def base_equity(self) -> float:
        return self.portfolio.capital if self.sizing == "constant" else self.portfolio.equity

    def book_frac(self, book: str) -> float:
        """Share of the capital the swing / intraday book may use (risk.books); both default to half."""
        b = self.cfg.get("books") or {}
        return float(b.get(book, 0.5))

    def book_equity(self, book: str) -> float:
        return max(self.base_equity(), 0.0) * self.book_frac(book)

    def book_of(self, strategy: str) -> str:
        return "intraday" if self.horizons.get(strategy) == "intraday" else "swing"

    def _kill(self, book: str) -> bool:
        """Kill-switch files next to KILL: KILL (everything), KILL_INTRADAY, KILL_SWING."""
        if not self.kill_file:
            return False
        return (self.kill_file.parent / f"{self.kill_file.name}_{book.upper()}").exists()

    def kelly(self, strategy: str) -> tuple[float, dict]:
        """Posterior-shrunk Kelly f* = p - (1-p)/R. Returns (risk fraction of equity, detail)."""
        p0, r0 = self.priors.get(strategy, (0.45, 1.5))
        n0 = float(self.cfg.get("prior_weight", 20))
        s = self.stat(strategy)
        losses = s.trades - s.wins
        p = (s.wins + p0 * n0) / (s.trades + n0)
        avg_win = (s.sum_win_r + r0 * n0 * p0) / (s.wins + n0 * p0)
        avg_loss = (s.sum_loss_r + 1.0 * n0 * (1 - p0)) / (losses + n0 * (1 - p0))
        R = avg_win / max(avg_loss, 1e-9)
        f = p - (1 - p) / R
        cap = self.cfg["intraday_cap"] if self.horizons.get(strategy) == "intraday" else self.cfg["swing_cap"]
        detail = {"p": round(p, 3), "R": round(R, 3), "f_star": round(f, 4), "trades": s.trades}
        if f <= 0 and s.trades >= int(self.cfg.get("min_trades_to_disable", 30)):
            detail["disabled"] = True
            return 0.0, detail
        return max(0.0, min(cap, self.cfg.get("kelly_fraction", 0.25) * f)) if f > 0 else cap * 0.25, detail

    def risk_amount(self, strategy: str, mult: float = 1.0) -> float:
        frac, _ = self.kelly(strategy)
        return self.book_equity(self.book_of(strategy)) * frac * mult

    def size(self, *, strategy: str, horizon: str, entry: float, stop: float, lot: int = 1, mult: float = 1.0,
             adv_value: float | None = None, gap_q: float | None = None, avail_cash: float | None = None,
             touch_qty: float | None = None) -> int:
        """Shares from the stop: (book capital x risk fraction) / (entry - stop), capped by order value, ADV, the
        book's position limit, the gap risk, the cash / margin available (avail_cash) and, for intraday, a share
        of the quantity resting at the best price (touch_qty x risk.depth_share)."""
        if not entry or entry <= 0 or mult <= 0:
            return 0
        risk_amt = self.risk_amount(strategy, mult)
        per = max(abs(entry - stop), entry * float(self.cfg.get("min_risk_pct", 0.001)))
        qty = math.floor(risk_amt / per)
        eq = self.book_equity("intraday" if horizon == "intraday" else "swing")
        # marketable limits sit up to limit_band_pct beyond the reference price: size against that price
        caps = [math.floor(self.cfg["max_order_value"] / (entry * (1 + float(self.cfg.get("limit_band_pct", 0.01)))))]
        if adv_value and adv_value > 0:
            caps.append(math.floor(self.cfg["adv_cap"] * adv_value / entry))
        if horizon == "swing":
            caps.append(math.floor(self.cfg["max_position_frac"] * eq / entry))
            if gap_q and gap_q > 0:   # worst-1% gap must cost <= gap_multiple x planned risk
                caps.append(math.floor(self.cfg["gap_multiple"] * risk_amt / (entry * gap_q)))
        if avail_cash is not None and avail_cash >= 0:
            per_share = entry * (float(self.cfg.get("mis_margin_frac", 0.2)) if horizon == "intraday" else 1.0)
            caps.append(math.floor(avail_cash / max(per_share, 1e-9)))
        if horizon == "intraday" and touch_qty and touch_qty > 0:
            caps.append(math.floor(float(self.cfg.get("depth_share", 0.25)) * touch_qty))
        qty = max(0, min([qty] + caps))
        lot = max(int(lot or 1), 1)
        return (qty // lot) * lot

    # ---- pre-trade checks ------------------------------------------------------------------
    def check(self, req: OrderRequest, now: datetime, ltp: float | None) -> tuple[bool, str]:
        if self.kill_file and self.kill_file.exists():
            self.halt("kill switch file present")
        if req.product == Product.CNC and req.side == Side.SELL:
            pos = self.portfolio.get(req.strategy, req.key)
            held = pos.qty if pos is not None and pos.qty > 0 else 0
            if req.qty > held:   # a delivery sell beyond holdings is a naked short: short delivery / auction risk
                return False, (f"cash short blocked: CNC sell {req.qty} > held {held}; short selling is intraday-only "
                               f"(MIS)")
        if req.intent != "entry":
            return True, ""
        if self.halted:
            return False, f"halted: {self.halt_reason}"
        if req.product == Product.MIS and self._kill("intraday"):
            return False, "intraday kill switch (KILL_INTRADAY)"
        if req.product == Product.CNC and self._kill("swing"):
            return False, "swing kill switch (KILL_SWING)"
        if req.product == Product.MIS and self.intraday_halted:
            return False, f"intraday halted: {self.intraday_reason}"
        if req.product == Product.CNC and self.swing_halted:
            return False, f"swing entries halted: {self.swing_reason}"
        if self.orders_today >= int(self.cfg.get("max_orders_per_day", 300)):
            return False, "daily order cap reached"
        px = req.price or req.trigger or ltp or 0.0
        value = req.qty * px
        if value > self.cfg["max_order_value"]:
            return False, f"order value {value:,.0f} above cap"
        eq = self.base_equity()
        if req.product == Product.MIS:
            gross = self.portfolio.gross_exposure("MIS") + value
            if gross > self.cfg["intraday_gross_leverage"] * self.book_equity("intraday"):
                return False, "intraday gross exposure cap (intraday book)"
        if req.product == Product.CNC:
            swing = self.portfolio.positions(product="CNC")
            if len(swing) >= int(self.cfg["max_swing_positions"]):
                return False, "max swing positions"
            if self.portfolio.gross_exposure("CNC") + value > self.book_equity("swing") * 1.0001:
                return False, "swing book budget used up"
            sec = self.sector_of(req.key)
            if sec and sum(1 for p in swing if self.sector_of(p.key) == sec) >= int(self.cfg["max_per_sector"]):
                return False, f"sector cap ({sec})"
        if (req.order_type == OrderType.LIMIT and ltp and req.price and not req.at_close
                and abs(req.price / ltp - 1) > self.cfg["limit_band_pct"]):
            return False, "limit price outside protection band"
        return True, ""

    def on_order_submitted(self, req: OrderRequest) -> None:
        self.orders_today += 1

    # ---- circuit breakers ------------------------------------------------------------------
    def halt(self, reason: str) -> None:
        if not self.halted:
            log.error("TRADING HALTED: %s", reason)
        self.halted, self.halt_reason = True, reason

    def resume(self) -> None:
        self.halted, self.halt_reason = False, ""

    def day_start(self) -> None:
        self.orders_today = 0
        self.consecutive_losses = 0
        self.day_intraday_pnl = 0.0
        self.intraday_halted, self.intraday_reason = False, ""
        self.swing_halted, self.swing_reason = False, ""
        self.day_swing_pnl = 0.0
        self.swing_unreal_open = self.portfolio.unrealized("CNC")
        self.equity_open = self.base_equity()
        self.intraday_open = self.book_equity("intraday")
        self.swing_open = self.book_equity("swing")

    def on_trade(self, trade) -> None:
        if self.learn:
            self.stat(trade.strategy).add(trade.r_multiple)
        if trade.product == "MIS":
            self.day_intraday_pnl += trade.pnl_net
            self.consecutive_losses = self.consecutive_losses + 1 if trade.pnl_net < 0 else 0
            if self.consecutive_losses >= int(self.cfg.get("max_consecutive_losses", 3)):
                self.intraday_halted, self.intraday_reason = True, f"{self.consecutive_losses} losses in a row"
        elif trade.product == "CNC":
            self.day_swing_pnl += trade.pnl_net

    def record_result(self, strategy: str, r: float, pnl: float, ts: datetime, entry: str | None = None) -> None:
        if self.learn:
            self.stat(strategy).add(r)
        self.storage.add_result(strategy, ts, r, pnl, entry)

    def monitor(self) -> str | None:
        """Per book. Intraday: daily loss limit (risk.daily_loss_limit x the intraday book at the open) or
        KILL_INTRADAY -> no new intraday entries + flatten intraday. Swing: risk.swing_daily_loss_limit x the
        swing book, or KILL_SWING -> no new swing entries (open swing positions keep their stops). KILL -> all."""
        if self.kill_file and self.kill_file.exists() and not self.halted:
            self.halt("kill switch file present")
            return "flatten_all"
        if self._kill("intraday") and not self.intraday_halted:
            self.intraday_halted, self.intraday_reason = True, "intraday kill switch (KILL_INTRADAY)"
            return "flatten_intraday"
        if not self.swing_halted:
            sw = self.day_swing_pnl + self.portfolio.unrealized("CNC") - self.swing_unreal_open
            lim = float(self.cfg.get("swing_daily_loss_limit", 0.02))
            if lim > 0 and sw <= -lim * max(self.swing_open, 1.0):
                self.swing_halted, self.swing_reason = True, f"swing daily loss limit ({sw:,.0f})"
                log.error("SWING ENTRIES HALTED for today: %s", self.swing_reason)
        live = self.day_intraday_pnl + self.portfolio.unrealized("MIS")
        if not self.intraday_halted and live <= -self.cfg["daily_loss_limit"] * max(self.intraday_open, 1.0):
            self.intraday_halted, self.intraday_reason = True, f"daily loss limit ({live:,.0f})"
            return "flatten_intraday"
        return None
