"""Intraday 1-minute prefetch: today's in-progress bars as partial_<date>.parquet, read_minutes merging them,
clean_partials housekeeping, and the OMS idle gate. No network (Fyers client is mocked)."""
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402
from datetime import date
from pathlib import Path

import pandas as pd

from nsealgo.data.minute_archive import clean_partials, prefetch_today, read_minutes


def _bars(day: str, n: int = 3, base: float = 100.0) -> pd.DataFrame:
    d0 = pd.Timestamp(day) + pd.Timedelta(hours=9, minutes=15)
    rows = [{"ts": d0 + pd.Timedelta(minutes=i), "open": base + i, "high": base + i + 1,
             "low": base + i - 1, "close": base + i + 0.5, "volume": 1000 + i} for i in range(n)]
    return pd.DataFrame(rows)


def _archive(tmp_path: Path) -> Path:
    d = tmp_path / "SBIN"
    d.mkdir(parents=True)
    pd.concat([_bars("2026-10-26"), _bars("2026-10-27")]).to_parquet(
        d / "2026-08-01_2026-10-27.parquet", index=False)
    return d


def test_read_minutes_merges_span_and_partial(tmp_path):
    d = _archive(tmp_path)
    _bars("2026-10-28", n=2).to_parquet(d / "partial_2026-10-28.parquet", index=False)
    df = read_minutes(tmp_path, "SBIN", start="2026-10-26", end="2026-10-28")
    days = {pd.Timestamp(t).date().isoformat() for t in df.index}
    assert days == {"2026-10-26", "2026-10-27", "2026-10-28"}
    assert len(df) == 8


def test_read_minutes_without_partial_is_unchanged(tmp_path):
    _archive(tmp_path)
    df = read_minutes(tmp_path, "SBIN", start="2026-10-26", end="2026-10-27")
    assert len(df) == 6


def test_clean_partials_removes_partial_keeps_span(tmp_path):
    d = _archive(tmp_path)
    _bars("2026-10-28", n=1).to_parquet(d / "partial_2026-10-28.parquet", index=False)
    assert clean_partials(tmp_path) == 1
    assert not (d / "partial_2026-10-28.parquet").exists()
    assert (d / "2026-08-01_2026-10-27.parquet").exists()


def test_prefetch_today_writes_partial(tmp_path):
    class FakeClient:
        def history(self, data):
            ts = int(pd.Timestamp("2026-10-28 09:15:00", tz="Asia/Kolkata").timestamp())
            return {"s": "ok", "candles": [[ts, 100.0, 101.0, 99.0, 100.5, 1000],
                                           [ts + 60, 100.5, 102.0, 100.0, 101.5, 2000]]}

    class FakeBroker:
        _client = FakeClient()

        def throttle(self, name):
            return None

    stats = prefetch_today(FakeBroker(), {"SBIN": "NSE:SBIN-EQ"}, tmp_path, date(2026, 10, 28), threads=1)
    assert stats["done"] == 1 and stats["failed"] == 0 and stats["bars"] == 2
    f = tmp_path / "SBIN" / "partial_2026-10-28.parquet"
    assert f.exists()
    assert list(pd.read_parquet(f).columns) == ["ts", "open", "high", "low", "close", "volume"]


def test_oms_idle_no_session_is_idle(tmp_path):
    from nsealgo.cli import _oms_idle
    # no runs/live/monitor.json -> no live session -> nothing is being sent -> idle
    assert _oms_idle(tmp_path, max_wait=0) is True
