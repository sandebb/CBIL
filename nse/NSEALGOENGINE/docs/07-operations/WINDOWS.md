> **Monorepo Path Architecture (`NSE_ROOT`)**: This repository uses a mirrored `code/` and `data/` directory structure rooted at the **`NSE_ROOT`** environment variable. All source code resides under `$NSE_ROOT/code/<ENGINE>` and all runtime artifacts (logs, SQLite/DuckDB databases, Parquet datasets, trained ML models, and generated reports) reside in the mirrored `$NSE_ROOT/data/<ENGINE>` directory.

# Running nsealgo on Windows 11

Windows 11 (or later) is the reference platform. Everything below is either automatic or one script away.

## 1. Setup (once)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1 -Extras "dev,zerodha"
```
The script:
- creates `.venv` and installs nsealgo with the broker extras you list;
- sets `PYTHONUTF8=1` for your user;
- runs `nsealgo hardware` and `nsealgo doctor`.

`doctor` checks, among other things, the clock offset against NTP and the active power plan.

Optional switches:
- `-PowerPlan` activates Ultimate Performance.
- `-DefenderDataExclusions` (Administrator) excludes only the data folders; see section 3.

Always run the tool as `nsealgo ...` or `python -m nsealgo ...` from the activated venv (not `python -m src.nsealgo.cli`).

## 2. Put the project on a Dev Drive (recommended)

**Why it matters:**
- Every backtest worker opens the history database and imports NumPy, pandas and SciPy.
- Defender's real-time protection normally scans each open *synchronously*.
- On a *trusted* Dev Drive, Defender switches to **performance mode**: files open immediately and are scanned asynchronously. Microsoft positions this as a safer alternative to folder exclusions.

**Requirements:** Windows 11 build 22621.2338 or later, at least 50 GB free, and local administrator rights. C: cannot be a Dev Drive.

**How to set it up:**
1. Create the drive: Settings → System → Storage → Advanced storage settings → Disks & volumes → Create Dev Drive (a VHD works).
2. Check it's trusted: `fsutil devdrv query D:` (`fsutil devdrv trust D:` if not).
3. Clone the project there, venv included. Pointing `PIP_CACHE_DIR` there also helps.

On a managed (corporate) device, Dev Drive trust and performance mode may be set by policy.

Microsoft's guide: https://learn.microsoft.com/en-us/windows/dev-drive/

## 3. If you use Defender exclusions instead

- **Exclude only nsealgo's data folders** (`data`, `runs`, `recordings`, `.cache`): `setup.ps1 -DefenderDataExclusions`. These hold market data and results, not code.
- **Do not add a process exclusion for `python.exe`**, and do not exclude the venv. A process exclusion stops scanning of *every* file any Python program touches, including packages that pip downloads: a supply-chain blind spot.

## 4. Power, sleep, priorities (automatic)

Configured in `system:` in `config/system.yaml`:
- **Keep-awake:** while a forward/live session or a backtest runs, the PC won't go to sleep (`SetThreadExecutionState`). A sleeping desktop silently drops every broker WebSocket. Set `keep_awake: always` for the whole daemon lifetime, or `off`.
- **EcoQoS opt-out:** Windows 11 can throttle a minimised console (EcoQoS power throttling). nsealgo opts its processes out.
- **Priorities:** backtest workers run *below normal*, so the desktop and a running forward/live engine stay responsive; batch throughput is unchanged when nothing else competes. The forward/live engine runs *above normal*. *High* priority is deliberately not used: 16+ busy high-priority processes would starve the live engine's own network threads.
- **Ultimate Performance plan** (optional, `setup.ps1 -PowerPlan`): all-core backtests already hold full clocks under load on any plan; the plan mainly helps bursty, latency-sensitive live work. Measure with `nsealgo bench` before and after. Revert with `powercfg /setactive SCHEME_BALANCED`, and keep laptops on AC.

## 5. Clock

The session timers (09:15 start, 15:10 flatten, ORB windows) run on the PC clock. `nsealgo doctor` reports the offset from `time.windows.com`. If it is more than a second, resync from an administrator prompt with `w32tm /resync`.

## 6. Unattended forward testing (Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\register-tasks.ps1 -DataBroker "shoonya,angelone,fyers"
```

This registers the task `nsealgo-forward`, which:
- starts at 08:30 local time on weekdays and **wakes the PC** (enable *Allow wake timers* under Power Options → Sleep);
- runs `nsealgo forwardtest --daemon`;
- restarts on failure;
- appends to `logs\forward.log`.

Requirements:
- **Clock:** the PC clock must be on IST.
- **Unattended login:** broker TOTP secrets must be in `.env` so logins need no interaction.

Manual daily routine: `scripts\windows\morning.ps1 -Broker zerodha`. To remove the task: `Unregister-ScheduledTask nsealgo-forward`.

## 7. Multi-core on Windows

- **Spawn start-up:** Windows starts worker processes with `spawn` (a fresh Python per worker). nsealgo keeps **one pool alive** across regime fits, backtest jobs and walk-forward variants, so that start-up cost is paid once per run.
- **Worker limits:** default is one worker per physical core; `--workers 32` tries the SMT threads. Windows caps a process pool at 61 workers.
- **Ctrl+C** stops all workers cleanly.
- **Your own scripts** must put their calling code under `if __name__ == "__main__":` (see `scripts/parallel_backtest.py`). `nsealgo` itself is guarded.
