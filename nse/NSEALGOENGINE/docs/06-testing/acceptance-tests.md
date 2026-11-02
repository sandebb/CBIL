> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Acceptance tests (PX13, manual)

Run these on the real machine with real accounts. Record the date, version and result in the release notes.

| ID | Steps | Expected |
|---|---|---|
| **AT-01 Install** | `powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1 -Gpu`, then `nsealgo doctor` | Setup completes; doctor reports no errors |
| **AT-02 GPU** | `nsealgo gpu --fresh` | PyTorch, ROCm and "Radeon 8060S" shown; each kernel shows "matches: yes" and a choice |
| **AT-03 Static IP, good** | `nsealgo netcheck` | "egress IP …: matches the registered static IP"; kept-alive latency below new-connection latency |
| **AT-04 Static IP, bad** | Set a wrong `STATIC_IP` in `.env`; `nsealgo login fyers` | Login refused: "requests leave from …, not your registered static IP"; restore `.env` afterwards |
| **AT-05 Login and data** | `nsealgo login fyers`; `nsealgo fetch-history` | Token cached; history downloaded |
| **AT-06 Desk demo** | `nsealgo desk --demo` | Main window; depth window with 3 TBT tabs × 5 ladders × 50 levels; market watch; strategy windows with charts |
| **AT-07 Forward test** | `nsealgo forwardtest` (full session), with `nsealgo desk` open | FORWARD banner; positions and orders update; strategy windows show signals and charts; no orders at the broker |
| **AT-08 Kill switch** | During AT-07, press KILL SWITCH and confirm | Banner turns red; no new entries; intraday positions flattened; `nsealgo resume` clears it |
| **AT-09 Review** | After the session: `nsealgo desk --review` | Today's date selectable; strategy windows chart the day; live-only buttons greyed out |
| **AT-10 Routing** | `nsealgo live` (without the risk flag) | Prints "all live orders -> fyers", then the risk reminder, and exits |
| **AT-11 Dormant broker** | `nsealgo live --broker shoonya` | Refused: "shoonya order placement is dormant…" |
| **AT-12 Restart** | Stop a forward session mid-day; start it again | Today's journal reloads; the desk shows the earlier signals |
