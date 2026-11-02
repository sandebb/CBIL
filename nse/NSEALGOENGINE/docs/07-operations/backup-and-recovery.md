> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Backup and recovery

## What to back up

| Data | Path | How often | Notes |
|---|---|---|---|
| Session state and journals | `runs/` | Daily, after the close | Irreplaceable: orders, fills, trades, journals |
| Configuration | `config/` | On change | Contains no secrets |
| Secrets | `.env` | On change | Encrypted storage only (e.g. a password manager); never in plain cloud folders |
| History | `data/history.db` | Weekly | Re-downloadable, but slow |
| Recordings | `recordings/` | Optional | Large; archive or prune after 30–90 days |
| Code and docs | The repository | Every change | Git, or the release zips |

`.cache/` holds daily tokens and symbol masters; it doesn't need backing up. The exception is `.cache/multi_state.json`, if you use multiple accounts.

## How

Copy SQLite files **with the session stopped**, or use SQLite's backup API, because WAL files must travel with their databases:

```
robocopy runs  E:\nsealgo-backup\runs  /MIR /R:1 /W:1
robocopy data  E:\nsealgo-backup\data  /MIR /R:1 /W:1
```

## Recovery

| Situation | Steps |
|---|---|
| Session crashed mid-day | Restart the same command; the journal and OMS state reload. Check broker positions |
| Corrupt `state.db` | Restore last night's copy; today's orders remain in the journal and at the broker |
| New machine | [Install](installation-guide.md), restore `runs/`, `data/`, `config/` and `.env`, re-register the static IP if it changed, then run `nsealgo doctor` and `netcheck` |
