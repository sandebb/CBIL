> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Runbook

## Daily routine (IST)

| Time | Action | Check |
|---|---|---|
| 08:30 | Machine on, plugged in, online | — |
| 08:40 | The scheduled task starts `nsealgo forwardtest` (or `live`); log in when prompted (`nsealgo login fyers`) | Session log: "orders leave from registered static IP" |
| 08:50 | `nsealgo desk` | Banner shows the right mode; session link "connected" |
| 09:15–15:30 | Watch the Alerts tab and the cards; the depth and strategy windows as needed | No STALE flags; no UNKNOWN orders lingering |
| 15:30 | Session ends; reports written | `runs/<mode>/` has today's report and journal |
| 15:45 | Review the day (`nsealgo desk --review`); back up ([Backup](backup-and-recovery.md)) | — |

## Incident playbooks

| # | Symptom | Do |
|---|---|---|
| 1 | Login refused: "requests leave from X, not your registered static IP" | Check the StaticIP.in service and `.env` (`STATICIP_PROXY`, `STATIC_IP`); run `nsealgo netcheck`. Do not remove `static_ip` to get past this |
| 2 | Orders stuck in **UNKNOWN** | The broker didn't acknowledge them. Check the broker's order book; the OMS confirms by tag on the next sync. Don't re-place them by hand |
| 3 | Rate-limit warnings, or Fyers rejects calls | Calls are held at 90% automatically. If Fyers blocked the account for the day, set the kill switch and stop the session |
| 4 | Feed STALE, or the desk shows old data | Check the Alerts tab for failover messages and the internet connection. The mesh fails over automatically; restart the session if no feed recovers |
| 5 | Position mismatch halt (live) | Compare the broker's positions with the desk; fix manually at the broker if needed; restart the session |
| 6 | Machine restarted mid-day | Start the same command; today's journal and OMS state reload. Check open positions at the broker |
| 7 | Anything unexpected while live | **KILL SWITCH** (desk) or `nsealgo kill`, then investigate; `nsealgo resume` and restart afterwards |
| 8 | GPU errors in the log | Calculations fall back to the CPU by themselves. To stay on the CPU, set `compute.device: cpu` |
| 9 | Fyers TBT error text in the log (e.g. a symbol limit) | Lower `feeds.depth_overlay.tbt_max_symbols` to the limit shown |

Escalation: the owner is the decision maker for every incident ([RACI](../09-project/raci.md)).
