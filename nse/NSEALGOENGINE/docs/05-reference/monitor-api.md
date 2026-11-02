> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Monitor API

A running forward or live session serves read-only JSON for the desk (`src/nsealgo/monitor/`).

| | |
|---|---|
| **Address** | `http://127.0.0.1:<port>`, never another interface. The port is free-chosen unless `monitor.port` pins it |
| **Discovery** | `runs/<mode>/monitor.json`: `{"url": …, "token": …, "pid": …}` |
| **Auth** | Header `X-Nsealgo-Token: <token>` on every request; wrong or missing → `403` |
| **Errors** | `404` unknown path; `500` with `{"error": …}` if a view fails (logged as a warning) |

## Endpoints

| Method and path | Returns |
|---|---|
| `GET /api/status` | `mode`, `now`, `capital`, `equity`, `realized`, `costs`, `unrealized`, `exposure`, `regime`, `kill`, `instruments`, and lists: `positions` (strategy, key, product, qty, avg, ltp, unrealized, since), `orders` (latest 150: time, strategy, key, side, qty, price, product, intent, status, filled, avg, message), `strategies` (name, horizon, active, positions, unrealized, open_orders), `alerts` (latest 100 warnings and errors) |
| `GET /api/tiers` | Depth tiers, deepest first: `[{"name", "levels", "groups": [[keys per connection], …]}]` |
| `GET /api/books?keys=A,B` | Up to 60 books: `{key: {ts, ltp, prev_close, volume, bids, asks, tbq, tsq, signals}}`. Levels are `[price, qty, orders?]`; `signals` holds imbalance, ofi_z, microprice_bps, pressure, absorb_ask, absorb_bid, spread_bps, levels |
| `GET /api/watch` | Every streamed instrument: key, ltp, chg, volume, bid, ask, spread_bps, levels, signals |
| `GET /api/strategy?name=orb` | `{name, horizon, signals: [journal records + would_pct], trades: [entry/exit times and prices, pct, status], summary}` |
| `GET /api/series?key=NSE:SBIN&kind=intraday\|daily` | `{key, kind, bars: [[epoch, o, h, l, c, v], …], ltp, now}` |
| `POST /api/kill` | Sets the kill switch (writes `runs/<mode>/KILL`); returns `{kill: true, path}` |

## Example

```
$token = (Get-Content runs\forward\monitor.json | ConvertFrom-Json).token
Invoke-RestMethod http://127.0.0.1:52134/api/status -Headers @{"X-Nsealgo-Token" = $token}
```

## Stability

The desk is the only client, and it ships with the engine, so fields may change between releases. Record any change in the CHANGELOG and in this document. Review mode reads the journal files directly, not this API.
