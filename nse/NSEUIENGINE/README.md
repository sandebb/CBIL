> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# NSEUIENGINE — Fyers access token, refreshed daily at 08:00

Fourth engine alongside NSEALGOENGINE, NSEDATAENGINE and NSEMLENGINE. Every morning it puts a valid Fyers API v3
access token into Windows environment variables so any script on this PC can use it.

| Variable | Value |
|---|---|
| `FYERS_ACCESS_TOKEN` | the access token |
| `FYERS_WS_TOKEN` | `<app_id>:<token>`, used by FyersDataSocket, the TBT socket and the REST `Authorization` header |
| `FYERS_TOKEN_DATE` | IST date the token was issued |
| `FYERS_TOKEN_ISSUED_AT` | IST timestamp |
| `FYERS_CLIENT_ID`, `FYERS_ID`, `FYERS_REDIRECT_URI`, `NSEUIENGINE_HOME` | set once by `--setup` |

## One-time setup

1. In Fyers, enable **External 2FA TOTP** (myaccount.fyers.in → Security) and note the TOTP secret key.
2. Have your API app's **App ID**, **secret key** and **redirect URL** from myapi.fyers.in (redirect
   `http://127.0.0.1:5000/` is what NSEALGOENGINE already uses).
3. Run: `powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEUIENGINE\setup_fyers_token.ps1`

The setup creates `.venv`, asks for credentials, gets the first token (a browser login opens if Fyers wants you to
approve the app once), and registers the task **NSEUIENGINE-FyersToken** (daily 08:00 and at logon, wakes the PC,
catches up if the PC was off).

## What each run does

1. Checks whether each token variable exists; any that are missing are created.
2. If today's token is already there and Fyers accepts it (`/profile`), it stops.
3. Otherwise it tries **refresh token** (official `validate-refresh-token`, valid 15 days), then **TOTP login**
   (Fyers ID + TOTP + PIN → auth code → `validate-authcode`, which also renews the refresh token), then (only with
   `--interactive`) a **browser login**.
4. It checks the new token against `/profile`, writes the variables and broadcasts the change.

Network errors are retried 3× (2 min apart). Wrong PIN/TOTP is **never** retried, because repeated failures can
lock the Fyers account.

## Using the token from other engines

```python
import os, sys; sys.path.insert(0, os.environ["NSEUIENGINE_HOME"])
from fyers_token import get_access_token, get_ws_token, get_client_id
```

`fyers_token.py` reads the registry, so an IDE or terminal opened before 08:00 still gets today's token.
`os.environ["FYERS_ACCESS_TOKEN"]` works only in processes started after the refresh.

## Security

- Secrets (secret key, PIN, TOTP secret, refresh token) are stored in `state\fyers_secrets.bin`, encrypted with
  Windows DPAPI. Only your Windows account on this PC can decrypt the file. Tokens are never logged.
- Anything holding the TOTP secret plus PIN can log in to your Fyers account. Keep `%NSE_ROOT%\code\NSEUIENGINE\state` out
  of backups, zips and git.
- `-Scope Machine` exposes the token to every account on the PC. Use the default (User) unless you need it.

## Commands

```
.venv\Scripts\python fyers_token_manager.py --status        what is set (masked)
.venv\Scripts\python fyers_token_manager.py --force         new token now
.venv\Scripts\python fyers_token_manager.py --interactive   allow browser login
.venv\Scripts\python fyers_token_manager.py --setup         change credentials
Start-ScheduledTask NSEUIENGINE-FyersToken                   run the task now
```

Log: `logs\fyers_token.log`. Last result: `state\fyers_token_status.json`.

## Notes

- The TOTP-login endpoints (`api-t2.fyers.in/vagator/v2/...`) are the ones Fyers' own login page uses. They are not
  in the published API docs and could change. If they break, the refresh token still works for up to 15 days, and
  `--interactive` always works.
- The PC clock must be correct, because TOTP depends on it (Windows time sync on).
- The task runs only while you are logged on, unless you registered it with `-RunWhenLoggedOff`.

---

# Swing trader (real money)

`swing_trader.py` trades NSEDATAENGINE's v3.11 swing signals on Fyers: 1 share per signal, the strategy's own
entry and exit, a protective stop on every position, and hard risk limits. Setup:

1. Enable **DDPI** on your Fyers account (Profile > DDPI). Without it the API cannot sell shares from your demat.
2. Set the user environment variables `STATICIP_PROXY` (http://user:pass@host:port) and `STATIC_IP` (the IP registered
   with Fyers). Orders are refused unless they leave from that IP (`require_static_ip`).
3. `powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEUIENGINE\setup_swing_trader.ps1`
4. Check the plan: `.venv\Scripts\python.exe swing_trader.py evening --no-scan`
5. Set `"live": true` in `state\trader_config.json`. Until then everything is a dry run.

| Time | Task | What happens |
|---|---|---|
| 20:30 Mon-Fri | Trader-Evening | NSEDATAENGINE update + scan, equity mark, prints tomorrow's plan |
| 08:58 Mon-Fri, at logon | Trader-Morning | 09:00 pre-open: exits (rule / time) and limit buys; 09:15 stops + PEAD buy-stops; until 15:30 fills, stops, targets, risk; 15:31 end of day |

Rules: every regime-allowed signal, highest backtested edge first; never two positions in one stock; skips shares
above `max_price_per_share`; total invested <= `capital`; `daily_loss_limit` / `stop_entries_drawdown` stop new
entries; `flatten_drawdown` sells everything. Exit: target strategies (pead) sell at target, others on v3.11's exit
rule at the next open; all have a stop at the exchange and a 3-month time exit.

Commands: `status`, `report` (live results per strategy vs the backtest), `kill`, `kill --flatten`, `resume`,
`morning --dry-run`. Files: `state\trader_config.json`, `state\trader_state.json`, `reports\trade_journal.csv`,
`logs\swing_trader.log`.
