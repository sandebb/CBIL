> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Monitoring and alerts

| Source | What | Where |
|---|---|---|
| Desk | Status cards, positions, orders, strategies; depth and data age; signal charts | `nsealgo desk` |
| Alerts tab | Every warning and error since the session started | Desk → Alerts |
| Telegram (optional) | Session start/stop, kill switch, halts, errors | `notifier.telegram_token` and `notifier.telegram_chat_id` (from `.env`) |
| Logs | Full detail | `logs/` |
| Events | Warnings and errors persisted | `runs/<mode>/state.db`, table `events` |
| Reports | End-of-day results, bootstrap intervals, gate verdicts | `runs/<mode>/` and `nsealgo report` |

## What to watch

| Signal | Why it matters | Normal |
|---|---|---|
| Session link | Desk ↔ session | "connected" |
| STALE flags, data age in ms | Decisions on old data | Under a second during market hours |
| UNKNOWN orders | Possibly live orders not yet confirmed | None, or briefly |
| Rejections | Margin, price bands, broker issues | Rare |
| Kill switch banner | Trading stopped | Off |
| Day P&L vs risk limits | The daily loss limit stops new entries | Within limits |
| TBT and Upstox depth notes | Broker limits differing from config | None after the first session |
