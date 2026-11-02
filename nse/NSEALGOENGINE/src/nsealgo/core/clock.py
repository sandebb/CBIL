"""Clocks (real and simulated) and the NSE trading calendar, all in IST."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import csv
import time as _t
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_IST_OFFSET_S = 19800        # +05:30, no daylight saving since 1945


def localize_ist(values):
    """Naive IST timestamps -> tz-aware IST DatetimeIndex. Identical to tz_localize(IST) (IST has a fixed
    offset) but ~4x faster: shifting to UTC and converting avoids per-value zoneinfo lookups."""
    import pandas as pd
    idx = pd.DatetimeIndex(values)
    return (idx - pd.Timedelta(seconds=_IST_OFFSET_S)).tz_localize("UTC").tz_convert(IST)

# Fallback when config/nse_holidays.csv is missing. Update yearly from the NSE holiday circular.
DEFAULT_HOLIDAYS = {date.fromisoformat(d) for d in (
    "2025-02-26", "2025-03-14", "2025-03-31", "2025-04-10", "2025-04-14", "2025-04-18", "2025-05-01",
    "2025-08-15", "2025-08-27", "2025-10-02", "2025-10-21", "2025-10-22", "2025-11-05", "2025-12-25",
    "2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03", "2026-04-14",
    "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10",
    "2026-11-24", "2026-12-25")}


def hm(s: str | time) -> time:
    if isinstance(s, time):
        return s
    h, m = str(s).split(":")[:2]
    return time(int(h), int(m))


def at(d: date, t: str | time) -> datetime:
    return datetime.combine(d, hm(t), tzinfo=IST)


class RealClock:
    is_sim = False

    def now(self) -> datetime:
        return datetime.now(IST)

    def sleep(self, seconds: float) -> None:
        _t.sleep(max(0.0, seconds))


class SimClock:
    is_sim = True

    def __init__(self, start: datetime | None = None):
        self._now = start or datetime.now(IST)

    def now(self) -> datetime:
        return self._now

    def set(self, ts: datetime) -> None:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        if ts > self._now:
            self._now = ts

    def reset(self, ts: datetime) -> None:
        self._now = ts if ts.tzinfo else ts.replace(tzinfo=IST)

    def sleep(self, seconds: float) -> None:
        self._now += timedelta(seconds=max(0.0, seconds))


class NSECalendar:
    """Weekends + exchange holidays (config/nse_holidays.csv, column `date`, ISO format)."""

    def __init__(self, holidays_file: str | Path | None = None, holidays: set[date] | None = None):
        self.holidays: set[date] = set(holidays or ())
        if not holidays and not (holidays_file and Path(holidays_file).exists()):
            self.holidays |= DEFAULT_HOLIDAYS
        if holidays_file and Path(holidays_file).exists():
            with open(holidays_file, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    try:
                        self.holidays.add(date.fromisoformat(row["date"].strip()))
                    except (KeyError, ValueError, AttributeError):
                        continue

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def next_trading_day(self, d: date) -> date:
        d += timedelta(days=1)
        while not self.is_trading_day(d):
            d += timedelta(days=1)
        return d

    def prev_trading_day(self, d: date) -> date:
        d -= timedelta(days=1)
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d

    def trading_days(self, start: date, end: date) -> list[date]:
        out, d = [], start
        while d <= end:
            if self.is_trading_day(d):
                out.append(d)
            d += timedelta(days=1)
        return out

    def sessions_between(self, a: date, b: date) -> int:
        """Trading sessions in (a, b]; negative if b < a."""
        if b >= a:
            return len(self.trading_days(a + timedelta(days=1), b))
        return -len(self.trading_days(b + timedelta(days=1), a))

    def add_sessions(self, d: date, n: int) -> date:
        step = self.next_trading_day if n >= 0 else self.prev_trading_day
        for _ in range(abs(n)):
            d = step(d)
        return d

    def is_month_end(self, d: date) -> bool:
        return self.is_trading_day(d) and self.next_trading_day(d).month != d.month
