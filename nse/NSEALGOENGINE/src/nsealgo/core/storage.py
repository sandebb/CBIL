"""SQLite persistence: orders, fills, trades, equity, events, regime log and strategy state."""
from __future__ import annotations
import pathlib as _pl, sys as _sys; _c = str(next((p for p in _pl.Path(__file__).resolve().parents if p.name == "code"), _pl.Path(__file__).resolve().parent)); _sys.path.insert(0, _c) if _c not in _sys.path else None; import runtime_deps  # noqa: E402

import json
import math
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders(client_id TEXT PRIMARY KEY, broker_id TEXT, strategy TEXT, key TEXT, side TEXT,
  qty INTEGER, order_type TEXT, product TEXT, price REAL, trigger REAL, status TEXT, filled_qty INTEGER, avg_price REAL,
  intent TEXT, message TEXT, created TEXT, updated TEXT, meta TEXT);
CREATE TABLE IF NOT EXISTS fills(id INTEGER PRIMARY KEY AUTOINCREMENT, client_id TEXT, key TEXT, side TEXT, qty INTEGER,
  price REAL, ts TEXT, strategy TEXT, product TEXT, costs REAL, intent TEXT);
CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT, strategy TEXT, key TEXT, direction INTEGER,
  qty INTEGER, entry_ts TEXT, exit_ts TEXT, entry_price REAL, exit_price REAL, pnl_gross REAL, costs REAL, pnl_net REAL,
  r_multiple REAL, product TEXT, meta TEXT);
CREATE TABLE IF NOT EXISTS results(id INTEGER PRIMARY KEY AUTOINCREMENT, strategy TEXT, ts TEXT, r REAL, pnl REAL,
  entry_ts TEXT);
CREATE TABLE IF NOT EXISTS equity(ts TEXT PRIMARY KEY, equity REAL, realized REAL, unrealized REAL, costs REAL);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, level TEXT, source TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS kv(ns TEXT, key TEXT, value TEXT, PRIMARY KEY(ns, key));
CREATE TABLE IF NOT EXISTS regime(date TEXT PRIMARY KEY, state TEXT, probs TEXT, source TEXT);
"""


def _json(o) -> str:
    return json.dumps(o, default=str)


class Storage:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        if self.path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(results)")}
        if "entry_ts" not in cols:   # migrate stores created by earlier versions
            self.db.execute("ALTER TABLE results ADD COLUMN entry_ts TEXT")
        self.db.commit()

    def _x(self, sql: str, args=()) -> None:
        with self.lock:
            self.db.execute(sql, args)

    def flush(self) -> None:
        with self.lock:
            self.db.commit()

    def close(self) -> None:
        self.flush()
        self.db.close()

    def upsert_order(self, o) -> None:
        r = o.req
        self._x("INSERT OR REPLACE INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (o.client_id, o.broker_id, r.strategy, r.key, r.side.value, r.qty, r.order_type.value, r.product.value,
                 r.price, r.trigger, o.status.value, o.filled_qty, o.avg_price, r.intent, o.message,
                 o.created.isoformat() if o.created else None, o.updated.isoformat() if o.updated else None, _json(r.meta)))

    def add_fill(self, f) -> None:
        self._x("INSERT INTO fills(client_id,key,side,qty,price,ts,strategy,product,costs,intent) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (f.client_id, f.key, f.side.value, f.qty, f.price, f.ts.isoformat(), f.strategy,
                 getattr(f.product, "value", f.product), f.costs, f.intent))

    def add_trade(self, t) -> None:
        d = asdict(t)
        r = d["r_multiple"]
        self._x("INSERT INTO trades(strategy,key,direction,qty,entry_ts,exit_ts,entry_price,exit_price,pnl_gross,costs,"
                "pnl_net,r_multiple,product,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d["strategy"], d["key"], d["direction"], d["qty"], d["entry_ts"], d["exit_ts"], d["entry_price"],
                 d["exit_price"], d["pnl_gross"], d["costs"], d["pnl_net"],
                 None if r is None or (isinstance(r, float) and math.isnan(r)) else r, d["product"], _json(d["meta"])))

    def add_result(self, strategy: str, ts: datetime, r: float, pnl: float, entry_ts: str | None = None) -> None:
        self._x("INSERT INTO results(strategy,ts,r,pnl,entry_ts) VALUES(?,?,?,?,?)",
                (strategy, ts.isoformat(), r, pnl, entry_ts))

    def add_rows(self, table: str, rows: list[dict]) -> None:
        """Bulk insert of plain dict rows (used when merging parallel backtest jobs)."""
        if not rows:
            return
        cols = [c for c in rows[0] if c != "id"]
        vals = [tuple(_json(r[c]) if isinstance(r[c], (dict, list)) else r[c] for c in cols) for r in rows]
        with self.lock:
            self.db.executemany(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)

    def add_equity(self, ts: datetime, equity: float, realized: float, unrealized: float, costs: float) -> None:
        self._x("INSERT OR REPLACE INTO equity VALUES(?,?,?,?,?)", (ts.isoformat(), equity, realized, unrealized, costs))

    def log_event(self, ts: datetime, level: str, source: str, message: str) -> None:
        self._x("INSERT INTO events(ts,level,source,message) VALUES(?,?,?,?)", (ts.isoformat(), level, source, message[:1000]))

    def add_regime(self, day, state: str, probs, source: str) -> None:
        self._x("INSERT OR REPLACE INTO regime VALUES(?,?,?,?)", (str(day), state, _json(probs), source))

    def kv_set(self, ns: str, key: str, value) -> None:
        self._x("INSERT OR REPLACE INTO kv VALUES(?,?,?)", (ns, key, _json(value)))

    def kv_get(self, ns: str, key: str, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM kv WHERE ns=? AND key=?", (ns, key)).fetchone()
        return json.loads(row[0]) if row else default

    def _df(self, sql: str, args=()) -> pd.DataFrame:
        with self.lock:
            return pd.read_sql_query(sql, self.db, params=args)

    def trades_df(self, strategy: str | None = None) -> pd.DataFrame:
        if strategy:
            return self._df("SELECT * FROM trades WHERE strategy=? ORDER BY id", (strategy,))
        return self._df("SELECT * FROM trades ORDER BY id")

    def results_df(self) -> pd.DataFrame:
        return self._df("SELECT * FROM results ORDER BY id")

    def equity_df(self) -> pd.DataFrame:
        return self._df("SELECT * FROM equity ORDER BY ts")

    def orders_df(self) -> pd.DataFrame:
        return self._df("SELECT * FROM orders ORDER BY created")

    def fills_df(self) -> pd.DataFrame:
        return self._df("SELECT * FROM fills ORDER BY id")

    def events_df(self, level: str | None = None) -> pd.DataFrame:
        if level:
            return self._df("SELECT * FROM events WHERE level=? ORDER BY id", (level,))
        return self._df("SELECT * FROM events ORDER BY id")

    def regime_df(self) -> pd.DataFrame:
        return self._df("SELECT * FROM regime ORDER BY date")
