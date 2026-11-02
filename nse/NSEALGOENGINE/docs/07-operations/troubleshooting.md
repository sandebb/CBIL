> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `nsealgo doctor` reports a missing SDK | Broker extra not installed | `pip install -e .[fyers]` (etc.); `nsealgo brokers` lists the commands |
| Upstox import breaks the standard library | The obsolete PyPI `uuid` package | `pip uninstall -y uuid` (doctor checks this) |
| "orders leave from X, not your registered static IP" | Proxy down, wrong credentials, or IP not registered | `nsealgo netcheck`; fix `.env`; register the IP with the broker |
| "… order placement is dormant" | You routed orders to a broker with `orders: false` | Choose another broker, or set `orders: true` for it |
| "no live order broker" | `broker.orders` not set and execution is `paper` | Set `broker.orders.intraday` (and `delivery`) |
| Orders stay UNKNOWN | No acknowledgement (Fyers 201 / −99, timeouts) | Wait for the next sync; check the broker's order book ([Runbook](runbook.md) #2) |
| Desk: DISCONNECTED | No running session, or a stale `monitor.json` | Start the session; the desk reconnects when it runs |
| Desk: "No deep-book tier yet" | `feeds.depth_overlay.source` is `none`, or nothing is ranked yet | Configure `fyers_tbt` / `upstox`; wait for the first focus update (60 s) |
| `nsealgo gpu`: "PyTorch is not installed" | Not installed, or the venv isn't Python 3.12 | Re-run `setup.ps1 -Gpu` with Python 3.12 |
| GPU listed but never chosen | The CPU measured faster at your sizes | Expected; see `nsealgo gpu` |
| Qt "could not load the platform plugin" on a server | No display | `QT_QPA_PLATFORM=offscreen` (tests), or use a desktop session |
| SQLite "database is locked" | A second writer, or a backup copying live files | One session per run folder; back up with the session stopped |
| Times look shifted | Machine clock or timezone | Windows time sync on; IST timezone; `nsealgo doctor` reports the clock offset |

Still stuck? Collect `logs/`, the Alerts tab, `nsealgo doctor` output and the CHANGELOG version, and see [Monitoring](monitoring-and-alerts.md).
