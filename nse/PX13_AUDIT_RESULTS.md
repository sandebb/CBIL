# PX13 Post-Refactoring Code & Artifact Sweep Report

## 1. Automated Syntax, Indentation & Config Validation
- Total Python (`.py`) files checked with `ast.parse()`: **226**
- Total JSON (`.json`) files checked with `json.loads()`: **10**
- Total YAML (`.yaml`/`.yml`) files checked with `yaml.safe_load()`: **13**

### Critical Bugs Caught & Fixed Automatically
- **Finding:** HIGH: code/NSEALGOENGINE/src/nsealgo/config.py used CWD-dependent relative '../../data/...' strings which fail when CLI is invoked outside code/NSEALGOENGINE.
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/Fyerstbt/fyers_tbt_streamer.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/NSEALGOENGINE/scripts/research/ml_gap_topn.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/NSEALGOENGINE/scripts/research/ml_overnight_backtest.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/NSEALGOENGINE/scripts/research/ml_range_orders.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/NSEALGOENGINE/scripts/research/opening_window_test.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/NSEALGOENGINE/scripts/research/rank_topn_test.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/NSEALGOENGINE/scripts/research/rsi_intraday_selection.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/NSEALGOENGINE/scripts/research/tbt_event_study.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/NSEDATAENGINE/bt_strategies.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/NSEDATAENGINE/nse_backtest.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/NSEDATAENGINE/nse_portfolio.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/NSEDATAENGINE/nse_updater.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/NSEUIENGINE/ml_accuracy.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/NSEUIENGINE/signal_ml_tracker.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/NSEUIENGINE/swing_trader.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/NSEUIENGINE/whatif.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/docs/archive/nse_updater.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/docs/archive/run_pipeline_parquet_CHUNKED_FIX_CTEMP.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/run_pipeline_parquet_CHUNKED_FIX_CTEMP.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/scripts/validation/direct_validation.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/scripts/validation/proper_diagnose.py (Python Path() does not expand shell variables automatically).
- **Finding:** HIGH: Literal unexpanded '${NSE_ROOT}' or '%NSE_ROOT%' found in Python file code/scripts/validation/validate_strategy_FIXED.py (Python Path() does not expand shell variables automatically).
- **Finding:** JSON PARSE ERROR in code/deploy/scheduler/user_env.json: Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)
- **Finding:** Could not auto-fix JSON code/deploy/scheduler/user_env.json: Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)
- **Fixed:** Fixed top-level indentation and auto-discovery in code/NSEDATAENGINE/nse_contract.py
- **Fixed:** Fixed top-level indentation and work_dir/data_engine_home resolution in code/NSEMLENGINE/nseml/config.py
- **Fixed:** Updated code/NSEALGOENGINE/src/nsealgo/config.py to resolve absolute paths dynamically from NSE_ROOT regardless of working directory.

## 2. Path & Cross-Engine References Found Across Subsystem `.py` Files
- `code/NSEUIENGINE/fyers_token_manager.py:86` -> `"logs"` in `LOG_DIR = DATA / "logs"`
- `code/NSEUIENGINE/fyers_token.py:44` -> `C:\\NSE\\DATA\\NSEUIENGINE` in `(the shared data area, e.g. C:\\NSE\\DATA\\NSEUIENGINE), else the folder this file is in."""`
- `code/NSEUIENGINE/whatif.py:143` -> `"reports"` in `out = data_dir() / "reports" / f"whatif_{datetime.now():%Y%m%d_%H%M}.txt"`
- `code/NSEUIENGINE/holdings.py:33` -> `"reports"` in `REPORTS = data_dir() / "reports" if (_read("NSEUIENGINE_DATA_DIR") or _read("NSE_DATA_ROOT")) else H`
- `code/NSEUIENGINE/holdings.py:33` -> `"reports"` in `REPORTS = data_dir() / "reports" if (_read("NSEUIENGINE_DATA_DIR") or _read("NSE_DATA_ROOT")) else H`
- `code/NSEUIENGINE/signal_ml_tracker.py:40` -> `"reports"` in `else Path(os.environ.get("NSEUIENGINE_HOME") or HERE)) / "reports"`
- `code/NSEUIENGINE/swing_trader.py:79` -> `"logs"` in `STATE_DIR, LOG_DIR = DATA / "state", DATA / "logs"`
- `code/NSEUIENGINE/swing_trader.py:82` -> `"reports"` in `JOURNAL_FILE = DATA / "reports" / "trade_journal.csv"`
- `code/NSEUIENGINE/ml_accuracy.py:37` -> `"reports"` in `else Path(os.environ.get("NSEUIENGINE_HOME") or HERE)) / "reports"`
- `code/Fyerstbt/fyers_tbt_streamer.py:608` -> `"logs"` in `logs = out_dir / "logs"`
- `code/NSEDATAENGINE/nse_fno_universe.py:73` -> `"logs"` in `self.logs = self.data / "logs"`
- `code/NSEDATAENGINE/nse_fresh_build.py:99` -> `D:\nse\data_fresh` in `python nse_fresh_build.py --data-dir D:\nse\data_fresh build`
- `code/NSEDATAENGINE/nse_fresh_build.py:100` -> `D:\exports\features_5y.parquet` in `python nse_fresh_build.py --out D:\exports\features_5y.parquet build`
- `code/NSEDATAENGINE/nse_fresh_build.py:132` -> `"logs"` in `self.logs = self.data / "logs"`
- `code/NSEDATAENGINE/nse_intraday.py:58` -> `"logs"` in `LOG = C.location("ref") / "logs" / "intraday_log.txt"`
- `code/NSEDATAENGINE/nse_intraday.py:370` -> `"logs"` in `out = C.location("ref") / "logs" / "intraday_coverage.csv"`
- `code/NSEDATAENGINE/nse_preopen.py:58` -> `"logs"` in `LOG = C.location("master") / "logs" / "preopen_log.txt"`
- `code/NSEDATAENGINE/nse_preopen.py:450` -> `"logs"` in `rp = C.location("master") / "logs" / "preopen_report.txt"`
- `code/NSEDATAENGINE/nse_common.py:54` -> `C:\\Program` in `C:\\Program Files and the script is not running as administrator) it retries with --user, which`
- `code/NSEDATAENGINE/nse_portfolio.py:120` -> `"backtest_results"` in `run_dir = Path(args.run) if Path(args.run).is_dir() else C.location("backtest_results") / args.run`
- `code/NSEDATAENGINE/nse_backtest.py:87` -> `D:\code\NSEALGOENGINE` in `python nse_backtest.py run --engine-dir D:\code\NSEALGOENGINE`
- `code/NSEDATAENGINE/nse_backtest.py:362` -> `"backtest_results"` in `return p if p.is_dir() else C.location("backtest_results") / name`
- `code/NSEDATAENGINE/nse_backtest.py:502` -> `"backtest_results"` in `out_dir = Path(args.out_dir) if args.out_dir else C.location("backtest_results") / run_name`
- `code/NSEDATAENGINE/nse_signals.py:127` -> `"backtest_results"` in `f = C.location("backtest_results") / run / "summary.csv"`
- `code/NSEDATAENGINE/nse_updater.py:85` -> `D:\nse\data` in `python nse_updater.py --data-dir D:\nse\data update`
- `code/NSEDATAENGINE/nse_updater.py:86` -> `D:\exports\features.parquet` in `python nse_updater.py --out D:\exports\features.parquet features`
- `code/NSEDATAENGINE/nse_updater.py:115` -> `"logs"` in `self.logs = self.data / "logs"`
- `code/NSEDATAENGINE/nse_alt_data.py:59` -> `"logs"` in `LOG = REF / "logs" / "alt_data_log.txt"`
- `code/NSEDATAENGINE/nse_alt_data.py:158` -> `"logs"` in `p = C.location("master") / "logs" / "symbol_changes.csv"`
- `code/NSEDATAENGINE/nse_close_snapshot.py:47` -> `"logs"` in `LOG = C.location("master") / "logs" / "at_close_log.txt"`
- `code/NSEMLENGINE/compare_models.py:32` -> `"work_alt"` in `ap.add_argument("--b", default=str(HOME / "work_alt" / "results" / "phase1_alt"))`
- `code/NSEMLENGINE/nseml/config.py:42` -> `"work_alt"` in `folder = str(cfg().get("work_folder") or ("work_alt" if alt else "work"))`
- `code/NSEMLENGINE/nseml/live.py:49` -> `"logs"` in `d = {"models": w / "live" / "models", "pred": w / "predictions", "logs": w / "logs"}`
- `code/NSEMLENGINE/nseml/live.py:49` -> `"logs"` in `d = {"models": w / "live" / "models", "pred": w / "predictions", "logs": w / "logs"}`
- `code/NSEMLENGINE/nseml/live.py:58` -> `"logs"` in `with open(_dirs()["logs"] / "predict_log.txt", "a", encoding="utf-8") as f:`

## 3. All `.bat`, `.ps1`, and `.cmd` Launcher Scripts in `code/`
### `code/Fyerstbt/START_FYERS_TBT.bat`
```
@echo off
rem Double-click launcher only - all logic is in fyers_tbt_streamer.py. Nothing is asked for or saved.
python "%~dp0fyers_tbt_streamer.py" %*
pause
```
### `code/NSEALGOENGINE/deploy/windows/register-tasks.ps1`
```
<#
.SYNOPSIS
  Run the forward test unattended with Windows Task Scheduler (replaces deploy/*.service and crontab on Windows).
    powershell -ExecutionPolicy Bypass -File deploy\windows\register-tasks.ps1 -DataBroker "shoonya,angelone,fyers"
  Creates task "nsealgo-forward": starts at -Start (default 08:30, local time) Monday-Friday, WAKES THE PC from sleep,
  runs `nsealgo forwardtest --daemon` (it waits for pre-market, logs in, trades the session, sleeps until the next
  one), restarts on failure, and appends output to logs\forward.log. Remove: Unregister-ScheduledTask nsealgo-forward
  Requirements: the PC clock set to IST (or shift -Start accordingly); "Allow wake timers" enabled in the power
  plan; broker credentials incl. TOTP secrets in .env so logins need no interaction.
#>
param([string]$DataBroker = "", [string]$Start = "08:30", [string]$TaskName = "nsealgo-forward")
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
New-Item -ItemType Directory -Force -Path (Join-Path $root "logs") | Out-Null
$cliArgs = "forwardtest --daemon" + $(if ($DataBroker) { " --data-broker $DataBroker" } else { "" })
$cmd = "set `"PYTHONUTF8=1`" && `"$root\.venv\Scripts\nsealgo.exe`" $cliArgs >> `"$root\logs\forward.log`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $cmd" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $Start
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "nsealgo forward test (paper trading on live data)" -Force | Out-Null
Write-Host "Registered '$TaskName' (weekdays $Start, wakes the PC). Test now: Start-ScheduledTask $TaskName"
```
### `code/NSEALGOENGINE/deploy/windows/register_minute_archive_task.ps1`
```
<#
.SYNOPSIS
  Scheduled task "NSEALGOENGINE-MinuteArchive": adds each day's 1-minute bars to data\minute_archive.

    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEALGOENGINE\deploy\windows\register_minute_archive_task.ps1

  Mon-Fri 19:05 -> run_minute_archive.cmd -> nsealgo fetch-minute-archive --unattended (re-downloads only the current
  90-day span of each stock, ~900 Fyers requests, a few minutes) -> nsealgo liquidity-table.
  Needs the day's Fyers token (NSEUIENGINE-FyersToken, 08:00). Wake to run is on (sleep is fine, not shut down).
  Runs whether you are logged on or not: asks for your Windows password once.
  -Interactive   register "only when logged on" instead (no password asked)
  -At "19:05"    start time (after 15:30; Fyers has the day's bars by then)

  Check:   .venv\Scripts\python -m nsealgo minute-archive-status      Log: runs\logs\minute_archive_task.txt
  Remove:  Unregister-ScheduledTask NSEALGOENGINE-MinuteArchive -Confirm:$false
#>
param([string]$At = "19:05", [switch]$Interactive)
$ErrorActionPreference = "Stop"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$cmd = Join-Path $root "run_minute_archive.cmd"
if (-not (Test-Path $cmd)) { throw "not found: $cmd" }
$name = "NSEALGOENGINE-MinuteArchive"

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$cmd`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew
$desc = "NSEALGOENGINE: daily top-up of the 1-minute parquet archive + liquidity table (run_minute_archive.cmd)"
$user = "$env:USERDOMAIN\$env:USERNAME"

if ($Interactive) {
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description $desc -Force | Out-Null
} else {
    $cred = Get-Credential -UserName $user -Message "Windows password for $name (runs while you are logged off)"
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
        -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited `
        -Description $desc -Force | Out-Null
}
$t = Get-ScheduledTask -TaskName $name
Write-Host ("Registered {0}: Mon-Fri {1}, logon {2}, wake to run {3}" -f $name, $At, $t.Principal.LogonType, $t.Settings.WakeToRun) -ForegroundColor Green
Write-Host "Run it once now:  Start-ScheduledTask $name   (log: $root\runs\logs\minute_archive_task.txt)"
```
### `code/NSEALGOENGINE/deploy/windows/register_minute_prefetch_task.ps1`
```
<#
.SYNOPSIS
  Scheduled task "NSEALGOENGINE-MinutePrefetch": intraday 1-minute prefetch (today's bars -> partial_<date>.parquet).

    powershell -ExecutionPolicy Bypass -File deploy\windows\register_minute_prefetch_task.ps1

  Mon-Fri 09:20-15:35, every 15 min -> run_minute_prefetch.cmd -> nsealgo prefetch-today --unattended.
  Idle-gated: nsealgo polls the live session's monitor and waits for zero open orders before calling the Fyers
  data API (if there is no session it proceeds - nothing is being sent). Light + idempotent: one small Fyers
  request per stock; each round overwrites partial_<date>.parquet with more of the day's bars. The 19:05 full
  run (NSEALGOENGINE-MinuteArchive) folds these into the 90-day span and deletes them.
  Needs the day's Fyers token (NSEUIENGINE-FyersToken, 08:00). Wake to run is on. Runs whether logged on or not:
  asks for your Windows password once.
  -Interactive    register "only when logged on" instead (no password asked)
  -Start "09:20" -End "15:35" -Every 15

  NOTE: registered through schtasks + task XML because this PC's ScheduledTasks PowerShell module cannot set a
  trigger repetition (no RepetitionInterval property, nested pattern not constructible) - the XML path is the
  one that works, and it is verified after registration (Repetition + WakeToRun in the task XML).

  Check:  .venv\Scripts\python -m nsealgo minute-archive-status     Log: runs\logs\minute_prefetch_task.txt
  Remove: Unregister-ScheduledTask NSEALGOENGINE-MinutePrefetch -Confirm:$false
#>
param([string]$Start = "09:20", [string]$End = "15:35", [int]$Every = 15, [switch]$Interactive)
$ErrorActionPreference = "Stop"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$cmd = Join-Path $root "run_minute_prefetch.cmd"
if (-not (Test-Path $cmd)) { throw "not found: $cmd" }
$name = "NSEALGOENGINE-MinutePrefetch"

$startT = [datetime]::ParseExact($Start, "HH:mm", [globalization.CultureInfo]::InvariantCulture)
$endT   = [datetime]::ParseExact($End,   "HH:mm", [globalization.CultureInfo]::InvariantCulture)
if ($endT -le $startT) { throw "End ($End) must be after Start ($Start)" }
$everyT = $endT - $startT
$repDur = ("PT{0}H{1:00}M" -f $everyT.Hours, $everyT.Minutes)

# local timezone offset for StartBoundary (e.g. +05:30) - integer hours + minutes component (TimeSpan.Hours is an int,
# a [Math]::Abs of a double like 5.5 with a "00" format would format as 55)
$tzOff = [System.TimeZoneInfo]::Local.GetUtcOffset([datetime]::Now)
$sign = if ($tzOff.TotalHours -ge 0) { "+" } else { "-" }
$tzOff = "{0}{1:00}:{2:00}" -f $sign, [int][Math]::Floor([double]$tzOff.TotalHours), [int][Math]::Abs($tzOff.TotalMinutes % 60)
$startBoundary = "{0}T{1:00}:{2:00}:{3:00}{4}" -f [datetime]::Now.ToString("yyyy-MM-dd"), $startT.Hour, $startT.Minute, 0, $tzOff

$xml = @"
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>NSEALGOENGINE: intraday 1-minute prefetch (today's bars -&gt; partial_&lt;date&gt;.parquet), every $Every min $Start-$End</Description>
    <URI>\$name</URI>
  </RegistrationInfo>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <WakeToRun>true</WakeToRun>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$startBoundary</StartBoundary>
      <ScheduleByWeek>
        <WeeksInterval>1</WeeksInterval>
        <DaysOfWeek>
          <Monday />
          <Tuesday />
          <Wednesday />
          <Thursday />
          <Friday />
        </DaysOfWeek>
      </ScheduleByWeek>
      <Repetition>
        <Interval>PT${Every}M</Interval>
        <Duration>$repDur</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </CalendarTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>/c "$cmd"</Arguments>
      <WorkingDirectory>$root</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
$xmlFile = Join-Path $env:TEMP "bionic_${name}.xml"
[System.IO.File]::WriteAllText($xmlFile, $xml, [System.Text.UTF8Encoding]::new($false))

# re-register: drop any existing copy first (the module's -Force equivalent)
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
}

if ($Interactive) {
    $out = & schtasks /Create /XML $xmlFile /TN $name 2>&1
} else {
    $cred = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" -Message "Windows password for $name (runs while you are logged off)"
    $out = & schtasks /Create /XML $xmlFile /TN $name /RU $cred.UserName /RP $cred.GetNetworkCredential().Password 2>&1
}
foreach ($line in $out) { Write-Host $line }
if ($LASTEXITCODE -ne 0) { Remove-Item $xmlFile -ErrorAction SilentlyContinue; throw "schtasks /Create failed (exit $LASTEXITCODE)" }
Remove-Item $xmlFile -ErrorAction SilentlyContinue

# verify: the registered task XML must carry the repetition and wake-to-run
$check = (& schtasks /Query /TN $name /XML 2>&1) -join "`n"
foreach ($needle in ("<Repetition>", "<Interval>PT${Every}M</Interval>", "<WakeToRun>true</WakeToRun>")) {
    if ($check -notlike "*$needle*") { throw "registered, but $needle missing - check the task" }
}
$t = Get-ScheduledTask -TaskName $name
Write-Host ("Registered {0}: Mon-Fri {1}-{2} every {3} min, logon {4}, wake to run {5}" -f $name, $Start, $End, $Every, $t.Principal.LogonType, $t.Settings.WakeToRun) -ForegroundColor Green
Write-Host "Run it once now:  Start-ScheduledTask $name   (log: $root\runs\logs\minute_prefetch_task.txt)"
```
### `code/NSEALGOENGINE/run_minute_archive.cmd`
```
@echo off
REM Daily top-up of the 1-minute archive (data\minute_archive): the newest session(s) for every stock, then the
REM liquidity table. Run by the scheduled task "NSEALGOENGINE-MinuteArchive" (Mon-Fri 19:05; register it with
REM deploy\windows\register_minute_archive_task.ps1). Uses the day's Fyers token from NSEUIENGINE's 08:00 login.
REM Log: runs\logs\minute_archive_task.txt
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
if not exist "runs\logs" mkdir "runs\logs"
echo ==== %date% %time% minute archive >> "runs\logs\minute_archive_task.txt"
"%PY%" -m nsealgo fetch-minute-archive --unattended >> "runs\logs\minute_archive_task.txt" 2>&1
set "RC=%errorlevel%"
"%PY%" -m nsealgo liquidity-table >> "runs\logs\minute_archive_task.txt" 2>&1
echo ==== %date% %time% done (fetch exit code %RC%) >> "runs\logs\minute_archive_task.txt"
exit /b %RC%
```
### `code/NSEALGOENGINE/run_minute_prefetch.cmd`
```
@echo off
REM Intraday 1-minute prefetch: fetch TODAY's bars (up to now) into <SYM>\partial_<date>.parquet, so the 15:37
REM ALT-INTRADAY forecast can run without waiting for the 19:05 full archive. Run by the scheduled task
REM "NSEALGOENGINE-MinutePrefetch" (Mon-Fri 09:20-15:35, every 15 min; register with
REM deploy\windows\register_minute_prefetch_task.ps1). Idle-gated: nsealgo waits for the live session to have
REM no open orders before calling the Fyers data API. Light: one small Fyers request per stock, idempotent.
REM Uses the day's Fyers token from NSEUIENGINE's 08:00 login. Log: runs\logs\minute_prefetch_task.txt
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
if not exist "runs\logs" mkdir "runs\logs"
echo ==== %date% %time% minute prefetch >> "runs\logs\minute_prefetch_task.txt"
"%PY%" -m nsealgo prefetch-today --unattended >> "runs\logs\minute_prefetch_task.txt" 2>&1
set "RC=%errorlevel%"
echo ==== %date% %time% done (exit code %RC%) >> "runs\logs\minute_prefetch_task.txt"
exit /b %RC%
```
### `code/NSEALGOENGINE/scripts/windows/morning.ps1`
```
<#
.SYNOPSIS
  Daily routine before 09:00 IST (Windows equivalent of scripts/morning.sh): log in (SEBI daily 2FA),
  verify the adapter, then start the forward test.
    powershell -ExecutionPolicy Bypass -File scripts\windows\morning.ps1 -Broker zerodha
#>
param([string]$Broker = "zerodha")
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$nsealgo = ".\.venv\Scripts\nsealgo.exe"
& $nsealgo login $Broker
if ($LASTEXITCODE -ne 0) { throw "login failed" }
& $nsealgo smoke-test $Broker --symbol SBIN
if ($LASTEXITCODE -ne 0) { throw "smoke test failed" }
& $nsealgo forwardtest --data-broker $Broker
```
### `code/NSEALGOENGINE/scripts/windows/setup.ps1`
```
<#
.SYNOPSIS
  One-time Windows 11 setup for nsealgo. Run from the project root in PowerShell:
    powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
  Options:
    -Extras "dev,zerodha"     pip extras to install (broker SDKs you use; see pyproject.toml)
    -PowerPlan                switch to the Ultimate Performance plan (measure with `nsealgo bench` first/after)
    -Gpu                      PyTorch for the Radeon GPU (AMD ROCm 7.2.1 wheels). Needs Python 3.12 and, first,
                              the "AMD Software: PyTorch on Windows Edition 7.2.1" graphics driver.
    -DefenderDataExclusions   (Administrator) exclude ONLY nsealgo's data folders from real-time scanning.
                              Never exclude python.exe or the virtual environment: that would stop Defender
                              scanning every file any Python program touches, including packages pip installs.
#>
param([string]$Extras = "dev", [switch]$PowerPlan, [switch]$DefenderDataExclusions, [switch]$Gpu)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $root

# 1. Virtual environment (Python 3.10+ via the py launcher)
if (-not (Test-Path ".venv\Scripts\python.exe")) { if ($Gpu) { py -3.12 -m venv .venv } else { py -3 -m venv .venv } }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[$Extras]"
# Some broker SDKs pull in PyPI packages named like standard-library modules ('asyncio' from fyers-apiv3, 'uuid' from
# upstox-python-sdk); they shadow Python's own modules and break things, so they are removed again.
foreach ($shadow in "asyncio", "uuid") {
    & .\.venv\Scripts\python.exe -m pip show $shadow 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { & .\.venv\Scripts\python.exe -m pip uninstall -y -q $shadow }
}

# 1b. Optional: PyTorch on the Radeon 8060S (AMD's Windows wheels, ROCm 7.2.1; cp312 only)
if ($Gpu) {
    $v = & .\.venv\Scripts\python.exe -c "import sys; print('%d.%d' % sys.version_info[:2])"
    if ($v -ne "3.12") { throw "-Gpu needs Python 3.12 (AMD's ROCm wheels are cp312). Install it, delete .venv, and re-run." }
    $r = "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1"
    & .\.venv\Scripts\python.exe -m pip install --no-cache-dir "$r/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl" `
        "$r/rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl" "$r/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl" `
        "$r/rocm-7.2.1.tar.gz"
    & .\.venv\Scripts\python.exe -m pip install --no-cache-dir "$r/torch-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl"
    & .\.venv\Scripts\python.exe -c "import torch; ok = torch.cuda.is_available(); print('PyTorch', torch.__version__, '| ROCm', torch.version.hip, '| GPU:', torch.cuda.get_device_name(0) if ok else 'NOT available - check the AMD PyTorch driver')"
    & .\.venv\Scripts\nsealgo.exe gpu --fresh
}

# 2. UTF-8 mode for this user's Python processes (rupee signs in logs and reports, BOM-free files)
[Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "User")
$env:PYTHONUTF8 = "1"

# 3. Optional: Ultimate Performance power plan (reuses the scheme if it already exists)
if ($PowerPlan) {
    $line = powercfg /list | Select-String "Ultimate Performance" | Select-Object -First 1
    if (-not $line) { $line = powercfg -duplicatescheme e9a42b02-d5df-448d-aa00-03f14749eb61 }
    $guid = ([regex]'[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}').Match("$line").Value
    powercfg /setactive $guid
    Write-Host "Ultimate Performance active ($guid). Revert with: powercfg /setactive SCHEME_BALANCED"
}

# 4. Optional: Defender exclusions for data folders only (needs Administrator; respect your org's policy)
if ($DefenderDataExclusions) {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) { throw "Re-run PowerShell as Administrator to use -DefenderDataExclusions" }
    foreach ($d in @("data", "runs", "recordings", ".cache", "bench_run")) {
        $p = Join-Path $root $d
        New-Item -ItemType Directory -Force -Path $p | Out-Null
        Add-MpPreference -ExclusionPath $p
        Write-Host "Defender exclusion added: $p"
    }
}

# 5. Sanity checks
& .\.venv\Scripts\nsealgo.exe hardware
& .\.venv\Scripts\nsealgo.exe doctor
Write-Host "`nNext: nsealgo demo; nsealgo bench; see docs\WINDOWS.md"
```
### `code/NSEALGOENGINE/scripts/windows/setup_shoonya.ps1`
```
<#
.SYNOPSIS
  Shoonya (Finvasia) as the order broker for nsealgo: credentials, SDK, static-IP check, smoke test, switch-on.

    powershell -ExecutionPolicy Bypass -File scripts\windows\setup_shoonya.ps1              # credentials + checks
    powershell -ExecutionPolicy Bypass -File scripts\windows\setup_shoonya.ps1 -TestOrder   # + a real test order
    powershell -ExecutionPolicy Bypass -File scripts\windows\setup_shoonya.ps1 -TestOrder -Enable

  Before you start (Shoonya website / Prism > API key):
    * generate the API key: you get a vendor code (usually your user id + "_U") and an API secret
    * register your static IP 151.245.182.58 (%STATIC_IP%) for API orders - SEBI rule; orders from any other IP fail
    * enable TOTP on the Shoonya account and keep the TOTP secret (the text behind the QR code)
  Steps
    1. installs the Shoonya SDK (NorenRestApiPy) into .venv
    2. asks for user id, password, TOTP secret, vendor code, API secret -> user environment variables SHOONYA_*
       (config\system.yaml reads them). Skipped with -SkipCredentials.
    3. nsealgo netcheck shoonya   : the order path leaves through the static IP, latency
    4. nsealgo smoke-test shoonya : login, instrument master, quote, history, order book, positions
       -TestOrder adds a 1-share CNC buy limit 10% below the market for SBIN, then cancels it (never fills)
    5. -Enable (only after 4 passed WITH -TestOrder): broker.brokers.shoonya.orders false -> true in system.yaml
  Until step 5, Shoonya stays dormant: `nsealgo live` refuses to route orders to it. Fyers stays the data feed.
#>
param([switch]$TestOrder, [switch]$Enable, [switch]$SkipCredentials)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $root
$py = ".\.venv\Scripts\python.exe"; $ns = ".\.venv\Scripts\nsealgo.exe"
if (-not (Test-Path $py)) { throw "no .venv - run scripts\windows\setup.ps1 first" }

Write-Host "== 1. Shoonya SDK"
& $py -m pip install --disable-pip-version-check -q -e ".[shoonya]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

function Clean([string]$v) { return (($v -replace '[\x00-\x1F\x7F]', '').Trim()) }
function Ask([string]$label, [string]$current, [string]$default = "") {
    $hint = if ($current) { " [Enter = keep current]" } elseif ($default) { " [Enter = $default]" } else { "" }
    $v = Clean (Read-Host "$label$hint")
    if (-not $v) { $v = if ($current) { $current } else { $default } }
    return $v
}
if (-not $SkipCredentials) {
    Write-Host "`n== 2. credentials (typed visibly so paste errors show; the window is not logged)"
    $cur = @{}
    foreach ($k in "SHOONYA_USER", "SHOONYA_PASSWORD", "SHOONYA_TOTP_SECRET", "SHOONYA_VENDOR_CODE", "SHOONYA_API_SECRET") {
        $cur[$k] = [Environment]::GetEnvironmentVariable($k, "User")
    }
    $u = (Ask "Shoonya user id (e.g. FA12345)" $cur.SHOONYA_USER).ToUpper()
    $p = Ask "Shoonya login password" $cur.SHOONYA_PASSWORD
    $t = (Ask "TOTP secret (letters A-Z and 2-7, NOT the 6-digit code)" $cur.SHOONYA_TOTP_SECRET) -replace '\s', ''
    $t = $t.ToUpper()
    if ($t -notmatch '^[A-Z2-7]{16,64}=*$') { throw "that is not a TOTP secret (base32, 16+ characters). The 6-digit code changes every 30 s and cannot be used." }
    $vc = Ask "Vendor code" $cur.SHOONYA_VENDOR_CODE "$($u)_U"
    $as = Ask "API secret (API key)" $cur.SHOONYA_API_SECRET
    if ($p.Length -lt 4 -or $as.Length -lt 8) { throw "password or API secret looks too short - nothing saved" }
    $code = & $py -c "import pyotp,sys; print(pyotp.TOTP(sys.argv[1]).now())" $t
    if ($LASTEXITCODE -ne 0) { throw "the TOTP secret is not valid base32" }
    Write-Host "   TOTP now: $code  (should match your authenticator app right now)"
    $vals = @{ SHOONYA_USER = $u; SHOONYA_PASSWORD = $p; SHOONYA_TOTP_SECRET = $t; SHOONYA_VENDOR_CODE = $vc; SHOONYA_API_SECRET = $as }
    foreach ($k in $vals.Keys) {
        [Environment]::SetEnvironmentVariable($k, $vals[$k], "User")
        Set-Item "env:$k" $vals[$k]
    }
    Write-Host "   saved SHOONYA_USER=$u, SHOONYA_VENDOR_CODE=$vc and the 3 secrets as user environment variables"
}
foreach ($k in "STATICIP_PROXY", "STATIC_IP") {
    if (-not (Get-Item "env:$k" -ErrorAction SilentlyContinue)) {
        $v = [Environment]::GetEnvironmentVariable($k, "User"); if ($v) { Set-Item "env:$k" $v }
    }
}
if (-not $env:STATIC_IP) { Write-Warning "STATIC_IP is not set - run %NSE_ROOT%\code\NSEUIENGINE\SET_STATIC_IP.cmd first" }

Write-Host "`n== 3. order path: static IP + latency"
& $ns netcheck shoonya
$netOk = ($LASTEXITCODE -eq 0)

Write-Host "`n== 4. smoke test against your real Shoonya account"
if ($TestOrder) { & $ns smoke-test shoonya --place-test-order } else { & $ns smoke-test shoonya }
$smokeOk = ($LASTEXITCODE -eq 0)

if ($Enable) {
    if (-not ($netOk -and $smokeOk -and $TestOrder)) {
        Write-Host "`nNOT enabled: -Enable needs netcheck and smoke-test -TestOrder to pass in the same run." -ForegroundColor Yellow
        exit 1
    }
    $cfgPath = Join-Path $root "config\system.yaml"
    $txt = Get-Content $cfgPath -Raw -Encoding UTF8
    $new = [regex]::Replace($txt, '(?s)(\n    shoonya:\s*\{.*?)orders: false\}', '${1}orders: true} ')
    if ($new -eq $txt) { Write-Host "shoonya is already enabled (or the line was edited by hand) - check config\system.yaml" }
    else {
        Copy-Item $cfgPath "$cfgPath.bak_pre_shoonya_enable" -Force
        [IO.File]::WriteAllText($cfgPath, $new, (New-Object Text.UTF8Encoding $false))
        Write-Host "`nENABLED: intraday and delivery orders now go to Shoonya (broker.brokers.shoonya.orders: true)." -ForegroundColor Green
    }
} elseif ($netOk -and $smokeOk) {
    Write-Host "`nAll checks passed. Next: run again with -TestOrder -Enable to switch Shoonya on for orders." -ForegroundColor Green
} else {
    Write-Host "`nSome checks failed - see FAIL lines above. Shoonya stays dormant." -ForegroundColor Yellow
}
```
### `code/NSEDATAENGINE/migrate/disable_old_pc_tasks.ps1`
```
<#
.SYNOPSIS
    OLD PC, switch-over day: disables every NSE scheduled task here (the new PC takes over), after saving each task
    as XML in <Root>\_move\old_pc_tasks_xml so it can be put back.

        powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEDATAENGINE\migrate\disable_old_pc_tasks.ps1
        ... -Undo        enables them again (if the new PC is not ready after all)

    Run it BEFORE the new PC registers its tasks (setup_new_pc.ps1 -TasksOnly): two PCs must never both run the
    swing trader (real orders) or both download / forecast into the same day.
#>
[CmdletBinding()]
param([string]$Root = '%NSE_ROOT%', [switch]$Undo)
$ErrorActionPreference = 'Stop'
# -cmatch, anchored: plain -match 'NSE' also hits Windows' own tasks (LicENSE..., StorageSENSE, ConSENt...)
$tasks = @(Get-ScheduledTask | Where-Object { $_.TaskName -cmatch '^(NSE|nsealgo)' })
if (-not $tasks.Count) { Write-Host 'No NSE scheduled tasks on this PC.'; return }
if ($Undo) {
    $tasks | ForEach-Object { Enable-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath | Out-Null; Write-Host "  enabled  $($_.TaskName)" }
    return
}
$xml = Join-Path $Root '_move\old_pc_tasks_xml'
New-Item -ItemType Directory -Force -Path $xml | Out-Null
foreach ($t in $tasks) {
    Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath | Set-Content -Path (Join-Path $xml "$($t.TaskName).xml") -Encoding Unicode
    if ($t.State -eq 'Running') { Stop-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath }
    Disable-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath | Out-Null
    Write-Host "  disabled $($t.TaskName)"
}
Write-Host ("`n{0} task(s) disabled; copies in {1}. Undo: this script with -Undo" -f $tasks.Count, $xml) -ForegroundColor Green
```
### `code/NSEDATAENGINE/migrate/export_settings.ps1`
```
<#
.SYNOPSIS
    OLD PC: saves your user environment variables (broker logins, static-IP proxy, settings) and the list of NSE
    scheduled tasks into <Root>\_move, so robocopy carries them to the new PC and setup_new_pc.ps1 restores them.

        powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEDATAENGINE\migrate\export_settings.ps1

    Saved: every USER variable except Path / TEMP / TMP / OneDrive and the daily Fyers token values (they expire).
    Values that point into the old folder (%NSE_ROOT%\data\...) are moved to the new folder on restore.
    _move\user_env.json holds credentials (Shoonya, Fyers, proxy) in plain text - the same values that sit in your
    user variables today. Delete the _move folder on BOTH PCs once the new PC is set up.
#>
[CmdletBinding()]
param([string]$Root = '%NSE_ROOT%')
$ErrorActionPreference = 'Stop'
$out = Join-Path $Root '_move'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$skip = @('Path', 'TEMP', 'TMP', 'FYERS_ACCESS_TOKEN', 'FYERS_WS_TOKEN', 'FYERS_TOKEN_DATE', 'FYERS_TOKEN_ISSUED_AT')
$vars = [Environment]::GetEnvironmentVariables('User')
$saved = [ordered]@{ '_old_root' = $Root; '_old_pc' = $env:COMPUTERNAME; '_saved' = (Get-Date -Format s) }
foreach ($k in ($vars.Keys | Sort-Object)) {
    if ($skip -contains $k -or $k -like 'OneDrive*') { continue }
    $saved[$k] = [string]$vars[$k]
}
$f = Join-Path $out 'user_env.json'
ConvertTo-Json -InputObject $saved | Set-Content -Path $f -Encoding UTF8
Write-Host ("Saved {0} user variables -> {1}" -f ($saved.Count - 3), $f) -ForegroundColor Green
($saved.Keys | Where-Object { -not $_.StartsWith('_') }) | ForEach-Object { Write-Host "  $_" }

# -cmatch, anchored: plain -match 'NSE' also hits Windows' own tasks (LicENSE..., StorageSENSE, ConSENt...)
$tasks = Get-ScheduledTask | Where-Object { $_.TaskName -cmatch '^(NSE|nsealgo)' }
$rows = foreach ($t in $tasks) {
    $i = $t | Get-ScheduledTaskInfo
    [pscustomobject]@{ TaskName = $t.TaskName; State = $t.State; LastRun = $i.LastRunTime; LastResult = $i.LastTaskResult;
                       NextRun = $i.NextRunTime; Action = (($t.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join ' | ') }
}
$rows | Export-Csv -NoTypeInformation -Path (Join-Path $out 'old_pc_tasks.csv')
Write-Host ("`n{0} NSE scheduled tasks listed -> {1}" -f @($rows).Count, (Join-Path $out 'old_pc_tasks.csv')) -ForegroundColor Green
$rows | Format-Table TaskName, State, LastRun, LastResult -AutoSize
```
### `code/NSEDATAENGINE/migrate/import_old_pc_tasks.ps1`
```
<#
.SYNOPSIS
    NEW PC (PX13): registers the NSE scheduled tasks from the copies saved on the old PC
    (<Root>\_move\old_pc_tasks_xml, written by disable_old_pc_tasks.ps1), with the old folder (%NSE_ROOT%) moved to
    <Root> and your Windows account on this PC. Every task runs whether you are logged on or not (Password logon,
    Wake to run, run as soon as possible after a missed start) - the same settings that worked on the old PC.

        powershell -ExecutionPolicy Bypass -File D:\NSE\NSEDATAENGINE\migrate\import_old_pc_tasks.ps1

    Asks for your Windows password once (for a Microsoft account: the Microsoft account password, not the PIN).
    Tasks that already exist on this PC are kept unless you add -Replace.

    -Only NSEUIENGINE-FyersToken,NSEUIENGINE-Trader-Morning    only these tasks
    -Replace                                                    also replace tasks already registered here
    -DryRun                                                     show what would be registered, change nothing

    Log (full error text): %USERPROFILE%\AlgoSetup\import_tasks_<date>.log

    Why this exists: on the PX13 (23 Oct 2026) setup_new_pc.ps1 -TasksOnly could not register the tasks that
    use "run only when logged on" (NSEMLENGINE-*, NSEUIENGINE-*), while the password-logon ones worked. The old
    PC's own task definitions are known-good, so they are re-used here.
#>
[CmdletBinding()]
param(
    [string]$Root = 'D:\NSE',
    [string]$OldRoot = '%NSE_ROOT%',
    [string]$XmlDir,
    [string[]]$Only = @(),
    [switch]$Replace,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
if (-not $XmlDir) { $XmlDir = Join-Path $Root '_move\old_pc_tasks_xml' }
$logDir = Join-Path $env:USERPROFILE 'AlgoSetup'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ('import_tasks_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.log')
function Log([string]$m, [string]$c = 'Gray') {
    Write-Host $m -ForegroundColor $c
    Add-Content -Path $log -Value ("{0}  {1}" -f (Get-Date -Format s), $m)
}

Log "=== NSE scheduled tasks from the old PC -> $env:COMPUTERNAME" 'Cyan'
Log "log: $log"

# ---- clock: every task fires by this PC's clock -------------------------------------------------------
$tz = Get-TimeZone
Log ("clock now: {0}   time zone: {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $tz.Id)
if ($tz.Id -ne 'India Standard Time') {
    Log "WARNING: the time zone is not India Standard Time - the tasks would fire at the wrong time." 'Yellow'
    Log "         Fix: Settings > Time & language > Date & time > Time zone = (UTC+05:30) Chennai, Kolkata, Mumbai, New Delhi" 'Yellow'
}

# ---- which tasks ------------------------------------------------------------------------------------
if (-not (Test-Path $XmlDir)) { throw "not found: $XmlDir (copy %NSE_ROOT%\data\_move from the old PC first)" }
# -cmatch (case-sensitive): plain -match 'NSE' also matches Windows' own tasks (LicENSE..., StorageSENSE, ConSENt...)
$files = @(Get-ChildItem -Path $XmlDir -Filter '*.xml' | Where-Object { $_.BaseName -cmatch '^(NSE|nsealgo)' } | Sort-Object BaseName)
if ($Only.Count) { $files = @($files | Where-Object { $Only -contains $_.BaseName }) }
if (-not $files.Count) { throw "no NSE task files in $XmlDir" }

$me = [Security.Principal.WindowsIdentity]::GetCurrent()
$sid = $me.User.Value
$account = $me.Name
Log "account: $account ($sid)"
$oldPat = [regex]::Escape($OldRoot.TrimEnd('\') + '\')
$newDir = $Root.TrimEnd('\') + '\'

# ---- build the new definitions first (nothing changes until the password is entered) -------------------
$plan = @()
foreach ($f in $files) {
    $name = $f.BaseName
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($existing -and -not $Replace) {
        Log ("  keep      {0,-38} already on this PC ({1}, {2}) - add -Replace to overwrite" -f $name, $existing.State, $existing.Principal.LogonType)
        continue
    }
    $xml = [IO.File]::ReadAllText($f.FullName)                              # UTF-16 with BOM
    $xml = $xml -ireplace $oldPat, $newDir                                  # %NSE_ROOT%\data\... -> D:\NSE\...
    $xml = [regex]::Replace($xml, '(<Principal id="Author">\s*<UserId>)[^<]*', ('${1}' + $sid))
    $xml = [regex]::Replace($xml, '(<LogonTrigger>(?:(?!</LogonTrigger>).)*?<UserId>)[^<]*', ('${1}' + $account),
        [Text.RegularExpressions.RegexOptions]::Singleline)
    $xml = $xml -replace '<LogonType>[^<]*</LogonType>', '<LogonType>Password</LogonType>'
    # the files the task runs must exist on this PC
    $missing = @()
    foreach ($m in [regex]::Matches($xml, '[A-Za-z]:\\[^"<>|&]*?\.(exe|bat|cmd|py)\b')) {
        $p = $m.Value
        if ($p -like 'C:\Windows*') { continue }
        if (-not (Test-Path $p)) { $missing += $p }
    }
    if ($xml -match [regex]::Escape($OldRoot)) { $missing += "(still points at $OldRoot)" }
    $plan += [pscustomobject]@{ Name = $name; Xml = $xml; Missing = ($missing | Select-Object -Unique) }
    $tag = if ($existing) { 'replace' } else { 'add' }
    Log ("  {0,-9} {1}" -f $tag, $name)
    foreach ($x in $plan[-1].Missing) { Log "            WARNING file not found: $x" 'Yellow' }
}
if (-not $plan.Count) { Log "`nNothing to register." 'Green'; return }
if ($DryRun) { Log "`n-DryRun: nothing registered." 'Yellow'; return }

# ---- register ---------------------------------------------------------------------------------------
$cred = Get-Credential -UserName $account -Message "Windows password for the NSE scheduled tasks (they run while you are logged off)"
if (-not $cred) { throw 'no password entered - nothing registered' }
$pw = $cred.GetNetworkCredential().Password
$ok = 0; $bad = 0
foreach ($t in $plan) {
    try {
        Register-ScheduledTask -TaskName $t.Name -Xml $t.Xml -User $account -Password $pw -Force | Out-Null
        $s = (Get-ScheduledTask -TaskName $t.Name).Settings
        if (-not $s.WakeToRun -or -not $s.StartWhenAvailable) {
            $s.WakeToRun = $true; $s.StartWhenAvailable = $true
            Set-ScheduledTask -TaskName $t.Name -Settings $s -User $account -Password $pw | Out-Null
        }
        Log ("  OK    {0}" -f $t.Name) 'Green'; $ok++
    } catch {
        $bad++
        Log ("  FAIL  {0}: {1}" -f $t.Name, $_.Exception.Message) 'Red'
        Add-Content -Path $log -Value ("        " + ($_ | Out-String))
        if ($_.Exception.Message -match 'password|logon failure|user name') {
            Log "  Stopping: wrong Windows password (Microsoft account: use its password, not the PIN). Run again." 'Red'
            break
        }
    }
}
$pw = $null

# ---- summary ------------------------------------------------------------------------------------------
Log "`n$ok registered, $bad failed." $(if ($bad) { 'Red' } else { 'Green' })
$rows = Get-ScheduledTask | Where-Object { $_.TaskName -cmatch '^(NSE|nsealgo)' } | Sort-Object TaskName | ForEach-Object {
    $i = $_ | Get-ScheduledTaskInfo
    [pscustomobject]@{ Task = $_.TaskName; State = $_.State; Logon = $_.Principal.LogonType; NextRun = $i.NextRunTime }
}
$rows | Format-Table -AutoSize | Out-String -Width 160 | ForEach-Object { Log $_ }
Log "Fyers token now (after its credentials are fixed):  Start-ScheduledTask NSEUIENGINE-FyersToken"
if ($bad) { exit 1 }
```
### `code/NSEDATAENGINE/migrate/keep_wifi_on.ps1`
```
<#
.SYNOPSIS
    Stops Windows from switching the Wi-Fi card off to save power (asks for Administrator rights).
    For the trading PC (PX13): the 08:00 token, 08:58 pre-open / swing trader and the evening tasks need the
    network the moment they start, including right after the PC wakes from sleep.

        powershell -ExecutionPolicy Bypass -File D:\NSE\NSEDATAENGINE\migrate\keep_wifi_on.ps1

    For every Wi-Fi adapter:
      1. Device Manager > Power Management: untick "Allow the computer to turn off this device to save power"
         (NetAdapter power setting, PnPCapabilities = 24 in the registry, and the WMI switch behind the tick box)
      2. Power plan > Wireless Adapter Settings > Power Saving Mode = Maximum Performance (plugged in and battery)
      3. Modern Standby PCs: "Networking connectivity in Standby" = Enable, so Wi-Fi stays up in sleep and
         scheduled tasks that wake the PC find the network ready
    Then restarts the Wi-Fi adapter once (a few seconds offline) so it takes effect without a reboot, and shows
    the result. Safe to run again any time.

    -NoRestart      don't restart the adapter (the change then takes effect at the next reboot)
    -Register       also add the scheduled task "PX13-KeepWifiOn" (SYSTEM, at every startup) that re-applies this:
                    Wi-Fi driver updates from Windows Update reset the Power Management tick box
    -PluggedInOnly  change the power plan for plugged-in only (default: plugged in and on battery)

    Log: %ProgramData%\KeepWifiOn\keep_wifi_on.log
#>
[CmdletBinding()]
param(
    [switch]$NoRestart,
    [switch]$Register,
    [switch]$PluggedInOnly,
    [switch]$Quiet
)

# ---- Administrator ------------------------------------------------------------------------------------
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host "Asking for Administrator rights (device power settings) ..."
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    if ($NoRestart) { $a += '-NoRestart' }
    if ($Register) { $a += '-Register' }
    if ($PluggedInOnly) { $a += '-PluggedInOnly' }
    $p = Start-Process powershell -Verb RunAs -Wait -PassThru -ArgumentList $a
    exit $p.ExitCode
}

$ErrorActionPreference = 'Stop'
$logDir = Join-Path $env:ProgramData 'KeepWifiOn'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'keep_wifi_on.log'
function Log([string]$m, [string]$c = 'Gray') {
    if (-not $Quiet) { Write-Host $m -ForegroundColor $c }
    Add-Content -Path $log -Value ("{0}  {1}" -f (Get-Date -Format s), $m)
}
$problems = 0
function Try-Step([string]$what, [scriptblock]$do, [switch]$Optional) {
    try { & $do; Log "    ok    $what" 'Green' }
    catch {
        if ($Optional) { Log "    skip  $what : $($_.Exception.Message)" 'Yellow' }
        else { $script:problems++; Log "    FAIL  $what : $($_.Exception.Message)" 'Red' }
    }
}

Log "=== keep Wi-Fi on  ($env:COMPUTERNAME)" 'Cyan'

# ---- 1. the Wi-Fi adapters -----------------------------------------------------------------------------
# NdisPhysicalMedium 9 = Native 802.11 (Wi-Fi); Bluetooth PAN and virtual adapters are left alone
$wifi = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object {
        $_.NdisPhysicalMedium -eq 9 -or $_.PhysicalMediaType -match '802\.11' })
if (-not $wifi.Count) { Log "No Wi-Fi adapter found (Get-NetAdapter -Physical). Nothing to do for the card." 'Yellow' }

$classKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}'
foreach ($ad in $wifi) {
    Log "`n[1] $($ad.Name): $($ad.InterfaceDescription)  (status $($ad.Status))" 'Cyan'

    Try-Step 'NetAdapter power management: computer may not turn the device off' {
        Set-NetAdapterPowerManagement -Name $ad.Name -AllowComputerToTurnOffDevice Disabled -NoRestart
    } -Optional

    Try-Step 'registry PnPCapabilities = 24 (Power Management tick box off)' {
        $guid = $ad.InterfaceGuid
        $key = Get-ChildItem $classKey -ErrorAction SilentlyContinue | Where-Object {
            (Get-ItemProperty $_.PSPath -Name NetCfgInstanceId -ErrorAction SilentlyContinue).NetCfgInstanceId -eq $guid
        } | Select-Object -First 1
        if (-not $key) { throw "adapter $guid not found under the network class key" }
        Set-ItemProperty -Path $key.PSPath -Name PnPCapabilities -Value 24 -Type DWord
    }

    Try-Step 'WMI MSPower_DeviceEnable = off (the same tick box)' {
        $pnp = $ad.PnPDeviceID
        $w = @(Get-CimInstance -Namespace root\wmi -ClassName MSPower_DeviceEnable -ErrorAction SilentlyContinue |
                Where-Object { $_.InstanceName -like "$pnp*" })
        if (-not $w.Count) { Log "          (the driver does not offer this switch - the registry value above covers it)"; return }
        foreach ($i in $w) { if ($i.Enable) { Set-CimInstance -InputObject $i -Property @{ Enable = $false } } }
    } -Optional

    # driver "advanced" properties differ per vendor (MediaTek, Intel, Qualcomm): shown, not changed
    $adv = @(Get-NetAdapterAdvancedProperty -Name $ad.Name -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -match 'power|sav|sleep|MIMO|U-APSD|idle' })
    foreach ($x in $adv) { Log ("          driver setting  {0,-40} = {1}" -f $x.DisplayName, $x.DisplayValue) }
}

# ---- 2. power plan: Wireless Adapter Settings ----------------------------------------------------------
Log "`n[2] Power plan: Wireless Adapter Settings > Power Saving Mode = Maximum Performance" 'Cyan'
$subWifi = '19cbb8fa-5279-450e-9fac-8a3d5fedd0c1'; $setWifi = '12bbebe6-58d6-4636-95bb-3217ef867c1a'
Try-Step 'plugged in' {
    & powercfg /setacvalueindex SCHEME_CURRENT $subWifi $setWifi 0; if ($LASTEXITCODE) { throw "powercfg exit $LASTEXITCODE" }
}
if (-not $PluggedInOnly) {
    Try-Step 'on battery' {
        & powercfg /setdcvalueindex SCHEME_CURRENT $subWifi $setWifi 0; if ($LASTEXITCODE) { throw "powercfg exit $LASTEXITCODE" }
    }
}

# ---- 3. Modern Standby: keep the network up while asleep -------------------------------------------------
$standby = (& powercfg /a) -join "`n"
if ($standby -match 'S0 Low Power Idle') {
    Log "`n[3] Modern Standby PC: Networking connectivity in Standby = Enable" 'Cyan'
    $subNone = 'fea3413e-7e05-4911-9a71-700331f1c294'; $setNet = 'f15576e8-98b7-4186-b944-eafa664402d9'
    & powercfg -attributes $subNone $setNet -ATTRIB_HIDE 2>$null | Out-Null       # show it in Power Options too
    Try-Step 'plugged in' {
        & powercfg /setacvalueindex SCHEME_CURRENT $subNone $setNet 1; if ($LASTEXITCODE) { throw "powercfg exit $LASTEXITCODE" }
    }
    if (-not $PluggedInOnly) {
        Try-Step 'on battery' {
            & powercfg /setdcvalueindex SCHEME_CURRENT $subNone $setNet 1; if ($LASTEXITCODE) { throw "powercfg exit $LASTEXITCODE" }
        }
    }
} else {
    Log "`n[3] Not a Modern Standby PC - skipped (S3 sleep turns Wi-Fi off; Wake to run brings it back)" 'Cyan'
}
& powercfg /setactive SCHEME_CURRENT | Out-Null

# ---- 4. take effect now --------------------------------------------------------------------------------
if ($wifi.Count -and -not $NoRestart) {
    Log "`n[4] Restarting the Wi-Fi adapter (a few seconds offline) ..." 'Cyan'
    foreach ($ad in $wifi) { Try-Step "restart $($ad.Name)" { Restart-NetAdapter -Name $ad.Name -Confirm:$false } }
    Start-Sleep -Seconds 10
} elseif ($wifi.Count) {
    Log "`n[4] -NoRestart: the Power Management change applies at the next reboot" 'Yellow'
}

# ---- 5. scheduled task that re-applies it at startup -----------------------------------------------------
if ($Register) {
    Log "`n[5] Scheduled task PX13-KeepWifiOn (SYSTEM, at startup)" 'Cyan'
    Try-Step 'register' {
        $act = New-ScheduledTaskAction -Execute 'powershell.exe' `
            -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -NoRestart -Quiet"
        $trg = New-ScheduledTaskTrigger -AtStartup
        $prn = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
        $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
        Register-ScheduledTask -TaskName 'PX13-KeepWifiOn' -Action $act -Trigger $trg -Principal $prn -Settings $set `
            -Description 'Re-applies keep_wifi_on.ps1 (Wi-Fi power saving off) after driver updates' -Force | Out-Null
    }
}

# ---- result -----------------------------------------------------------------------------------------------
Log "`n=== Result" 'Cyan'
foreach ($ad in $wifi) {
    $pm = Get-NetAdapterPowerManagement -Name $ad.Name -ErrorAction SilentlyContinue
    $now = Get-NetAdapter -Name $ad.Name
    Log ("  {0}: status {1}, link {2}, computer may turn it off: {3}" -f $ad.Name, $now.Status, $now.LinkSpeed,
        $(if ($pm) { $pm.AllowComputerToTurnOffDevice } else { 'unknown' }))
}
$q = (& powercfg /query SCHEME_CURRENT $subWifi $setWifi) -join "`n"
if ($q -match 'Current AC Power Setting Index:\s*(0x[0-9a-f]+)') { Log "  Wi-Fi power saving (plugged in): $($Matches[1])  (0x00000000 = Maximum Performance)" }
if ($problems) { Log "$problems step(s) failed - see above / $log" 'Red'; exit 1 }
Log 'Done.' 'Green'
if (-not $Quiet -and $Host.Name -eq 'ConsoleHost') { Start-Sleep -Seconds 5 }
exit 0
```
### `code/NSEDATAENGINE/migrate/move_to_shared_data.ps1`
```
<#
.SYNOPSIS
  Moves the data of all four engines into ONE shared data area, one folder per engine (option A):

      C:\NSE\DATA\NSEDATAENGINE   data, data_fresh, data_fresh_2019, data_ref, backtest_results, portfolio_results, signals
      C:\NSE\DATA\NSEMLENGINE     (was NSEMLENGINE\work: dataset, models, predictions, results, logs)
      C:\NSE\DATA\NSEALGOENGINE   data, runs, .cache, recordings (created when nsealgo first runs)
      C:\NSE\DATA\NSEUIENGINE     state (incl. the encrypted secrets and the trader's state), reports, logs
      C:\NSE\DATA\_archive        NSEDATAENGINE\backup_pre_contract_20261003 + the old data files in %NSE_ROOT%
      C:\NSE\DATA\_migration      journal of every move (for -Rollback)

  Code stays where it is. Every engine finds its folder through environment variables (set here):
  NSE_DATA_ROOT, NSEDATAENGINE_*_DIR, NSEMLENGINE_WORK_DIR, NSEALGOENGINE_DATA_DIR, NSEUIENGINE_DATA_DIR.

.USAGE
  powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEDATAENGINE\migrate\move_to_shared_data.ps1            # plan only
  powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEDATAENGINE\migrate\move_to_shared_data.ps1 -Execute   # move
  powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEDATAENGINE\migrate\move_to_shared_data.ps1 -Rollback  # undo

  Folders are MOVED (renamed - instant, no extra space, when C:\NSE is on the same drive as %NSE_ROOT%), each one
  checked (file count + bytes) and journalled. Refuses to start while an NSE scheduled task or an engine's
  Python process is running. Run it after 22:30 on a weekday or at the weekend; close Excel / LibreOffice / IDEs
  that have files of these folders open.
#>
param(
    [string]$Root = "C:\NSE\DATA",
    [string]$Base = "%NSE_ROOT%",
    [switch]$Execute,
    [switch]$Rollback
)
$ErrorActionPreference = "Stop"
$JDir = Join-Path $Root "_migration"

function Get-Stats([string]$p) {
    if (-not (Test-Path -LiteralPath $p)) { return $null }
    $it = Get-Item -LiteralPath $p -Force
    if (-not $it.PSIsContainer) { return [pscustomobject]@{ Files = 1; Bytes = [int64]$it.Length } }
    $f = @(Get-ChildItem -LiteralPath $p -Recurse -Force -File -ErrorAction SilentlyContinue)
    $b = ($f | Measure-Object -Property Length -Sum).Sum
    return [pscustomobject]@{ Files = $f.Count; Bytes = [int64]$(if ($b) { $b } else { 0 }) }
}
function Fmt([int64]$b) { if ($b -ge 1GB) { "{0:N1} GB" -f ($b / 1GB) } elseif ($b -ge 1MB) { "{0:N0} MB" -f ($b / 1MB) } else { "{0:N0} KB" -f ($b / 1KB) } }

# ---------------------------------------------------------------------------------------------- rollback
if ($Rollback) {
    $jfile = Get-ChildItem (Join-Path $JDir "migration_*.json") -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1
    if (-not $jfile) { throw "no migration journal in $JDir" }
    $J = Get-Content $jfile.FullName -Raw | ConvertFrom-Json
    Write-Host "Rolling back $($jfile.Name)"
    $done = @($J.moves | Where-Object { $_.done })
    [array]::Reverse($done)
    foreach ($m in $done) {
        if (Test-Path -LiteralPath $m.src) { Write-Warning "skip (source exists again): $($m.src)"; continue }
        $parent = Split-Path -Parent $m.src
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        Move-Item -LiteralPath $m.dst -Destination $m.src
        Write-Host "  back: $($m.dst) -> $($m.src)"
    }
    foreach ($p in $J.env.PSObject.Properties) {
        $old = $p.Value.old
        [Environment]::SetEnvironmentVariable($p.Name, $(if ($old) { $old } else { $null }), "User")
        Write-Host ("  env {0} = {1}" -f $p.Name, $(if ($old) { $old } else { "<removed>" }))
    }
    $reg = Join-Path $Base "NSEUIENGINE\register_eod_tasks.ps1"
    if (Test-Path $reg) { Remove-Item Env:NSE_DATA_ROOT, Env:NSEUIENGINE_DATA_DIR -ErrorAction SilentlyContinue; & $reg | Out-Null }
    Write-Host "Rollback finished. Open a new terminal before running anything." -ForegroundColor Green
    exit 0
}

# ---------------------------------------------------------------------------------------------- plan
$moves = New-Object System.Collections.ArrayList
function Add-Move($eng, $src, $dst) { [void]$moves.Add([pscustomobject]@{ engine = $eng; src = $src; dst = $dst; files = 0; bytes = 0; done = $false }) }
foreach ($d in "data", "data_fresh", "data_fresh_2019", "data_ref", "backtest_results", "portfolio_results", "signals") {
    Add-Move "NSEDATAENGINE" "$Base\NSEDATAENGINE\$d" "$Root\NSEDATAENGINE\$d"
}
Add-Move "NSEMLENGINE" "$Base\NSEMLENGINE\work" "$Root\NSEMLENGINE"
foreach ($d in "data", "runs", ".cache", "recordings") { Add-Move "NSEALGOENGINE" "$Base\NSEALGOENGINE\$d" "$Root\NSEALGOENGINE\$d" }
foreach ($d in "state", "reports", "logs") { Add-Move "NSEUIENGINE" "$Base\NSEUIENGINE\$d" "$Root\NSEUIENGINE\$d" }
Add-Move "_archive" "$Base\NSEDATAENGINE\backup_pre_contract_20261003" "$Root\_archive\NSEDATAENGINE\backup_pre_contract_20261003"
Get-ChildItem -LiteralPath $Base -File -Force | Where-Object { $_.Extension -in ".parquet", ".zip" -or $_.Name -eq "data_quality_report.csv" } |
    ForEach-Object { Add-Move "_archive" $_.FullName "$Root\_archive\temp_root\$($_.Name)" }

$envs = [ordered]@{
    "NSE_DATA_ROOT"               = $Root
    "NSEDATAENGINE_DATA_DIR"      = "$Root\NSEDATAENGINE\data"
    "NSEDATAENGINE_EVENTS_DIR"    = "$Root\NSEDATAENGINE\data\events"
    "NSEDATAENGINE_FRESH_DIR"     = "$Root\NSEDATAENGINE\data_fresh"
    "NSEDATAENGINE_FRESH2019_DIR" = "$Root\NSEDATAENGINE\data_fresh_2019"
    "NSEDATAENGINE_REF_DIR"       = "$Root\NSEDATAENGINE\data_ref"
    "NSEDATAENGINE_BACKTEST_DIR"  = "$Root\NSEDATAENGINE\backtest_results"
    "NSEDATAENGINE_PORTFOLIO_DIR" = "$Root\NSEDATAENGINE\portfolio_results"
    "NSEDATAENGINE_SIGNALS_DIR"   = "$Root\NSEDATAENGINE\signals"
    "NSEMLENGINE_WORK_DIR"        = "$Root\NSEMLENGINE"
    "NSEALGOENGINE_DATA_DIR"      = "$Root\NSEALGOENGINE"
    "NSEUIENGINE_DATA_DIR"        = "$Root\NSEUIENGINE"
}

Write-Host "`nShared data area: $Root   (code stays in $Base)`n"
$total = [int64]0
$problems = @()
foreach ($m in $moves) {
    $s = Get-Stats $m.src
    if (-not $s) { $m.files = -1; continue }
    $m.files = $s.Files; $m.bytes = $s.Bytes; $total += $s.Bytes
    if (Test-Path -LiteralPath $m.dst) {
        $ds = Get-Stats $m.dst
        if ($ds.Files -gt 0) { $problems += "destination already has files: $($m.dst)" }
    }
}
Write-Host ("{0,-14} {1,9} {2,9}  {3}" -f "Engine", "Files", "Size", "From  ->  To")
foreach ($m in ($moves | Where-Object { $_.files -ge 0 })) {
    Write-Host ("{0,-14} {1,9} {2,9}  {3}  ->  {4}" -f $m.engine, $m.files, (Fmt $m.bytes), $m.src, $m.dst)
}
$skipped = @($moves | Where-Object { $_.files -lt 0 } | ForEach-Object { $_.src })
if ($skipped) { Write-Host ("Not present (nothing to move): " + ($skipped -join ", ")) }
Write-Host ("Total: {0}" -f (Fmt $total))
Write-Host "`nUser environment variables:"
foreach ($k in $envs.Keys) {
    $old = [Environment]::GetEnvironmentVariable($k, "User")
    Write-Host ("  {0,-28} {1}  ->  {2}" -f $k, $(if ($old) { $old } else { "<not set>" }), $envs[$k])
}

# safety checks
$sameVol = ([IO.Path]::GetPathRoot($Root)).ToUpper() -eq ([IO.Path]::GetPathRoot($Base)).ToUpper()
if (-not $sameVol) { Write-Warning "$Root is on another drive: folders will be COPIED then deleted (slow, needs $(Fmt $total) free)" }
$running = @()
if (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue) {
    $running = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match "NSE|nsealgo" -and $_.State -eq "Running" })
}
if ($running) { $problems += "scheduled task(s) running: " + (($running | ForEach-Object TaskName) -join ", ") }
$procs = @()
if (Get-Command Get-CimInstance -ErrorAction SilentlyContinue) {
    $procs = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "NSE(DATA|ML|UI|ALGO)ENGINE|nseml|nsealgo" })
}
if ($procs) { $problems += "engine Python process(es) running: " + (($procs | ForEach-Object { "$($_.ProcessId)" }) -join ", ") }
$locks = @($moves | Where-Object { $_.files -gt 0 -and (Get-Item -LiteralPath $_.src).PSIsContainer } |
    ForEach-Object { Get-ChildItem -LiteralPath $_.src -Recurse -Force -Filter ".~lock*" -ErrorAction SilentlyContinue })
if ($locks) { Write-Warning ("LibreOffice lock files found (a file may be open): " + (($locks | ForEach-Object FullName) -join ", ")) }

if ($problems) {
    Write-Host "`nCannot move now:" -ForegroundColor Red
    $problems | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    if ($Execute) { exit 1 }
}
if (-not $Execute) { Write-Host "`nPlan only. Run again with -Execute to move." -ForegroundColor Yellow; exit 0 }

# ---------------------------------------------------------------------------------------------- execute
New-Item -ItemType Directory -Force -Path $JDir | Out-Null
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$jpath = Join-Path $JDir "migration_$ts.json"
$envJ = [ordered]@{}
foreach ($k in $envs.Keys) { $envJ[$k] = @{ old = [Environment]::GetEnvironmentVariable($k, "User"); new = $envs[$k] } }
function Save-Journal { @{ when = $ts; root = $Root; base = $Base; moves = $moves; env = $envJ } | ConvertTo-Json -Depth 5 | Set-Content $jpath -Encoding UTF8 }
Save-Journal

foreach ($m in $moves) {
    if ($m.files -lt 0) { continue }
    $parent = Split-Path -Parent $m.dst
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    if ((Test-Path -LiteralPath $m.dst) -and (Get-Item -LiteralPath $m.dst).PSIsContainer) { Remove-Item -LiteralPath $m.dst -Force }  # empty
    try {
        Move-Item -LiteralPath $m.src -Destination $m.dst
    } catch {
        Save-Journal
        Write-Host "`nFAILED moving $($m.src): $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "Nothing else was moved after this. Fix the cause (open file?) and run -Execute again, or -Rollback." -ForegroundColor Red
        exit 1
    }
    $s = Get-Stats $m.dst
    if ($s.Files -ne $m.files -or $s.Bytes -ne $m.bytes) {
        Save-Journal
        throw "verification failed for $($m.dst): $($s.Files) files / $($s.Bytes) bytes, expected $($m.files) / $($m.bytes)"
    }
    $m.done = $true
    Save-Journal
    Write-Host ("  moved  {0,-60} {1,8} files  {2,9}" -f $m.dst, $m.files, (Fmt $m.bytes))
}
foreach ($d in "NSEDATAENGINE", "NSEMLENGINE", "NSEALGOENGINE", "NSEUIENGINE") {
    New-Item -ItemType Directory -Force -Path (Join-Path $Root $d) | Out-Null
}
foreach ($k in $envs.Keys) {
    [Environment]::SetEnvironmentVariable($k, $envs[$k], "User")
    Set-Item -Path "Env:$k" -Value $envs[$k]
}
Write-Host "`nEnvironment variables set." -ForegroundColor Green

# the one task whose command line contains a data path
$reg = Join-Path $Base "NSEUIENGINE\register_eod_tasks.ps1"
if (Test-Path $reg) { & $reg | Out-Null; Write-Host "Re-registered NSEUIENGINE-SignalLog (log now in $Root\NSEUIENGINE\logs)" }

# checks
Write-Host "`n== NSEDATAENGINE: python nse_contract.py paths"
Push-Location "$Base\NSEDATAENGINE"; & python nse_contract.py paths; Pop-Location
Write-Host "`n== NSEMLENGINE work folder"
$mlpy = if ($env:NSEMLENGINE_PYTHON) { $env:NSEMLENGINE_PYTHON } else { "python" }
& $mlpy -c "import sys; sys.path.insert(0, r'$Base\NSEMLENGINE'); import nseml.config as c; print(c.work_dir())"
$uipy = "$Base\NSEUIENGINE\.venv\Scripts\python.exe"
if (Test-Path $uipy) {
    Write-Host "`n== NSEUIENGINE"
    & $uipy -c "import sys; sys.path.insert(0, r'$Base\NSEUIENGINE'); import swing_trader as T; print('state  ', T.STATE_DIR); print('journal', T.JOURNAL_FILE); print('signals', T.signals_dir(T.load_config()))"
    & $uipy "$Base\NSEUIENGINE\fyers_token_manager.py" --status
}
Write-Host "`nDone. Journal: $jpath" -ForegroundColor Green
Write-Host "Open a NEW terminal / restart IDEs so they see the new variables. Undo: ...\move_to_shared_data.ps1 -Rollback"
```
### `code/NSEDATAENGINE/migrate/pack_project.ps1`
```
<#
.SYNOPSIS
    OLD PC: packs every registered project into a migration bundle (numbered zip files + manifest).

.DESCRIPTION
    Reads the project registry (%LOCALAPPDATA%\AlgoProjects\projects.json, written by
    register_project.ps1). For each project it reads <project>\project_manifest.json and packs its
    parts, in order, into:

        <Destination>\migration_<date>\
            unpack_project.ps1          <- run this on the new PC
            bundle_manifest.json        what is in the bundle, SHA-256 of every zip, original paths
            README.txt
            <project name>\01_scripts.zip, 02_updater_state.zip, 03_..., ...

    Parts marked "regenerable" in the manifest (the big feature files) are skipped unless
    -IncludeRegenerable is given. Use -RequiredOnly for the smallest possible bundle.

    Large files: zips are written with .NET's ZipArchive, which switches to ZIP64 automatically, so
    multi-GB parquet files are fine (Compress-Archive in Windows PowerShell 5.1 fails above 2 GB).
    Parquet / zip files are stored without re-compression (they are already compressed - recompressing
    wastes time for ~0% gain); text files are compressed.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEDATAENGINE\migrate\pack_project.ps1 -Destination E:\move
.EXAMPLE
    ... pack_project.ps1 -Destination E:\move -Project nsedataengine -IncludeRegenerable
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Destination,
    # Only these projects (names from the registry). Default: all registered projects.
    [string[]]$Project,
    # Also pack parts that can be rebuilt (feature files).
    [switch]$IncludeRegenerable,
    # Pack only parts marked required.
    [switch]$RequiredOnly
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Get-RegistryPath {
    $base = [Environment]::GetFolderPath('LocalApplicationData')
    return (Join-Path (Join-Path $base 'AlgoProjects') 'projects.json')
}

# Expand one manifest "include" pattern into files.
#   "data\logs\**"      -> every file under data\logs (recursive)
#   "*.py", "data\x.pq" -> files matching the wildcard in that folder (not recursive)
function Resolve-Include([string]$root, [string]$pattern) {
    $pattern = $pattern -replace '[\\/]', [IO.Path]::DirectorySeparatorChar
    if ($pattern.EndsWith('**')) {
        $dir = Join-Path $root ($pattern.Substring(0, $pattern.Length - 2).TrimEnd([IO.Path]::DirectorySeparatorChar))
        if (Test-Path $dir) { return @(Get-ChildItem -Path $dir -Recurse -File) }
        return @()
    }
    $full = Join-Path $root $pattern
    $parent = Split-Path $full -Parent
    if (-not (Test-Path $parent)) { return @() }
    return @(Get-ChildItem -Path $parent -Filter (Split-Path $full -Leaf) -File)
}

# Write a zip. $items: list of @{ Path = full path; Entry = name inside the zip (forward slashes) }
function Write-Zip([string]$zipPath, $items) {
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    $zip = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($it in $items) {
            $ext = [IO.Path]::GetExtension($it.Path).ToLowerInvariant()
            $level = if ($ext -in @('.parquet', '.zip', '.gz', '.7z')) { [System.IO.Compression.CompressionLevel]::NoCompression }
                     else { [System.IO.Compression.CompressionLevel]::Optimal }
            [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $it.Path, $it.Entry, $level)
        }
    } finally { $zip.Dispose() }
}

# ------------------------------------------------------------------------------------------------
$regPath = Get-RegistryPath
if (-not (Test-Path $regPath)) {
    throw "No project registry at $regPath. Run migrate\register_project.ps1 in each project first."
}
$registry = @(Get-Content $regPath -Raw | ConvertFrom-Json)
if ($Project) { $registry = @($registry | Where-Object { $Project -contains $_.name }) }
if ($registry.Count -eq 0) { throw 'No matching registered projects.' }

$bundle = Join-Path $Destination ("migration_" + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Force -Path $bundle | Out-Null
$bundleInfo = [ordered]@{
    created         = (Get-Date).ToString('s')
    source_computer = $env:COMPUTERNAME
    projects        = @()
}

foreach ($reg in $registry) {
    # The env var wins over the registry path (the folder may have been moved since registering).
    $root = [Environment]::GetEnvironmentVariable($reg.env_var)
    if (-not $root -or -not (Test-Path $root)) { $root = $reg.path }
    if (-not (Test-Path (Join-Path $root 'project_manifest.json'))) {
        Write-Warning "Project '$($reg.name)': no project_manifest.json in $root - skipped"; continue
    }
    $m = Get-Content (Join-Path $root 'project_manifest.json') -Raw | ConvertFrom-Json
    $projDir = Join-Path $bundle $m.name
    New-Item -ItemType Directory -Force -Path $projDir | Out-Null
    Write-Host "`n=== $($m.name)  ($root)" -ForegroundColor Cyan
    $partsInfo = @()

    foreach ($part in ($m.parts | Sort-Object order)) {
        $tag = '{0:D2}_{1}' -f [int]$part.order, $part.name
        if ($part.regenerable -and -not $IncludeRegenerable) { Write-Host "  $tag : skipped (regenerable)"; continue }
        if ($RequiredOnly -and -not $part.required) { Write-Host "  $tag : skipped (optional)"; continue }
        $items = @()
        foreach ($pat in @($part.include)) {
            if (-not $pat) { continue }
            foreach ($f in (Resolve-Include $root $pat)) {
                $rel = $f.FullName.Substring($root.TrimEnd('\', '/').Length + 1) -replace '\\', '/'
                $items += @{ Path = $f.FullName; Entry = $rel }
            }
        }
        foreach ($ext in @($part.external)) {          # absolute paths -> archive/<file name>
            if ($ext -and (Test-Path $ext)) { $items += @{ Path = (Resolve-Path $ext).Path; Entry = 'archive/' + (Split-Path $ext -Leaf) } }
            elseif ($ext) { Write-Warning "  $tag : external file not found: $ext" }
        }
        # the same file can match two patterns - keep it once
        $items = @($items | Group-Object { $_.Entry } | ForEach-Object { $_.Group[0] })
        if ($items.Count -eq 0) { Write-Host "  $tag : nothing to pack"; continue }
        $zipName = "$tag.zip"
        $zipPath = Join-Path $projDir $zipName
        $sw = [Diagnostics.Stopwatch]::StartNew()
        Write-Zip $zipPath $items
        $hash = (Get-FileHash -Algorithm SHA256 -Path $zipPath).Hash
        $size = (Get-Item $zipPath).Length
        Write-Host ("  {0} : {1} files, {2:N1} MB, {3:N0}s" -f $tag, $items.Count, ($size / 1MB), $sw.Elapsed.TotalSeconds)
        $partsInfo += [ordered]@{
            order = [int]$part.order; name = $part.name; file = "$($m.name)/$zipName"; sha256 = $hash
            bytes = $size; file_count = $items.Count; required = [bool]$part.required; why = $part.why
        }
    }
    $bundleInfo.projects += [ordered]@{
        name = $m.name; description = $m.description; env_var = $m.env_var
        legacy_env_vars = $m.legacy_env_vars; legacy_names = $m.legacy_names
        source_path = $root; default_path = $m.default_path
        python_requirements = $m.python_requirements; verify_commands = $m.verify_commands
        scheduled_task = $m.scheduled_task; parts = $partsInfo
    }
}

ConvertTo-Json -InputObject $bundleInfo -Depth 8 | Set-Content -Path (Join-Path $bundle 'bundle_manifest.json') -Encoding UTF8
Copy-Item (Join-Path $PSScriptRoot 'unpack_project.ps1') $bundle
$setupScript = Join-Path $PSScriptRoot 'setup_new_pc.ps1'
if (Test-Path $setupScript) { Copy-Item $setupScript $bundle }
@"
Migration bundle created $($bundleInfo.created) on $($bundleInfo.source_computer)

On the NEW PC:
  1. Copy this whole folder to the new PC.
  2. In PowerShell (normal, not admin), in this folder:
        powershell -ExecutionPolicy Bypass -File .\setup_new_pc.ps1
     It installs Python 3.12 if needed (winget), checks (SHA-256) and unpacks every project to %NSE_ROOT%\data\<folder>
     (-Root to change), restores the saved settings variables, runs each project's setup steps from its
     project_manifest.json (packages, virtual environments, environment variables, scheduled tasks) and
     verifies every project. NSEUIENGINE asks for the Fyers credentials again (DPAPI secrets do not move).
  Without setup_new_pc.ps1 (older bundle): powershell -ExecutionPolicy Bypass -File .\unpack_project.ps1
"@ | Set-Content -Path (Join-Path $bundle 'README.txt') -Encoding UTF8

$total = (Get-ChildItem $bundle -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ("`nBundle ready: {0}  ({1:N1} GB)" -f $bundle, ($total / 1GB)) -ForegroundColor Green
```
### `code/NSEDATAENGINE/migrate/prepare_move.ps1`
```
<#
.SYNOPSIS
    OLD PC: packs every NSE project into one migration bundle for setup_new_pc.ps1.

.DESCRIPTION
        powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEDATAENGINE\migrate\prepare_move.ps1 -Destination E:\move

    1. registers every project folder under -Root that has a project_manifest.json (NSEDATAENGINE,
       NSEALGOENGINE, NSEMLENGINE, NSEUIENGINE, and any project added later) in
       %LOCALAPPDATA%\AlgoProjects\projects.json - existing scheduled tasks are not touched
    2. saves the user variables that are settings, not paths (STATICIP_PROXY, STATIC_IP, FYERS_ID,
       FYERS_CLIENT_ID, FYERS_REDIRECT_URI, PYTHONUTF8) to user_env.json in the bundle. Folder variables are
       recomputed on the new PC; daily Fyers tokens are not copied (they expire)
    3. packs every project with pack_project.ps1 (SHA-256 per zip) and puts setup_new_pc.ps1 in the bundle

    Then copy the bundle folder to the new PC and run setup_new_pc.ps1 from it.

    The bundle can contain credentials (NSEALGOENGINE\.env if present, STATICIP_PROXY). Keep it on a
    private drive and delete it after the move.

.PARAMETER IncludeRegenerable
    Also pack files that can be rebuilt (feature files, NSEMLENGINE dataset). Bigger bundle, faster setup.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Destination,
    [string]$Root = '%NSE_ROOT%',
    [switch]$IncludeRegenerable,
    [switch]$RequiredOnly
)
$ErrorActionPreference = 'Stop'
$here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$register = Join-Path $here 'register_project.ps1'
$pack = Join-Path $here 'pack_project.ps1'

# ---- 1. register every project --------------------------------------------------------------------
Write-Host "[1] Registering projects under $Root" -ForegroundColor Cyan
$names = @()
foreach ($d in @(Get-ChildItem -Path $Root -Directory)) {
    $mf = Join-Path $d.FullName 'project_manifest.json'
    if (-not (Test-Path $mf)) { continue }
    $m = Get-Content $mf -Raw | ConvertFrom-Json
    # -RegistryOnly: only the registry entry - variables and scheduled tasks on this PC stay as they are
    & $register -ProjectRoot $d.FullName -RegistryOnly
    $names += $m.name
}
if (-not $names.Count) { throw "No project_manifest.json found under $Root" }

# ---- 2. pack -------------------------------------------------------------------------------------
Write-Host "`n[2] Packing $($names -join ', ')" -ForegroundColor Cyan
$before = @(Get-ChildItem -Path $Destination -Directory -Filter 'migration_*' -ErrorAction SilentlyContinue | ForEach-Object FullName)
$packArgs = @{ Destination = $Destination; Project = $names }
if ($IncludeRegenerable) { $packArgs.IncludeRegenerable = $true }
if ($RequiredOnly) { $packArgs.RequiredOnly = $true }
& $pack @packArgs
$bundle = Get-ChildItem -Path $Destination -Directory -Filter 'migration_*' | Where-Object { $before -notcontains $_.FullName } |
    Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $bundle) { throw "pack_project.ps1 did not create a bundle in $Destination" }

# ---- 3. settings variables -------------------------------------------------------------------------
Write-Host "`n[3] Saving user settings variables" -ForegroundColor Cyan
$keep = @('STATICIP_PROXY', 'STATIC_IP', 'FYERS_ID', 'FYERS_CLIENT_ID', 'FYERS_REDIRECT_URI', 'PYTHONUTF8')
$saved = [ordered]@{}
foreach ($k in $keep) {
    $v = [Environment]::GetEnvironmentVariable($k, 'User')
    if ($v) { $saved[$k] = $v; Write-Host "  $k" }
}
ConvertTo-Json -InputObject $saved | Set-Content -Path (Join-Path $bundle.FullName 'user_env.json') -Encoding UTF8
Copy-Item (Join-Path $here 'setup_new_pc.ps1') $bundle.FullName -Force

Write-Host "`nBundle ready: $($bundle.FullName)" -ForegroundColor Green
Write-Host 'On the new PC: copy the folder, then in PowerShell inside it:'
Write-Host '    powershell -ExecutionPolicy Bypass -File .\setup_new_pc.ps1'
```
### `code/NSEDATAENGINE/migrate/register_preopen_task.ps1`
```
<#
.SYNOPSIS
  Scheduled task "NSEDATAENGINE-PreOpen": records NSE's pre-open auction every trading morning (nse_preopen.py).

    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEDATAENGINE\migrate\register_preopen_task.ps1

  Mon-Fri 08:58 -> run_preopen.bat -> python nse_preopen.py record (finishes ~09:14).
  Runs whether you are logged on or not: asks for your Windows password once (for a Microsoft account / PIN sign-in:
  the Microsoft account password). Wake to run is on, so the PC may be asleep - but not shut down or hibernated.
  -Interactive   register "only when logged on" instead (no password asked)
  -At "08:58"    start time (keep it before 09:00)

  Check:   python nse_preopen.py show        Report:  python nse_preopen.py report
  Logs:    data\logs\preopen_log.txt, data\logs\preopen_task.txt
  Remove:  Unregister-ScheduledTask NSEDATAENGINE-PreOpen -Confirm:$false
#>
param([string]$At = "08:58", [switch]$Interactive)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$bat = Join-Path $root "run_preopen.bat"
if (-not (Test-Path $bat)) { throw "not found: $bat" }
$name = "NSEDATAENGINE-PreOpen"

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 40) -MultipleInstances IgnoreNew
$desc = "NSEDATAENGINE: records NSE pre-open indicative prices 09:00-09:14 (nse_preopen.py record)"
$user = "$env:USERDOMAIN\$env:USERNAME"

if ($Interactive) {
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description $desc -Force | Out-Null
} else {
    $cred = Get-Credential -UserName $user -Message "Windows password for $name (runs while you are logged off)"
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
        -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited `
        -Description $desc -Force | Out-Null
}
$t = Get-ScheduledTask -TaskName $name
Write-Host ("Registered {0}: Mon-Fri {1}, logon {2}, wake to run {3}" -f $name, $At, $t.Principal.LogonType, $t.Settings.WakeToRun) -ForegroundColor Green
Write-Host "Test the NSE connection now (saves the last session's pre-open):  python `"$root\nse_preopen.py`" once"
```
### `code/NSEDATAENGINE/migrate/register_project.ps1`
```
<#
.SYNOPSIS
    Registers this project on THIS PC: sets its environment variable and adds it to the machine's
    project registry, so pack_project.ps1 knows what to pack when you change PCs.

.DESCRIPTION
    Run once on the current PC (and it is run automatically by unpack_project.ps1 on a new PC).

    What it does
      1. Reads ..\project_manifest.json (the project folder is the parent of this "migrate" folder).
      2. Sets the user environment variable named in the manifest (NSEDATAENGINE_HOME) to the project folder,
         plus the folder variables defined in contract\schema.yaml (via set_env_vars.ps1), and removes the
         old variable names listed in legacy_env_vars (NSE_ALGO_HOME).
         The Python scripts use it to find their data, wherever the project lives.
      3. Adds/updates the project in the project registry:
             %LOCALAPPDATA%\AlgoProjects\projects.json
         One entry per project: name, env var, folder. pack_project.ps1 packs every registered project,
         so projects you add later (e.g. your trading-system code) travel together.
      4. Optionally (-ScheduledTask) creates the Windows scheduled task described in the manifest.

    No administrator rights needed: user-level environment variable and a user-level scheduled task.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEDATAENGINE\migrate\register_project.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEDATAENGINE\migrate\register_project.ps1 -ScheduledTask
#>
[CmdletBinding()]
param(
    # Project folder. Default: the parent of the folder this script is in (worked out below - Windows
    # PowerShell 5.1 leaves $PSScriptRoot empty inside parameter defaults, so it cannot be used here).
    [string]$ProjectRoot,
    # Also create / replace the scheduled task from the manifest.
    [switch]$ScheduledTask,
    # Only add / refresh the project registry entry (used by prepare_move.ps1 on the old PC).
    [switch]$RegistryOnly
)
$ErrorActionPreference = 'Stop'
if (-not $ProjectRoot) {
    $here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $ProjectRoot = Split-Path -Parent $here
}

# Windows PowerShell 5.1 has no $IsWindows variable; PowerShell 7 has. This works on both.
function Test-Windows { return ($env:OS -eq 'Windows_NT') }

# Where the registry of projects lives (per user, survives reboots, not inside any project).
function Get-RegistryPath {
    $base = [Environment]::GetFolderPath('LocalApplicationData')
    return (Join-Path (Join-Path $base 'AlgoProjects') 'projects.json')
}

$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$manifestPath = Join-Path $ProjectRoot 'project_manifest.json'
if (-not (Test-Path $manifestPath)) { throw "project_manifest.json not found in $ProjectRoot" }
$m = Get-Content $manifestPath -Raw | ConvertFrom-Json

# --- 1. environment variable ---------------------------------------------------------------------
# 'User' scope = stored in the registry under HKCU\Environment, visible to new processes of this user
# (including scheduled tasks). The current window gets it too via $env:.
if (-not $RegistryOnly) {
if (Test-Windows) {
    [Environment]::SetEnvironmentVariable($m.env_var, $ProjectRoot, 'User')
}
Set-Item -Path "Env:$($m.env_var)" -Value $ProjectRoot
Write-Host "Environment variable $($m.env_var) = $ProjectRoot"

# --- 1b. folder variables from the column contract (contract\schema.yaml, `locations:`) -----------
# NSEDATAENGINE_DATA_DIR, _FRESH_DIR, _REF_DIR, ... and NSEALGO_SRC; old names (legacy_env_vars) are removed.
$setEnv = Join-Path $PSScriptRoot 'set_env_vars.ps1'
if (-not $PSScriptRoot) { $setEnv = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'set_env_vars.ps1' }
# only for a project that defines folder variables through a column contract (NSEDATAENGINE)
if ((Test-Path $setEnv) -and (Test-Path (Join-Path $ProjectRoot 'nse_contract.py'))) {
    try { & $setEnv -ProjectRoot $ProjectRoot -LegacyVars @($m.legacy_env_vars) }
    catch { Write-Warning "Folder variables not set: $_  (the scripts then use the default folders under $ProjectRoot)" }
}

}   # end of: if (-not $RegistryOnly)

# --- 2. project registry -------------------------------------------------------------------------
$regPath = Get-RegistryPath
New-Item -ItemType Directory -Force -Path (Split-Path $regPath) | Out-Null
$reg = @()
if (Test-Path $regPath) { $reg = @(Get-Content $regPath -Raw | ConvertFrom-Json) }
# replace any older entry for this project, including entries under its old names (legacy_names)
$reg = @($reg | Where-Object { $_.name -ne $m.name -and (@($m.legacy_names) -notcontains $_.name) })
$reg += [pscustomobject]@{
    name          = $m.name
    env_var       = $m.env_var
    path          = $ProjectRoot
    registered_at = (Get-Date).ToString('s')
    computer      = $env:COMPUTERNAME
}
ConvertTo-Json -InputObject @($reg) -Depth 5 | Set-Content -Path $regPath -Encoding UTF8
Write-Host "Registered project '$($m.name)' in $regPath ($($reg.Count) project(s) registered)"

# --- 3. scheduled task (optional) ----------------------------------------------------------------
if ($ScheduledTask -and $m.scheduled_task -and -not $RegistryOnly) {
    if (-not (Test-Windows)) { Write-Warning 'Scheduled tasks can only be created on Windows - skipped.' }
    else {
        $t = $m.scheduled_task
        $cmd = '"' + (Join-Path $ProjectRoot $t.script) + '"'
        # /F replaces an existing task of the same name (e.g. one pointing at the old folder)
        $taskArgs = @('/Create', '/TN', $t.name, '/TR', $cmd, '/SC', $t.schedule, '/ST', $t.time, '/F')
        if ($t.days) { $taskArgs += @('/D', $t.days) }
        & schtasks.exe @taskArgs | Out-Host
        Write-Host "Scheduled task '$($t.name)' -> $cmd ($($t.schedule) $($t.days) $($t.time))"
    }
}
Write-Host 'Done. Open a NEW terminal window for the environment variable to be visible there.'
```
### `code/NSEDATAENGINE/migrate/set_env_vars.ps1`
```
<#
.SYNOPSIS
    Sets the NSEDATAENGINE folder environment variables for the current Windows user.

.DESCRIPTION
    The folders (data, data_fresh, data_ref, events, backtest_results, portfolio_results, NSEALGOENGINE source)
    and their variable names are defined ONCE, in contract\schema.yaml (`locations:`). This script asks
    nse_contract.py for them ("python nse_contract.py env --home <root>") and stores each one as a user
    environment variable, so every script - and the scheduled task - finds its files through them.
    Old variable names listed in -LegacyVars are removed.
    Called by register_project.ps1 and unpack_project.ps1; can also be run on its own after moving a folder.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEDATAENGINE\migrate\set_env_vars.ps1
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot,
    [string[]]$LegacyVars = @('NSE_ALGO_HOME'),
    [string]$Python = 'python',
    [string[]]$PythonArgs = @()
)
$ErrorActionPreference = 'Stop'
if (-not $ProjectRoot) {
    $here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $ProjectRoot = Split-Path -Parent $here
}
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$isWin = ($env:OS -eq 'Windows_NT')
$contractPy = Join-Path $ProjectRoot 'nse_contract.py'
if (-not (Test-Path $contractPy)) { throw "nse_contract.py not found in $ProjectRoot" }

$lines = @(& $Python @PythonArgs $contractPy env --home $ProjectRoot)
if ($LASTEXITCODE -ne 0 -or $lines.Count -eq 0) {
    throw "python nse_contract.py env failed (is PyYAML installed? python -m pip install PyYAML)"
}
foreach ($line in $lines) {
    if ($line -match '^([A-Za-z0-9_]+)=(.+)$') {
        $name, $value = $Matches[1], $Matches[2].Trim()
        if ($isWin) { [Environment]::SetEnvironmentVariable($name, $value, 'User') }
        Set-Item -Path "Env:$name" -Value $value
        Write-Host ("Environment variable {0,-28} = {1}" -f $name, $value)
    }
}
foreach ($old in $LegacyVars) {
    if (-not $old) { continue }
    if ($isWin -and [Environment]::GetEnvironmentVariable($old, 'User')) {
        [Environment]::SetEnvironmentVariable($old, $null, 'User')
        Write-Host "Removed old environment variable $old"
    }
    Remove-Item -Path "Env:$old" -ErrorAction SilentlyContinue
}
```
### `code/NSEDATAENGINE/migrate/set_time_sync.ps1`
```
<#
.SYNOPSIS
  Accurate PC clock for trading and latency measurement (asks for Administrator rights).
  Windows Time service: automatic start, time.google.com + time.cloudflare.com, sync every ~17 min, correct the
  clock at once instead of slowly. Then shows the offset (should be within about +/-0.01 s).
    powershell -ExecutionPolicy Bypass -File migrate\set_time_sync.ps1
#>
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host "Asking for Administrator rights (Windows time settings) ..."
    $p = Start-Process powershell -Verb RunAs -Wait -PassThru -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    exit $p.ExitCode
}
sc.exe config w32time start= auto | Out-Null
reg add HKLM\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\NtpClient /v SpecialPollInterval /t REG_DWORD /d 900 /f | Out-Null
reg add HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Config /v MaxAllowedPhaseOffset /t REG_DWORD /d 0 /f | Out-Null
net stop w32time 2>$null | Out-Null
net start w32time | Out-Null
w32tm /config /manualpeerlist:"time.google.com,0x8 time.cloudflare.com,0x8" /syncfromflags:manual /update | Out-Null
Start-Sleep -Seconds 3
w32tm /resync /force
w32tm /stripchart /computer:time.google.com /samples:3 /dataonly
Write-Host "Done. The offsets above should be close to +00.00s."
if ($Host.Name -eq "ConsoleHost") { Start-Sleep -Seconds 5 }
exit 0
```
### `code/NSEDATAENGINE/migrate/setup_new_pc.ps1`
```
<#
.SYNOPSIS
    NEW PC: sets up every NSE project (NSEDATAENGINE, NSEALGOENGINE, NSEMLENGINE, NSEUIENGINE) from scratch.

.DESCRIPTION
    One script for the whole move. Run it in a normal (non-admin) PowerShell window:

        powershell -ExecutionPolicy Bypass -File .\setup_new_pc.ps1

    1. Python      makes sure Python 3.12 is installed (winget, per-user, no admin) and that 'python' is on PATH
    2. Files       if a migration bundle is next to this script (bundle_manifest.json, made on the old PC by
                   prepare_move.ps1), checks and unpacks every project into the same folders as before
                   (or -Root); without a bundle it uses the project folders already under -Root
    3. Settings    restores the non-path user variables saved on the old PC (STATICIP_PROXY, FYERS_ID, ...)
    4. Projects    runs each project's own setup steps, read from its project_manifest.json ("setup" block),
                   in order: packages / virtual environments, environment variables, scheduled tasks
    5. Verify      runs each project's checks and prints a summary; a full log is written to
                   %USERPROFILE%\AlgoSetup\setup_<date>.log

    Each project describes its own steps, so a new project or a new step is added in that project's
    project_manifest.json - this script does not change.

    Interactive on purpose: NSEUIENGINE asks for the Fyers credentials again, because its secrets file is
    encrypted with Windows DPAPI for the old PC's account and cannot be read on a new PC.

.PARAMETER Root
    Parent folder of the projects (default %NSE_ROOT%). With a bundle, projects are unpacked to <Root>\<folder>.
.PARAMETER Only / Skip
    Project folder names to include / leave out, e.g. -Skip NSEUIENGINE
.PARAMETER Gpu
    NSEALGOENGINE: install PyTorch for the Radeon GPU (needs the AMD "PyTorch on Windows" driver first).
.PARAMETER ForwardTestTask
    NSEALGOENGINE: also register the 'nsealgo-forward' scheduled task.
.PARAMETER AlgoExtras
    NSEALGOENGINE pip extras (broker SDKs). Default dev,fyers,shoonya,upstox,yahoo
.PARAMETER NoPythonInstall
    Do not install Python (use whatever is installed).
.PARAMETER NoTasks
    Set everything up but register NO scheduled tasks (and fetch no Fyers token) - use this while the old PC is
    still the live one, so the two PCs never both trade or both download.
.PARAMETER TasksOnly
    Switch-over: only the scheduled-task steps (run after disable_old_pc_tasks.ps1 on the old PC).
.PARAMETER SettingsFile
    user_env.json written on the old PC by export_settings.ps1 (default: <Root>\_move\user_env.json if present).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\setup_new_pc.ps1
.EXAMPLE
    .\setup_new_pc.ps1 -Root D:\algo -Gpu -Skip NSEUIENGINE
.EXAMPLE
    .\setup_new_pc.ps1 -Root D:\NSE -Gpu -NoTasks      (PX13, Oct 2026: set up while the HP is still live)
    .\setup_new_pc.ps1 -Root D:\NSE -Gpu -TasksOnly    (switch-over day, after the HP's tasks are disabled)
#>
[CmdletBinding()]
param(
    [string]$Root = 'D:\NSE',
    [string]$BundleDir,
    [string[]]$Only = @(),
    [string[]]$Skip = @(),
    [switch]$Gpu,
    [switch]$ForwardTestTask,
    [string]$AlgoExtras = 'dev,fyers,shoonya,upstox,yahoo',
    [string]$PythonVersion = '3.12',
    [switch]$NoPythonInstall,
    [switch]$NoTasks,
    [switch]$TasksOnly,
    [string]$SettingsFile
)
if ($NoTasks -and $TasksOnly) { throw '-NoTasks and -TasksOnly exclude each other' }
$ErrorActionPreference = 'Stop'
$here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $BundleDir -and (Test-Path (Join-Path $here 'bundle_manifest.json'))) { $BundleDir = $here }
$logDir = Join-Path $env:USERPROFILE 'AlgoSetup'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("setup_" + (Get-Date -Format 'yyyyMMdd_HHmmss') + ".log")
$summary = New-Object System.Collections.Generic.List[object]

function Log([string]$msg, [string]$color = 'Gray') {
    Write-Host $msg -ForegroundColor $color
    Add-Content -Path $logFile -Value ("{0}  {1}" -f (Get-Date -Format s), $msg)
}
function Step([string]$project, [string]$name, [string]$status, [string]$detail = '') {
    $summary.Add([pscustomobject]@{ Project = $project; Step = $name; Status = $status; Detail = $detail })
}
function Update-SessionPath {
    $m = [Environment]::GetEnvironmentVariable('Path', 'Machine'); $u = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($m, $u) | Where-Object { $_ }) -join ';'
}
function Update-SessionEnv {
    # pick up variables set for the user by earlier steps (they are not in this window's environment yet)
    foreach ($scope in 'Machine', 'User') {
        $vars = [Environment]::GetEnvironmentVariables($scope)
        foreach ($k in $vars.Keys) { if ($k -ne 'Path') { Set-Item -Path "Env:$k" -Value $vars[$k] } }
    }
    Update-SessionPath
}
# the real python.exe on PATH (Windows ships a WindowsApps stub that only opens the Store)
function Get-PathPython {
    foreach ($c in @(Get-Command python -All -ErrorAction SilentlyContinue)) {
        if ($c.Source -and $c.Source -notlike '*WindowsApps*') { return $c.Source }
    }
    return $null
}
function Test-PyLauncher([string]$v) {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) { return $false }
    & py "-$v" -c "print(1)" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}
# Run one command line in a project folder through cmd.exe (inherits this console, so prompts work).
# Interactive steps keep the console untouched (prompts must show); other steps also copy their output (and the
# error text when they fail) into the log, so a FAILED step can be diagnosed from the log afterwards.
function Invoke-InFolder([string]$folder, [string]$cmdline, [bool]$interactive = $true) {
    Push-Location $folder
    try {
        Add-Content -Path $logFile -Value "    > $cmdline"
        if ($interactive) {
            & cmd.exe /d /c $cmdline | Out-Host
        } else {
            & cmd.exe /d /c "$cmdline 2>&1" | ForEach-Object { Add-Content -Path $logFile -Value "    | $_"; $_ } | Out-Host
        }
        return $LASTEXITCODE
    } finally { Pop-Location }
}

Log "=== NSE projects - new PC setup  ($env:COMPUTERNAME, $(Get-Date -Format 'yyyy-MM-dd HH:mm'))" 'Cyan'
Log "log: $logFile"
if ($env:OS -ne 'Windows_NT') { throw 'This script sets up Windows (scheduled tasks, user variables).' }

# ---- 1. Python ------------------------------------------------------------------------------------
Log "`n[1] Python $PythonVersion" 'Cyan'
if (-not (Test-PyLauncher $PythonVersion) -and -not $NoPythonInstall) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Python $PythonVersion not found and winget is missing. Install Python $PythonVersion from python.org (tick 'Add python.exe to PATH'), then run this again."
    }
    $id = "Python.Python.$PythonVersion"
    Log "  installing $id with winget (per-user) ..."
    & winget install -e --id $id --scope user --silent --accept-package-agreements --accept-source-agreements `
        --override "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1"
    Update-SessionPath
    if (-not (Test-PyLauncher $PythonVersion)) { throw "Python $PythonVersion still not found after winget - open a new PowerShell window and run this again." }
}
$pathPy = Get-PathPython
if (-not $pathPy) {
    # 'python' must exist on PATH: NSEDATAENGINE's scheduled task and NSEMLENGINE run 'python'
    $exe = (& py "-$PythonVersion" -c "import sys; print(sys.executable)").Trim()
    $dir = Split-Path $exe
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    [Environment]::SetEnvironmentVariable('Path', ("$dir;$dir\Scripts;" + $userPath).TrimEnd(';'), 'User')
    Update-SessionPath
    $pathPy = Get-PathPython
    Log "  added $dir to the user PATH"
}
$pyVer = (& $pathPy -c "import sys; print('%d.%d.%d' % sys.version_info[:3])").Trim()
Log "  python on PATH: $pathPy ($pyVer)"
& $pathPy -m pip install --user --disable-pip-version-check -q --upgrade pip | Out-Null
Step 'PC' 'Python' 'ok' "$pathPy ($pyVer)"

# ---- 2. files -------------------------------------------------------------------------------------
Log "`n[2] Project files" 'Cyan'
if ($BundleDir) {
    $BundleDir = (Resolve-Path $BundleDir).Path
    $bm = Get-Content (Join-Path $BundleDir 'bundle_manifest.json') -Raw | ConvertFrom-Json
    $paths = @{}
    foreach ($p in @($bm.projects)) {
        $leaf = Split-Path $p.source_path -Leaf
        $paths[$p.name] = Join-Path $Root $leaf
    }
    Log "  bundle $BundleDir (from $($bm.source_computer), $($bm.created)) -> $Root"
    $unpack = Join-Path $BundleDir 'unpack_project.ps1'
    & $unpack -BundleDir $BundleDir -Paths $paths -NoScheduledTask -SkipPython
    Step 'PC' 'unpack bundle' 'ok' "$(@($bm.projects).Count) project(s) -> $Root"
    $userEnv = Join-Path $BundleDir 'user_env.json'
} else {
    Log "  no bundle next to this script - using the project folders under $Root"
    $userEnv = $null
}
if ($SettingsFile) { $userEnv = $SettingsFile }
elseif (-not $userEnv -and (Test-Path (Join-Path $Root '_move\user_env.json'))) { $userEnv = Join-Path $Root '_move\user_env.json' }

# ---- 3. user variables saved on the old PC ----------------------------------------------------------
Log "`n[3] User variables from the old PC" 'Cyan'
if ($TasksOnly) { Log '  -TasksOnly: skipped' }
elseif ($userEnv -and (Test-Path $userEnv)) {
    $saved = Get-Content $userEnv -Raw | ConvertFrom-Json
    $oldRoot = if ($saved.PSObject.Properties['_old_root']) { [string]$saved._old_root } else { '%NSE_ROOT%' }
    foreach ($prop in $saved.PSObject.Properties) {
        if ($prop.Name.StartsWith('_')) { continue }
        if ([Environment]::GetEnvironmentVariable($prop.Name, 'User')) { Log "  $($prop.Name): already set - kept"; continue }
        $val = [string]$prop.Value
        if ($val -like "$oldRoot*") { $val = $Root + $val.Substring($oldRoot.Length) }     # old folder -> new folder
        [Environment]::SetEnvironmentVariable($prop.Name, $val, 'User')
        Log "  $($prop.Name) restored"
    }
    Step 'PC' 'user variables' 'ok' (($saved.PSObject.Properties | ForEach-Object Name) -join ', ')
} else { Log '  none saved (no bundle or no user_env.json)' }
Update-SessionEnv

# ---- 4 + 5. each project's own setup and checks ---------------------------------------------------
$projects = @()
foreach ($mf in @(Get-ChildItem -Path $Root -Directory | ForEach-Object { Join-Path $_.FullName 'project_manifest.json' } | Where-Object { Test-Path $_ })) {
    $m = Get-Content $mf -Raw | ConvertFrom-Json
    $folder = Split-Path $mf -Parent
    $leaf = Split-Path $folder -Leaf
    if ($Only.Count -and $Only -notcontains $leaf) { continue }
    if ($Skip -contains $leaf) { Log "  $leaf skipped (-Skip)"; continue }
    if (-not $m.setup) { Log "  $leaf has no setup steps in project_manifest.json - skipped" 'Yellow'; continue }
    $order = if ($m.setup.order) { [int]$m.setup.order } else { 99 }
    $projects += [pscustomobject]@{ Leaf = $leaf; Folder = $folder; M = $m; Order = $order }
}
$dataHome = ($projects | Where-Object { $_.M.name -eq 'nsedataengine' } | Select-Object -First 1).Folder
if (-not $dataHome) { $dataHome = Join-Path $Root 'NSEDATAENGINE' }
$conditions = @('always', $(if ($Gpu) { 'gpu' } else { 'nogpu' }))
if ($ForwardTestTask) { $conditions += 'forwardtest' }

foreach ($p in ($projects | Sort-Object Order)) {
    Log "`n[4] $($p.Leaf) - $($p.M.description)" 'Cyan'
    $subst = @{ '{python}' = "`"$pathPy`""; '{root}' = $p.Folder; '{datahome}' = $dataHome; '{algo_extras}' = $AlgoExtras }
    $failed = $false
    foreach ($s in @($p.M.setup.steps)) {
        $when = if ($s.when) { $s.when } else { 'always' }
        if ($conditions -notcontains $when) { continue }
        $isTask = [bool]($s.PSObject.Properties['task'] -and $s.task)
        if ($NoTasks -and $isTask) { Log "  - $($s.name): skipped (-NoTasks)" 'Yellow'; Step $p.Leaf $s.name 'ok' 'deferred (-NoTasks)'; continue }
        if ($TasksOnly -and -not $isTask) { continue }
        $cmd = [string]$s.run
        foreach ($k in $subst.Keys) { $cmd = $cmd.Replace($k, $subst[$k]) }
        Log "  - $($s.name)"
        $isInteractive = [bool]($s.PSObject.Properties['interactive'] -and $s.interactive)
        $rc = Invoke-InFolder $p.Folder $cmd $isInteractive
        Update-SessionEnv
        if ($rc -ne 0) { Log "    FAILED (exit $rc)" 'Red'; Step $p.Leaf $s.name 'FAILED' "exit $rc"; $failed = $true; break }
        Step $p.Leaf $s.name 'ok'
    }
    Log "[5] $($p.Leaf) checks" 'Cyan'
    foreach ($v in @($p.M.setup.verify)) {
        if (-not $v -or $failed) { continue }
        $cmd = [string]$v
        foreach ($k in $subst.Keys) { $cmd = $cmd.Replace($k, $subst[$k]) }
        Log "  - $cmd"
        $rc = Invoke-InFolder $p.Folder $cmd $false
        Step $p.Leaf "check: $v" $(if ($rc -eq 0) { 'ok' } else { 'FAILED' }) $(if ($rc -ne 0) { "exit $rc" } else { '' })
    }
    foreach ($n in @($p.M.setup.notes)) { if ($n) { Log "  note: $n" 'Yellow' } }
}

# ---- summary ----------------------------------------------------------------------------------------
Log "`n=== Summary" 'Cyan'
$summary | Format-Table -AutoSize | Out-String -Width 220 | ForEach-Object { Log $_ }
$bad = @($summary | Where-Object Status -ne 'ok')
if ($bad.Count) {
    Log "$($bad.Count) step(s) failed - fix them and run this script again (finished steps are safe to repeat)." 'Red'
    $oldXml = Join-Path $Root '_move\old_pc_tasks_xml'
    if (@($bad | Where-Object Step -like 'scheduled task*').Count -and (Test-Path $oldXml)) {
        Log "Scheduled tasks failed: register the old PC's saved tasks instead:  powershell -ExecutionPolicy Bypass -File `"$dataHome\migrate\import_old_pc_tasks.ps1`" -Root $Root" 'Yellow'
    }
    exit 1
}
Log 'All projects are set up. Open a NEW terminal so the new environment variables are visible there.' 'Green'
if ($NoTasks) { Log "No scheduled tasks were registered (-NoTasks). Switch-over: on the old PC disable_old_pc_tasks.ps1, then here: setup_new_pc.ps1 -Root $Root -TasksOnly" 'Yellow' }
Log "Scheduled tasks: Get-ScheduledTask | Where-Object TaskName -cmatch '^(NSE|nsealgo)' | Format-Table TaskName,State"
```
### `code/NSEDATAENGINE/migrate/unpack_project.ps1`
```
<#
.SYNOPSIS
    NEW PC: restores every project in a migration bundle made by pack_project.ps1.

.DESCRIPTION
    Run from inside the bundle folder (a copy of this script is placed there by pack_project.ps1).
    For each project in bundle_manifest.json:
      1. asks where the project should live (Enter = same path as on the old PC); -Paths skips the prompt
      2. checks every zip against its SHA-256 from the old PC (a corrupted copy is refused, not unpacked)
      3. unpacks the zips in their numbered order into that folder, keeping every relative path, so the
         scripts find their data exactly as before (existing files are overwritten)
      4. sets the project's user environment variable (e.g. NSEDATAENGINE_HOME) and registers the project in
         %LOCALAPPDATA%\AlgoProjects\projects.json, so the next move works the same way
      5. installs the Python packages from the project's requirements file into the Python on PATH
      6. runs the project's verify command(s) (e.g. "python nse_updater.py check")
      7. asks whether to re-create the scheduled task at the new path (-ScheduledTask / -NoScheduledTask)
    A log of everything is written to <project>\migrate\unpack_log.txt.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\unpack_project.ps1
.EXAMPLE
    .\unpack_project.ps1 -Paths @{ nse_data = 'D:\algo\nse_data' } -ScheduledTask
#>
[CmdletBinding()]
param(
    # Bundle folder. Default: the folder this script is in (set below; see note in register_project.ps1).
    [string]$BundleDir,
    # project name -> install folder; projects not listed are asked for interactively
    [hashtable]$Paths = @{},
    [switch]$ScheduledTask,
    [switch]$NoScheduledTask,
    [switch]$SkipPython
)
$ErrorActionPreference = 'Stop'
if (-not $BundleDir) {
    $BundleDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
}
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Test-Windows { return ($env:OS -eq 'Windows_NT') }

function Get-RegistryPath {
    $base = [Environment]::GetFolderPath('LocalApplicationData')
    return (Join-Path (Join-Path $base 'AlgoProjects') 'projects.json')
}

# Find a Python 3 to use: the "py" launcher (standard on Windows) or "python" on PATH.
function Get-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) { return @('py', '-3') }
    foreach ($n in @('python', 'python3')) {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        # Windows ships a "python.exe" stub that only opens the Microsoft Store - skip it
        if ($c -and $c.Source -notlike '*WindowsApps*') { return @($n) }
    }
    return $null
}

# Unpack a zip into $root, overwriting files; entries use forward slashes, converted to this OS.
function Expand-ZipOverwrite([string]$zipPath, [string]$root) {
    $zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        foreach ($e in $zip.Entries) {
            if (-not $e.Name) { continue }                       # folder entry
            $rel = $e.FullName -replace '/', [IO.Path]::DirectorySeparatorChar
            $target = [IO.Path]::GetFullPath((Join-Path $root $rel))
            if (-not $target.StartsWith([IO.Path]::GetFullPath($root))) { throw "unsafe path in zip: $($e.FullName)" }
            New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($e, $target, $true)
        }
        return $zip.Entries.Count
    } finally { $zip.Dispose() }
}

# ------------------------------------------------------------------------------------------------
$BundleDir = (Resolve-Path $BundleDir).Path
$bm = Get-Content (Join-Path $BundleDir 'bundle_manifest.json') -Raw | ConvertFrom-Json
Write-Host "Bundle from $($bm.source_computer), created $($bm.created): $(@($bm.projects).Count) project(s)" -ForegroundColor Cyan

foreach ($p in @($bm.projects)) {
    Write-Host "`n=== $($p.name) - $($p.description)" -ForegroundColor Cyan
    # --- 1. where should it go? -----------------------------------------------------------------
    $default = $p.source_path
    if ($Paths.ContainsKey($p.name)) { $root = $Paths[$p.name] }
    else {
        $ans = Read-Host "Folder for project '$($p.name)' [Enter = $default]"
        $root = if ([string]::IsNullOrWhiteSpace($ans)) { $default } else { $ans.Trim('"', ' ') }
    }
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $root = (Resolve-Path $root).Path
    $logDir = Join-Path $root 'migrate'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $logFile = Join-Path $logDir 'unpack_log.txt'
    function Log([string]$msg) { $line = "$(Get-Date -Format s)  $msg"; Write-Host "  $msg"; Add-Content -Path $logFile -Value $line }
    Log "unpacking '$($p.name)' to $root (bundle $BundleDir, from $($bm.source_computer):$($p.source_path))"

    # --- 2 + 3. verify and unpack in order --------------------------------------------------------
    $ok = $true
    foreach ($part in (@($p.parts) | Sort-Object order)) {
        $zipPath = Join-Path $BundleDir ($part.file -replace '/', [IO.Path]::DirectorySeparatorChar)
        if (-not (Test-Path $zipPath)) {
            if ($part.required) { Log "MISSING required part $($part.file) - project not complete"; $ok = $false }
            else { Log "optional part $($part.file) not in bundle - skipped" }
            continue
        }
        $hash = (Get-FileHash -Algorithm SHA256 -Path $zipPath).Hash
        if ($hash -ne $part.sha256) {
            Log "CHECKSUM MISMATCH for $($part.file) - file was damaged while copying; copy it again. Not unpacked."
            if ($part.required) { $ok = $false }
            continue
        }
        $n = Expand-ZipOverwrite $zipPath $root
        Log ("{0:D2} {1}: {2} files restored" -f [int]$part.order, $part.name, $n)
    }
    if (-not $ok) { Write-Warning "Project '$($p.name)' is incomplete - fix the problems above and run this script again."; continue }

    # --- 4. environment variable + registry -----------------------------------------------------
    if (Test-Windows) { [Environment]::SetEnvironmentVariable($p.env_var, $root, 'User') }
    Set-Item -Path "Env:$($p.env_var)" -Value $root
    $regPath = Get-RegistryPath
    New-Item -ItemType Directory -Force -Path (Split-Path $regPath) | Out-Null
    $reg = @()
    if (Test-Path $regPath) { $reg = @(Get-Content $regPath -Raw | ConvertFrom-Json) }
    $reg = @($reg | Where-Object { $_.name -ne $p.name -and (@($p.legacy_names) -notcontains $_.name) })
    $reg += [pscustomobject]@{ name = $p.name; env_var = $p.env_var; path = $root
                              registered_at = (Get-Date).ToString('s'); computer = $env:COMPUTERNAME }
    ConvertTo-Json -InputObject @($reg) -Depth 5 | Set-Content -Path $regPath -Encoding UTF8
    Log "environment variable $($p.env_var) = $root; registered in $regPath"

    # --- 5 + 6. Python packages and verification ---------------------------------------------------
    if (-not $SkipPython) {
        # @() keeps a one-element result as an array (PowerShell would otherwise unwrap it to a string)
        $py = @(Get-Python | Where-Object { $_ })
        if ($py.Count -eq 0) {
            Log 'Python not found. Install Python 3.11+ from python.org (tick "Add python.exe to PATH"), then run:'
            Log "  cd `"$root`"; python -m pip install -r $($p.python_requirements); python $($p.verify_commands -join '; python ')"
        } else {
            $pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)
            if ($p.python_requirements -and (Test-Path (Join-Path $root $p.python_requirements))) {
                Log "installing Python packages from $($p.python_requirements) ..."
                & $pyExe @pyArgs -m pip install --user --disable-pip-version-check -q -r (Join-Path $root $p.python_requirements) 2>&1 |
                    ForEach-Object { Add-Content -Path $logFile -Value "    $_" }
            }
            # folder variables from contract\schema.yaml (needs the packages above)
            $setEnv = Join-Path $root 'migrate\set_env_vars.ps1'
            if (Test-Path $setEnv) {
                try { & $setEnv -ProjectRoot $root -LegacyVars @($p.legacy_env_vars) -Python $pyExe -PythonArgs $pyArgs | ForEach-Object { Log "    $_" } }
                catch { Log "folder variables not set: $_" }
            }
            Push-Location $root
            try {
                foreach ($vc in @($p.verify_commands)) {
                    if (-not $vc) { continue }
                    Log "verify: python $vc"
                    $out = & $pyExe @pyArgs @($vc -split ' ') 2>&1
                    $out | ForEach-Object { Log "    $_" }
                }
            } finally { Pop-Location }
        }
    }

    # --- 7. scheduled task -----------------------------------------------------------------------
    if ($p.scheduled_task -and -not $NoScheduledTask) {
        $t = $p.scheduled_task
        $create = $ScheduledTask
        if (-not $create) { $create = ((Read-Host "Create scheduled task '$($t.name)' ($($t.schedule) $($t.days) $($t.time))? [y/N]") -match '^[yY]') }
        if ($create) {
            if (-not (Test-Windows)) { Log 'scheduled tasks need Windows - skipped' }
            else {
                $cmd = '"' + (Join-Path $root $t.script) + '"'
                $taskArgs = @('/Create', '/TN', $t.name, '/TR', $cmd, '/SC', $t.schedule, '/ST', $t.time, '/F')
                if ($t.days) { $taskArgs += @('/D', $t.days) }
                & schtasks.exe @taskArgs | Out-Null
                Log "scheduled task '$($t.name)' -> $cmd"
            }
        }
    }
    Log "project '$($p.name)' ready"
}
Write-Host "`nDone. Open a NEW terminal so the environment variables are visible there." -ForegroundColor Green
```
### `code/NSEDATAENGINE/run_at_close.bat`
```
@echo off
REM Today's 15:30 bars for NSEMLENGINE's at-close forecast - scheduled task "NSEDATAENGINE-AtClose", Mon-Fri 14:57.
REM Snapshots at 15:00:00 and 15:30:15 (Fyers quotes) -> data\at_close\YYYY-MM-DD.parquet  Log: data\logs\at_close_log.txt
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "DATADIR=%NSEDATAENGINE_DATA_DIR%"
if "%DATADIR%"=="" set "DATADIR=%~dp0data"
if not exist "%DATADIR%\logs" mkdir "%DATADIR%\logs"
python nse_close_snapshot.py run >> "%DATADIR%\logs\at_close_task.txt" 2>&1
```
### `code/NSEDATAENGINE/run_preopen.bat`
```
@echo off
REM Records NSE's pre-open auction (09:00-09:15) - run by the scheduled task "NSEDATAENGINE-PreOpen", Mon-Fri 08:58.
REM Output: data\preopen\YYYY-MM-DD.parquet   Log: data\logs\preopen_log.txt   (see nse_preopen.py)
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "DATADIR=%NSEDATAENGINE_DATA_DIR%"
if "%DATADIR%"=="" set "DATADIR=%~dp0data"
if not exist "%DATADIR%\logs" mkdir "%DATADIR%\logs"
python nse_preopen.py record >> "%DATADIR%\logs\preopen_task.txt" 2>&1
```
### `code/NSEDATAENGINE/run_update.bat`
```
@echo off
REM Updates the NSE equity dataset: downloads every missing trading day, then rebuilds the feature file.
REM Safe to run daily, weekly or monthly - it only fetches what is missing.
REM %~dp0 = the folder this .bat file is in, so it works wherever the project is unpacked.
cd /d "%~dp0"
REM Data folder: %NSEDATAENGINE_DATA_DIR% (set by migrate\register_project.ps1 from contract\schema.yaml),
REM else the data folder next to this file.
set "DATADIR=%NSEDATAENGINE_DATA_DIR%"
if "%DATADIR%"=="" set "DATADIR=%~dp0data"
if not exist "%DATADIR%\logs" mkdir "%DATADIR%\logs"
python nse_updater.py update >> "%DATADIR%\logs\scheduled_runs.txt" 2>&1
REM NSE sector of every stock (full refresh weekly, missing stocks daily): data_ref\sectors.parquet
python nse_sectors.py >> "%DATADIR%\logs\scheduled_runs.txt" 2>&1
REM delivery %, bulk/block deals, insider trades, ASM/GSM lists, shareholding, pledge, index changes (data_ref)
python nse_alt_data.py daily >> "%DATADIR%\logs\scheduled_runs.txt" 2>&1
REM intraday features from NSEALGOENGINE's 1-minute archive (task NSEALGOENGINE-MinuteArchive 19:05): data_ref\intraday_daily.parquet
python nse_intraday.py daily >> "%DATADIR%\logs\scheduled_runs.txt" 2>&1
```
### `code/NSEMLENGINE/nseml.cmd`
```
@echo off
REM Runs "python -m nseml <args>" from this folder, with %NSEMLENGINE_PYTHON% (the GPU venv set by
REM setup_env.ps1 -Gpu) when it is set, else the python on PATH.   Example:  nseml.cmd baselines --models deeplob
setlocal
set "PY=%NSEMLENGINE_PYTHON%"
if "%PY%"=="" set "PY=python"
pushd "%~dp0"
"%PY%" -m nseml %*
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
```
### `code/NSEMLENGINE/nseml_alt.cmd`
```
@echo off
REM The ALT model (original inputs + delivery %, short selling, MTF): "python -m nseml <args>" with config_alt.yaml
REM and its own work folder (work_alt, or %NSEMLENGINE_ALT_WORK_DIR%). The original model (nseml.cmd, work\) is untouched.
REM   nseml_alt.cmd build                                         dataset with the extra inputs -> work_alt\dataset_v2.parquet
REM   nseml_alt.cmd baselines --models lgbm,deeplob,blend --last 0 --name phase1_alt     walk-forward, same folds as phase1
REM   nseml_alt.cmd predict                                       nightly forecast (the NSEMLENGINE-PredictAlt task)
setlocal
set "PY=%NSEMLENGINE_PYTHON%"
if "%PY%"=="" set "PY=python"
set "NSEMLENGINE_CONFIG=%~dp0config_alt.yaml"
set "NSEMLENGINE_WORK_DIR=%NSEMLENGINE_ALT_WORK_DIR%"
if "%NSEMLENGINE_WORK_DIR%"=="" set "NSEMLENGINE_WORK_DIR=%~dp0work_alt"
pushd "%~dp0"
"%PY%" -m nseml %*
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
```
### `code/NSEMLENGINE/nseml_alt_close.cmd`
```
@echo off
REM The ALT-CLOSE model (original inputs + delivery % / short selling up to yesterday + a 15:37 NOWCAST of today's
REM delivery %, MTF to the day before yesterday): "python -m nseml <args>" with config_alt_close.yaml and its own work
REM folder (work_alt_close, or %NSEMLENGINE_ALT_CLOSE_WORK_DIR%). The original model (nseml.cmd, work\) is untouched.
REM   nseml_alt_close.cmd build                     dataset + delivery nowcast history -> work_alt_close\
REM   nseml_alt_close.cmd baselines --models lgbm,deeplob,blend --last 0 --name phase1_alt_close    walk-forward
REM   nseml_alt_close.cmd predict                   nightly (trains its models when due)  - task NSEMLENGINE-PredictAltClose
REM   nseml_alt_close.cmd predict-close             15:33, from the 15:30 snapshot         - task NSEMLENGINE-PredictCloseAlt
setlocal
set "PY=%NSEMLENGINE_PYTHON%"
if "%PY%"=="" set "PY=python"
set "NSEMLENGINE_CONFIG=%~dp0config_alt_close.yaml"
set "NSEMLENGINE_WORK_DIR=%NSEMLENGINE_ALT_CLOSE_WORK_DIR%"
if "%NSEMLENGINE_WORK_DIR%"=="" set "NSEMLENGINE_WORK_DIR=%~dp0work_alt_close"
pushd "%~dp0"
"%PY%" -m nseml %*
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
```
### `code/NSEMLENGINE/nseml_model.cmd`
```
@echo off
REM Run "python -m nseml <command>" for an ALTERNATE model:  nseml_model.cmd MODEL command [options]
REM   MODEL = the name in config_MODEL.yaml (alt, alt_close, alt_link, ...); its files live in work_MODEL\
REM   nseml_model.cmd alt_link build              (the first build also draws ALT-LINK's link map)
REM   nseml_model.cmd alt_link baselines --models lgbm,deeplob,blend --last 12 --name phase1_alt_link
REM   nseml_model.cmd alt_link predict         /   nseml_model.cmd alt_link predict-close   /   nseml_model.cmd alt_link link-map
REM The original model is run with nseml.cmd (config.yaml, work\) and is never touched by this.
setlocal
set "MODEL=%~1"
if "%MODEL%"=="" (echo usage: nseml_model.cmd MODEL command [options]   e.g. nseml_model.cmd alt_link build & exit /b 2)
if not exist "%~dp0config_%MODEL%.yaml" (echo config_%MODEL%.yaml not found in %~dp0 & exit /b 2)
set "PY=%NSEMLENGINE_PYTHON%"
if "%PY%"=="" set "PY=python"
set "NSEMLENGINE_CONFIG=%~dp0config_%MODEL%.yaml"
set "NSEMLENGINE_WORK_DIR=%~dp0work_%MODEL%"
REM everything after the model name exactly as typed (%%1..%%9 would split "lgbm,deeplob,blend" at the commas)
set "ARGS=%*"
call set "ARGS=%%ARGS:*%MODEL%=%%"
pushd "%~dp0"
"%PY%" -m nseml %ARGS%
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
```
### `code/NSEMLENGINE/register_alt_close_tasks.ps1`
```
<#
.SYNOPSIS
  Scheduled tasks of the ALT-CLOSE model (one Windows password prompt for both). The original model's tasks are not changed.

    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEMLENGINE\register_alt_close_tasks.ps1

  NSEMLENGINE-PredictCloseAlt   Mon-Fri 15:33  run_predict_close_alt.bat  15:37 forecast with yesterday's delivery /
                                short selling + today's delivery NOWCAST -> work_alt_close\predictions\latest_close.csv
                                (needs NSEDATAENGINE-AtClose, the 15:00/15:30 snapshot task)
  NSEMLENGINE-PredictAltClose   Mon-Fri 21:20  run_predict_alt_close.bat  nightly: dataset + nowcast history, retrain when
                                due, forecast, close_check.txt
  Runs whether you are logged on or not (Password logon); -Interactive: only while logged on.
  Remove:  Unregister-ScheduledTask NSEMLENGINE-PredictCloseAlt -Confirm:$false; Unregister-ScheduledTask NSEMLENGINE-PredictAltClose -Confirm:$false
#>
param([string]$CloseAt = "15:33", [string]$NightAt = "21:20", [switch]$Interactive)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$jobs = @(
    @{ Name = "NSEMLENGINE-PredictCloseAlt"; Bat = "run_predict_close_alt.bat"; At = $CloseAt; Min = 30
       Desc = "NSEMLENGINE ALT-CLOSE: 15:37 forecast with yesterday's delivery data + today's delivery nowcast (no orders)" },
    @{ Name = "NSEMLENGINE-PredictAltClose"; Bat = "run_predict_alt_close.bat"; At = $NightAt; Min = 240
       Desc = "NSEMLENGINE ALT-CLOSE: nightly dataset, nowcast history, retraining, forecast, close check" }
)
foreach ($j in $jobs) { if (-not (Test-Path (Join-Path $root $j.Bat))) { throw "not found: $($j.Bat)" } }
$user = "$env:USERDOMAIN\$env:USERNAME"
$cred = $null
if (-not $Interactive) { $cred = Get-Credential -UserName $user -Message "Windows password for the ALT-CLOSE tasks (run while logged off)" }
foreach ($j in $jobs) {
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$(Join-Path $root $j.Bat)`"" -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $j.At
    $settings = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes $j.Min) -MultipleInstances IgnoreNew
    if ($Interactive) {
        $p = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $j.Name -Action $action -Trigger $trigger -Settings $settings -Principal $p -Description $j.Desc -Force | Out-Null
    } else {
        Register-ScheduledTask -TaskName $j.Name -Action $action -Trigger $trigger -Settings $settings `
            -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Description $j.Desc -Force | Out-Null
    }
    $t = Get-ScheduledTask -TaskName $j.Name
    Write-Host ("Registered {0}: Mon-Fri {1}, logon {2}" -f $j.Name, $j.At, $t.Principal.LogonType) -ForegroundColor Green
}
```
### `code/NSEMLENGINE/register_alt_task.ps1`
```
<#
.SYNOPSIS
  Scheduled task "NSEMLENGINE-PredictAlt": nightly forecasts of the ALT model (original inputs + delivery %, short
  selling, MTF), next to the original NSEMLENGINE-Predict (20:00), which is not changed.

    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEMLENGINE\register_alt_task.ps1

  Mon-Fri 20:40 -> run_predict_alt.bat -> work_alt\predictions\latest.csv (first run trains its own models, ~30-60 min).
  Runs whether you are logged on or not (asks for your Windows password once); -Interactive: only while logged on.
  Remove:  Unregister-ScheduledTask NSEMLENGINE-PredictAlt -Confirm:$false
#>
param([string]$At = "20:40", [switch]$Interactive)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$bat = Join-Path $root "run_predict_alt.bat"
if (-not (Test-Path $bat)) { throw "not found: $bat" }
$name = "NSEMLENGINE-PredictAlt"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $At
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) -MultipleInstances IgnoreNew
$desc = "NSEMLENGINE ALT model (with delivery / short selling / MTF inputs): next-session forecasts"
$user = "$env:USERDOMAIN\$env:USERNAME"
if ($Interactive) {
    $p = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $p -Description $desc -Force | Out-Null
} else {
    $cred = Get-Credential -UserName $user -Message "Windows password for $name (runs while you are logged off)"
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
        -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Description $desc -Force | Out-Null
}
$t = Get-ScheduledTask -TaskName $name
Write-Host ("Registered {0}: Mon-Fri {1}, logon {2}" -f $name, $At, $t.Principal.LogonType) -ForegroundColor Green
```
### `code/NSEMLENGINE/register_close_intraday_task.ps1`
```
<#
.SYNOPSIS
  Scheduled task for the ALT-INTRADAY 15:37 at-close forecast (one Windows password prompt):

    powershell -ExecutionPolicy Bypass -File D:\NSE\NSEMLENGINE\register_close_intraday_task.ps1

  NSEMLENGINE-PredictClose-alt_intraday  Mon-Fri 15:37  run_predict_close_alt_intraday.bat
      1) NSEALGOENGINE  nsealgo prefetch-today --unattended --no-wait-idle   (top-up: today's 1-minute bars)
      2) NSEDATAENGINE  python nse_intraday.py daily                          (today's intraday row, ok on session shape)
      3) NSEMLENGINE    nseml_model.cmd alt_intraday_gpu predict-close        (work_alt_intraday_gpu)
    Needs: the day's Fyers token (NSEUIENGINE-FyersToken 08:00) and the 15:30 snapshot (NSEDATAENGINE-AtClose 14:57).
    NOTHING here places orders. Log: work_alt_intraday_gpu\logs\scheduled_runs.txt

  Runs whether you are logged on or not (Password logon; for a Microsoft-account / PIN sign-in enter the Microsoft
  account password); wake to run on. -Interactive: only while logged on, no password asked.
  The ALT-INTRADAY nightly task (train + forecast, e.g. 23:00) is separate:
      powershell -ExecutionPolicy Bypass -File register_model_tasks.ps1 -Model alt_intraday_gpu -NightAt 23:00 -NoClose
  Remove: Unregister-ScheduledTask NSEMLENGINE-PredictClose-alt_intraday -Confirm:$false
#>
param([string]$At = "15:37", [int]$Min = 45, [switch]$Interactive)
$ErrorActionPreference = "Stop"
$ml = $PSScriptRoot
$bat = Join-Path $ml "run_predict_close_alt_intraday.bat"
if (-not (Test-Path $bat)) { throw "not found: $bat" }
$name = "NSEMLENGINE-PredictClose-alt_intraday"
$user = "$env:USERDOMAIN\$env:USERNAME"
$cred = $null
if (-not $Interactive) { $cred = Get-Credential -UserName $user -Message "Windows password for $name (run while logged off)" }

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $ml
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes $Min) -MultipleInstances IgnoreNew
$desc = "NSEMLENGINE ALT-INTRADAY (alt_intraday_gpu): 15:37 at-close forecast - minute-bar top-up + intraday row + predict-close (no orders)"
if ($Interactive) {
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description $desc -Force | Out-Null
} else {
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
        -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Description $desc -Force | Out-Null
}
$t = Get-ScheduledTask -TaskName $name
Write-Host ("Registered {0}: Mon-Fri {1}, logon {2}, limit {3} min" -f $name, $At, $t.Principal.LogonType, $Min) -ForegroundColor Green
Write-Host "Dry run (needs a trading day + the day's Fyers token):  Start-ScheduledTask $name"
Write-Host "Check after:  $ml\work_alt_intraday_gpu\logs\scheduled_runs.txt   and   work_alt_intraday_gpu\predictions\latest_close.csv"
```
### `code/NSEMLENGINE/register_close_tasks.ps1`
```
<#
.SYNOPSIS
  The two scheduled tasks of the at-close forecast (one Windows password prompt for both):

    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEMLENGINE\register_close_tasks.ps1

  NSEDATAENGINE-AtClose     Mon-Fri 14:57  NSEDATAENGINE\run_at_close.bat -> nse_close_snapshot.py run
                            Fyers quotes at 15:00:00 and 15:30:15 -> today's bar per stock, close = 15:00-15:30 VWAP
  NSEMLENGINE-PredictClose  Mon-Fri 15:31  run_predict_close.bat -> python -m nseml predict-close
                            next-session forecast with the newest nightly models, ready ~15:33-15:37
                            -> work\predictions\latest_close.csv
  The nightly NSEMLENGINE-Predict (20:00) then writes work\predictions\close_check.txt: at-close vs nightly forecast,
  snapshot close vs NSE's close, and the paper overnight trade (buy the top predicted gap-ups at the close, sell at
  the next open). NOTHING here places orders.

  Runs whether you are logged on or not (Password logon; for a Microsoft-account / PIN sign-in enter the Microsoft
  account password); wake to run on. -Interactive: only while logged on, no password asked.
  Remove:  Unregister-ScheduledTask NSEDATAENGINE-AtClose -Confirm:$false; Unregister-ScheduledTask NSEMLENGINE-PredictClose -Confirm:$false
#>
param([string]$SnapAt = "14:57", [string]$PredictAt = "15:31", [switch]$Interactive)
$ErrorActionPreference = "Stop"
$ml = $PSScriptRoot
$data = if ($env:NSEDATAENGINE_HOME) { $env:NSEDATAENGINE_HOME } else { Join-Path (Split-Path $ml -Parent) "NSEDATAENGINE" }
$jobs = @(
    @{ Name = "NSEDATAENGINE-AtClose"; Bat = (Join-Path $data "run_at_close.bat"); Dir = $data; At = $SnapAt; Min = 45
       Desc = "NSEDATAENGINE: 15:00 / 15:30 Fyers snapshot -> today's bars for the at-close forecast" },
    @{ Name = "NSEMLENGINE-PredictClose"; Bat = (Join-Path $ml "run_predict_close.bat"); Dir = $ml; At = $PredictAt; Min = 30
       Desc = "NSEMLENGINE: next-session forecast from today's 15:30 snapshot (no orders)" }
)
foreach ($j in $jobs) { if (-not (Test-Path $j.Bat)) { throw "not found: $($j.Bat)" } }
$user = "$env:USERDOMAIN\$env:USERNAME"
$cred = $null
if (-not $Interactive) { $cred = Get-Credential -UserName $user -Message "Windows password for the at-close tasks (run while logged off)" }
foreach ($j in $jobs) {
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$($j.Bat)`"" -WorkingDirectory $j.Dir
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $j.At
    $settings = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes $j.Min) -MultipleInstances IgnoreNew
    if ($Interactive) {
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $j.Name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
            -Description $j.Desc -Force | Out-Null
    } else {
        Register-ScheduledTask -TaskName $j.Name -Action $action -Trigger $trigger -Settings $settings `
            -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Description $j.Desc -Force | Out-Null
    }
    $t = Get-ScheduledTask -TaskName $j.Name
    Write-Host ("Registered {0}: Mon-Fri {1}, logon {2}" -f $j.Name, $j.At, $t.Principal.LogonType) -ForegroundColor Green
}
Write-Host "Check after a trading day:  python $data\nse_close_snapshot.py show   and   work\predictions\latest_close.csv"
Write-Host "Nightly comparison:          work\predictions\close_check.txt  (after the 20:00 forecast)"
```
### `code/NSEMLENGINE/register_model_tasks.ps1`
```
<#
.SYNOPSIS
  Scheduled tasks for an ALTERNATE model (config_MODEL.yaml, work_MODEL\), one Windows password prompt.

    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEMLENGINE\register_model_tasks.ps1 -Model alt_link

  NSEMLENGINE-Predict-MODEL       Mon-Fri -NightAt (default 22:00)  run_model.bat MODEL predict
                                  nightly: dataset, retrain when due, forecast, close_check.txt
  NSEMLENGINE-PredictClose-MODEL  Mon-Fri -CloseAt (default 15:35)  run_model.bat MODEL predict-close
                                  15:37 forecast from the 15:30 snapshot (skip with -NoClose; only for models that are
                                  usable at 15:37, i.e. alt_lag: 1 - alt_close, alt_link)
  -Interactive: only while logged on (no password). The original model's tasks are not changed.
  Remove:  Unregister-ScheduledTask NSEMLENGINE-Predict-alt_link -Confirm:$false  (and ...PredictClose-alt_link)
#>
param([Parameter(Mandatory = $true)][string]$Model, [string]$NightAt = "22:00", [string]$CloseAt = "15:35",
      [switch]$NoClose, [switch]$Interactive)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
if (-not (Test-Path (Join-Path $root "config_$Model.yaml"))) { throw "config_$Model.yaml not found in $root" }
$bat = Join-Path $root "run_model.bat"
$jobs = @(@{ Name = "NSEMLENGINE-Predict-$Model"; Cmd = "predict"; At = $NightAt; Min = 240 })
if (-not $NoClose) { $jobs += @{ Name = "NSEMLENGINE-PredictClose-$Model"; Cmd = "predict-close"; At = $CloseAt; Min = 30 } }
$user = "$env:USERDOMAIN\$env:USERNAME"
$cred = $null
if (-not $Interactive) { $cred = Get-Credential -UserName $user -Message "Windows password for the $Model model tasks (run while logged off)" }
foreach ($j in $jobs) {
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"`"$bat`" $Model $($j.Cmd)`"" -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $j.At
    $settings = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes $j.Min) -MultipleInstances IgnoreNew
    $desc = "NSEMLENGINE $Model model: $($j.Cmd) (config_$Model.yaml, work_$Model)"
    if ($Interactive) {
        $p = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $j.Name -Action $action -Trigger $trigger -Settings $settings -Principal $p -Description $desc -Force | Out-Null
    } else {
        Register-ScheduledTask -TaskName $j.Name -Action $action -Trigger $trigger -Settings $settings `
            -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Description $desc -Force | Out-Null
    }
    $t = Get-ScheduledTask -TaskName $j.Name
    Write-Host ("Registered {0}: Mon-Fri {1}, logon {2}" -f $j.Name, $j.At, $t.Principal.LogonType) -ForegroundColor Green
}
```
### `code/NSEMLENGINE/register_tasks.ps1`
```
<#
.SYNOPSIS
    Registers the NSEMLENGINE scheduled task "NSEMLENGINE-Predict" (no admin rights needed).

.DESCRIPTION
    Monday-Friday at -At (default 20:00 IST, after the NSEDATAENGINE "NSE data update" at 19:30) it runs
    run_predict.bat: waits until today's data is in NSEDATAENGINE (up to live.wait_minutes), retrains the
    models when they are older than live.retrain_every_days, and writes work\predictions\latest.csv.
    Runs while you are logged on, wakes the PC, and catches up a missed run as soon as possible.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEMLENGINE\register_tasks.ps1
    Start-ScheduledTask NSEMLENGINE-Predict          # run it now
    Unregister-ScheduledTask NSEMLENGINE-Predict -Confirm:$false
#>
param([string]$At = "20:00", [string]$TaskName = "NSEMLENGINE-Predict")
$ErrorActionPreference = "Stop"
$root = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$bat = Join-Path $root "run_predict.bat"
if (-not (Test-Path $bat)) { throw "run_predict.bat not found in $root" }
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $At
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 4) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "NSEMLENGINE: next-session OHLCV forecasts after the NSEDATAENGINE update" -Force | Out-Null
Write-Host "Registered '$TaskName': Mon-Fri $At -> $bat"
Write-Host "Run now: Start-ScheduledTask $TaskName    Log: work\logs\predict_log.txt    Output: work\predictions\latest.csv"
```
### `code/NSEMLENGINE/run_alt_link_gpu_predict.bat`
```
@echo off
REM One-off GPU test run for ALT-LINK-GPU (29-Oct-2026): retrain + predict on the Radeon 8060S,
REM unrolled-LSTM DeepLOB (see config_alt_link_gpu.yaml). Log: work_alt_link_gpu\logs\gpu_predict_run.txt
cd /d "%~dp0"
set "WORK=%~dp0work_alt_link_gpu"
call "%~dp0nseml_model.cmd" alt_link_gpu predict --retrain --force --wait 0 >> "%WORK%\logs\gpu_predict_run.txt" 2>&1
exit /b %ERRORLEVEL%
```
### `code/NSEMLENGINE/run_alt_walkforward.bat`
```
@echo off
REM Walk-forward test of the ALTERNATE models on the same 12 test quarters as the original model (phase1), one model
REM after another, then the fold-by-fold comparisons (blend = lgbm + deeplob average, what the live forecasts use).
REM   run_alt_walkforward.bat                  alt, alt_close and alt_link
REM   run_alt_walkforward.bat alt_link         only the model(s) named
REM   run_alt_walkforward.bat alt_intraday     ALT-INTRADAY = ALT + intraday inputs (nightly only; needs NSEDATAENGINE
REM                                            data_ref\intraday_daily.parquet from nse_intraday.py)
REM Each model: build (5-15 min; ALT-LINK's first build also draws its link map) + 12 folds of lgbm + deeplob
REM (about 3.5-4 h on the HP). Resumable: run it again and finished folds are skipped.
REM Progress: work_MODEL\results\phase1_MODEL\log.txt    Results: compare_*.txt in work_alt*\results\
REM Needs NSEDATAENGINE's short selling / MTF history first:  python nse_alt_data.py backfill --start 2019-01-01 --only short_selling,mtf
setlocal EnableDelayedExpansion
set "FAILED="
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PY=%NSEMLENGINE_PYTHON%"
if "%PY%"=="" set "PY=python"
set "MODELS=%*"
if "%MODELS%"=="" set "MODELS=alt alt_close alt_link"
for %%M in (%MODELS%) do (
  echo ==== %%M: inputs
  call "%~dp0nseml_model.cmd" %%M paths
  if errorlevel 1 (echo STOPPED: inputs of %%M are missing - see above & exit /b 1)
)
for %%M in (%MODELS%) do (
  echo.
  echo ==== %%M: dataset
  call "%~dp0nseml_model.cmd" %%M build
  if errorlevel 1 (
    echo FAILED: build of %%M - skipped, the other models still run; fix and rerun: run_alt_walkforward.bat %%M
    set "FAILED=!FAILED! %%M"
  ) else (
    echo ==== %%M: walk-forward, last 12 folds
    call "%~dp0nseml_model.cmd" %%M baselines --models lgbm,deeplob,blend --last 12 --name phase1_%%M
    if errorlevel 1 (
      echo FAILED: walk-forward of %%M - rerun run_alt_walkforward.bat %%M, finished folds are kept
      set "FAILED=!FAILED! %%M"
    )
  )
)
set "REF=work\results\phase1_pc"
if not exist "%REF%" set "REF=work\results\phase1"
echo.
echo ==== comparisons (model blend; A = reference, B = challenger)
if exist "work_alt\results\phase1_alt" "%PY%" compare_models.py --a %REF% --b work_alt\results\phase1_alt
if exist "work_alt_close\results\phase1_alt_close" "%PY%" compare_models.py --a %REF% --b work_alt_close\results\phase1_alt_close
if exist "work_alt_link\results\phase1_alt_link" "%PY%" compare_models.py --a %REF% --b work_alt_link\results\phase1_alt_link
if exist "work_alt_link\results\phase1_alt_link" if exist "work_alt_close\results\phase1_alt_close" "%PY%" compare_models.py --a work_alt_close\results\phase1_alt_close --b work_alt_link\results\phase1_alt_link
if exist "work_alt_intraday\results\phase1_alt_intraday" "%PY%" compare_models.py --a %REF% --b work_alt_intraday\results\phase1_alt_intraday
if exist "work_alt_intraday\results\phase1_alt_intraday" if exist "work_alt\results\phase1_alt" "%PY%" compare_models.py --a work_alt\results\phase1_alt --b work_alt_intraday\results\phase1_alt_intraday
if exist "work_alt_intraday_gpu\results\phase1_alt_intraday_gpu" "%PY%" compare_models.py --a %REF% --b work_alt_intraday_gpu\results\phase1_alt_intraday_gpu
if exist "work_alt_intraday_gpu\results\phase1_alt_intraday_gpu" if exist "work_alt\results\phase1_alt" "%PY%" compare_models.py --a work_alt\results\phase1_alt --b work_alt_intraday_gpu\results\phase1_alt_intraday_gpu
echo.
if not "!FAILED!"=="" echo NOT FINISHED:!FAILED! - see FAILED lines above
echo Done. Comparison files: compare_*.txt in work_alt\results, work_alt_close\results, work_alt_link\results and work_alt_intraday\results
```
### `code/NSEMLENGINE/run_gpu_predicts_all.bat`
```
@echo off
REM 29-Oct-2026: first GPU runs for ORIG / ALT / ALT-CLOSE (ALT-LINK-GPU already ran OK at 04:14).
REM Sequential on one GPU; each retrains (no models yet in the GPU work folders) + predicts.
cd /d "%~dp0"
set "PYTHONUTF8=1"
call "%~dp0nseml_model.cmd" orig_gpu predict --wait 0 >> "%~dp0work_orig_gpu\logs\gpu_predict_run.txt" 2>&1
call "%~dp0nseml_model.cmd" alt_gpu predict --wait 0 >> "%~dp0work_alt_gpu\logs\gpu_predict_run.txt" 2>&1
call "%~dp0nseml_model.cmd" alt_close_gpu predict --wait 0 >> "%~dp0work_alt_close_gpu\logs\gpu_predict_run.txt" 2>&1
```
### `code/NSEMLENGINE/run_model.bat`
```
@echo off
REM Scheduled-task entry for an alternate model:  run_model.bat MODEL predict | predict-close
REM Output and log in work_MODEL\ (logs\scheduled_runs.txt, predictions\latest.csv / latest_close.csv).
cd /d "%~dp0"
set "WORK=%~dp0work_%~1"
if not exist "%WORK%\logs" mkdir "%WORK%\logs"
set "PYTHONUTF8=1"
call "%~dp0nseml_model.cmd" %1 %2 >> "%WORK%\logs\scheduled_runs.txt" 2>&1
```
### `code/NSEMLENGINE/run_predict.bat`
```
@echo off
REM NSEMLENGINE nightly forecasts - scheduled task "NSEMLENGINE-Predict" (register_tasks.ps1).
REM 29-Oct-2026: switched to the GPU model (ORIG-GPU: config_orig_gpu.yaml, work_orig_gpu) on the Radeon 8060S.
REM Previous (CPU) version kept in run_predict.bat.bak_pre_gpu. Waits for the NSEDATAENGINE 19:30 update,
REM retrains when due, writes work_orig_gpu\predictions\latest.csv.
cd /d "%~dp0"
set "WORK=%~dp0work_orig_gpu"
if not exist "%WORK%\logs" mkdir "%WORK%\logs"
set "PYTHONUTF8=1"
call "%~dp0nseml_model.cmd" orig_gpu predict >> "%WORK%\logs\scheduled_runs.txt" 2>&1
exit /b %ERRORLEVEL%
```
### `code/NSEMLENGINE/run_predict_alt.bat`
```
@echo off
REM NSEMLENGINE ALT model nightly forecasts - scheduled task "NSEMLENGINE-PredictAlt" (register_alt_task.ps1).
REM 29-Oct-2026: switched to the GPU model (ALT-GPU: config_alt_gpu.yaml, work_alt_gpu) on the Radeon 8060S.
REM Previous (CPU) version kept in run_predict_alt.bat.bak_pre_gpu. -> work_alt_gpu\predictions\latest.csv
cd /d "%~dp0"
set "WORK=%~dp0work_alt_gpu"
if not exist "%WORK%\logs" mkdir "%WORK%\logs"
set "PYTHONUTF8=1"
call "%~dp0nseml_model.cmd" alt_gpu predict >> "%WORK%\logs\scheduled_runs.txt" 2>&1
exit /b %ERRORLEVEL%
```
### `code/NSEMLENGINE/run_predict_alt_close.bat`
```
@echo off
REM ALT-CLOSE model, nightly (task NSEMLENGINE-PredictAltClose).
REM 29-Oct-2026: switched to the GPU model (ALT-CLOSE-GPU: config_alt_close_gpu.yaml, work_alt_close_gpu)
REM on the Radeon 8060S. Previous (CPU) version kept in run_predict_alt_close.bat.bak_pre_gpu.
REM Rebuilds its dataset, extends the delivery nowcast, retrains when due, forecasts, then writes
REM work_alt_close_gpu\predictions\close_check.txt (15:37 vs nightly + paper trade).
cd /d "%~dp0"
set "WORK=%~dp0work_alt_close_gpu"
if not exist "%WORK%\logs" mkdir "%WORK%\logs"
set "PYTHONUTF8=1"
call "%~dp0nseml_model.cmd" alt_close_gpu predict >> "%WORK%\logs\scheduled_runs.txt" 2>&1
exit /b %ERRORLEVEL%
```
### `code/NSEMLENGINE/run_predict_close.bat`
```
@echo off
REM NSEMLENGINE at-close forecast - scheduled task "NSEMLENGINE-PredictClose", Mon-Fri 15:31.
REM 29-Oct-2026: switched to the GPU model (ORIG-GPU: work_orig_gpu). Waits (up to 10 min) for the
REM NSEDATAENGINE 15:30 snapshot, forecasts the next session with the newest GPU nightly models
REM -> work_orig_gpu\predictions\predictions_close_YYYYMMDD.csv + latest_close.csv.
REM Previous (CPU) version kept in run_predict_close.bat.bak_pre_gpu. Log: work_orig_gpu\logs\
cd /d "%~dp0"
set "WORK=%~dp0work_orig_gpu"
if not exist "%WORK%\logs" mkdir "%WORK%\logs"
set "PYTHONUTF8=1"
call "%~dp0nseml_model.cmd" orig_gpu predict-close >> "%WORK%\logs\scheduled_runs.txt" 2>&1
exit /b %ERRORLEVEL%
```
### `code/NSEMLENGINE/run_predict_close_alt.bat`
```
@echo off
REM ALT-CLOSE model at 15:33 (task NSEMLENGINE-PredictCloseAlt): waits for NSEDATAENGINE's 15:30 snapshot,
REM forecasts the next session with yesterday's delivery data + today's delivery nowcast.
REM 29-Oct-2026: switched to the GPU model (ALT-CLOSE-GPU: work_alt_close_gpu)
REM -> work_alt_close_gpu\predictions\latest_close.csv.
REM Previous (CPU) version kept in run_predict_close_alt.bat.bak_pre_gpu.
cd /d "%~dp0"
set "WORK=%~dp0work_alt_close_gpu"
if not exist "%WORK%\logs" mkdir "%WORK%\logs"
set "PYTHONUTF8=1"
call "%~dp0nseml_model.cmd" alt_close_gpu predict-close >> "%WORK%\logs\scheduled_runs.txt" 2>&1
exit /b %ERRORLEVEL%
```
### `code/NSEMLENGINE/run_predict_close_alt_intraday.bat`
```
@echo off
REM ALT-INTRADAY at-close forecast - scheduled task "NSEMLENGINE-PredictClose-alt_intraday", Mon-Fri 15:37.
REM Today's full-day intraday features are built from NSEALGOENGINE's 1-minute archive, which the intraday
REM prefetch (NSEALGOENGINE-MinutePrefetch, every 15 min) has been filling with today's bars all day. This task:
REM   1) NSEALGOENGINE  nsealgo prefetch-today --unattended --no-wait-idle
REM      (top-up: today's complete bars into partial_<date>.parquet; --no-wait-idle: the market is CLOSED at 15:37,
REM       resting overnight orders are the normal state and must not skip the forecast; the idle gate is for the
REM       intraday rounds only, to keep the Fyers data calls away from live order flow)
REM   2) NSEDATAENGINE  python nse_intraday.py daily          (build today's row -> data_ref\intraday_daily.parquet)
REM   3) NSEMLENGINE    nseml_model.cmd alt_intraday_gpu predict-close   (the 15:37 forecast)
REM Uses the day's Fyers token from NSEUIENGINE's 08:00 login. Log: work_alt_intraday_gpu\logs\scheduled_runs.txt
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "WORK=%~dp0work_alt_intraday_gpu"
if not exist "%WORK%\logs" mkdir "%WORK%\logs"
set "LOG=%WORK%\logs\scheduled_runs.txt"

REM 1) NSEALGOENGINE prefetch (top-up) - NSEALGOENGINE's venv python
set "ALGO=%~dp0..\NSEALGOENGINE"
set "ALGOPY=%ALGO%\.venv\Scripts\python.exe"
if not exist "%ALGOPY%" set "ALGOPY=python"
pushd "%ALGO%"
"%ALGOPY%" -m nsealgo prefetch-today --unattended --no-wait-idle >> "%LOG%" 2>&1
set "RC1=%errorlevel%"
popd
echo [at-close] nsealgo prefetch-today exit %RC1% >> "%LOG%"

REM 2) NSEDATAENGINE intraday row - system python (NSEDATAENGINE has no venv)
set "DATA=%~dp0..\NSEDATAENGINE"
pushd "%DATA%"
python nse_intraday.py daily >> "%LOG%" 2>&1
set "RC2=%errorlevel%"
popd
echo [at-close] nse_intraday.py daily exit %RC2% >> "%LOG%"

REM 3) NSEMLENGINE at-close forecast (alt_intraday_gpu)
call "%~dp0nseml_model.cmd" alt_intraday_gpu predict-close >> "%LOG%" 2>&1
set "RC=%errorlevel%"
if %RC1% neq 0 set "RC=%RC1%"
if %RC2% neq 0 set "RC=%RC2%"
exit /b %RC%
```
### `code/NSEMLENGINE/setup_env.ps1`
```
<#
.SYNOPSIS
    Sets the NSEMLENGINE environment variables for the current Windows user (no admin rights needed),
    and with -Gpu builds a Python 3.12 virtual environment with PyTorch for the Radeon GPU.

.DESCRIPTION
    Always:  NSEMLENGINE_HOME, NSEMLENGINE_WORK_DIR (and NSEDATAENGINE_HOME if it is not set yet).
    -Gpu:    creates <project>\.venv with Python 3.12 (AMD's ROCm PyTorch wheels for Windows are cp312 only),
             installs requirements.txt and AMD's torch 2.9.1 + ROCm 7.2.1 wheels (the same ones NSEALGOENGINE
             uses), checks that the GPU is visible, and sets NSEMLENGINE_PYTHON to that venv's python.exe.
             Needs the "AMD Software: PyTorch on Windows Edition" graphics driver installed first.
    nseml.cmd runs "python -m nseml" with %NSEMLENGINE_PYTHON% when it is set, else the python on PATH.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEMLENGINE\setup_env.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEMLENGINE\setup_env.ps1 -Gpu
#>
param(
    [string]$ProjectRoot,
    [string]$WorkDir,
    [string]$DataEngineHome,
    [switch]$Gpu
)
$ErrorActionPreference = 'Stop'
if (-not $ProjectRoot) { $ProjectRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path } }
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
if (-not $WorkDir) { $WorkDir = Join-Path $ProjectRoot 'work' }
$vars = [ordered]@{ NSEMLENGINE_HOME = $ProjectRoot; NSEMLENGINE_WORK_DIR = $WorkDir }
# NSEDATAENGINE_HOME is owned by NSEDATAENGINE (its register_project.ps1); only set it if missing.
$existing = [Environment]::GetEnvironmentVariable('NSEDATAENGINE_HOME', 'User')
if ($DataEngineHome) { $vars['NSEDATAENGINE_HOME'] = (Resolve-Path $DataEngineHome).Path }
elseif (-not $existing) {
    $guess = Join-Path (Split-Path -Parent $ProjectRoot) 'NSEDATAENGINE'
    if (Test-Path $guess) { $vars['NSEDATAENGINE_HOME'] = $guess }
}

if ($Gpu) {
    $venv = Join-Path $ProjectRoot '.venv'
    $py = Join-Path $venv 'Scripts\python.exe'
    if (-not (Test-Path $py)) {
        Write-Host 'Creating .venv with Python 3.12 ...'
        & py -3.12 -m venv $venv
        if (-not (Test-Path $py)) { throw 'Python 3.12 not found (py -3.12). Install it (winget install Python.Python.3.12) and run again.' }
    }
    & $py -m pip install --disable-pip-version-check -q --upgrade pip
    # everything except torch from requirements.txt, then AMD's ROCm build of torch
    $req = Get-Content (Join-Path $ProjectRoot 'requirements.txt') | Where-Object { $_ -notmatch '^\s*torch' }
    $tmp = Join-Path $env:TEMP 'nseml_requirements_gpu.txt'
    $req | Set-Content -Path $tmp -Encoding ASCII
    & $py -m pip install --disable-pip-version-check -q -r $tmp
    if ($LASTEXITCODE -ne 0) { throw 'pip install of the requirements failed' }
    $r = 'https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1'
    & $py -m pip install --disable-pip-version-check --no-cache-dir "$r/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl" `
        "$r/rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl" "$r/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl" `
        "$r/rocm-7.2.1.tar.gz"
    & $py -m pip install --disable-pip-version-check --no-cache-dir "$r/torch-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl"
    if ($LASTEXITCODE -ne 0) { throw 'ROCm PyTorch install failed' }
    & $py -c "import torch; ok = torch.cuda.is_available(); print('PyTorch', torch.__version__, '| GPU:', torch.cuda.get_device_name(0) if ok else 'NOT available - install the AMD PyTorch driver; deeplob will run on the CPU')"
    $vars['NSEMLENGINE_PYTHON'] = $py
}

foreach ($k in $vars.Keys) {
    [Environment]::SetEnvironmentVariable($k, $vars[$k], 'User')
    Set-Item -Path "Env:$k" -Value $vars[$k]
    Write-Host ("{0,-22} = {1}" -f $k, $vars[$k])
}
Write-Host 'Done. Open a NEW terminal, then:  %NSE_ROOT%\code\NSEMLENGINE\nseml.cmd paths'
```
### `code/NSEUIENGINE/SET_STATIC_IP.cmd`
```
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0set_static_ip.ps1"
echo.
pause
```
### `code/NSEUIENGINE/go_live.ps1`
```
<#
.SYNOPSIS
  NSEUIENGINE: one-shot setup + GO / NO-GO check for live trading.

    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEUIENGINE\go_live.ps1 -CsvPath "<staticip.in csv>"
    ... -GoLive      also switch the trader to LIVE if every check passes

  1. finds how the staticip.in proxy works (http / https) and checks traffic really leaves from your static IP
  2. saves STATICIP_PROXY and STATIC_IP as user environment variables
  3. registers the trader's scheduled tasks (setup_swing_trader.ps1)
  4. runs the evening scan for the next session (NSEDATAENGINE update + signals, ~3-5 minutes)
  5. runs preflight.py check: login, DDPI, static IP route, signals, plan, funds, tasks
#>
param([string]$CsvPath = "", [switch]$GoLive)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$data = if ($env:NSEUIENGINE_DATA_DIR) { $env:NSEUIENGINE_DATA_DIR } elseif ($env:NSE_DATA_ROOT) { Join-Path $env:NSE_DATA_ROOT "NSEUIENGINE" } else { $root }   # state / reports / logs
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "no .venv in $root - run setup_fyers_token.ps1 first" }

if (-not $CsvPath) {
    $f = Get-ChildItem "$env:USERPROFILE\Downloads\*ip_details*.csv" -ErrorAction SilentlyContinue |
         Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($f) { $CsvPath = $f.FullName } else { throw "pass -CsvPath <the staticip.in csv>" }
}
Write-Host "== 1. static IP proxy ($CsvPath)"
$out = & $py (Join-Path $root "preflight.py") proxy --csv $CsvPath
if ($LASTEXITCODE -ne 0) { throw "the staticip.in proxy did not work - see the messages above" }
$j = ($out | Select-Object -Last 1) | ConvertFrom-Json

Write-Host "== 2. environment variables"
[Environment]::SetEnvironmentVariable("STATICIP_PROXY", $j.proxy, "User"); $env:STATICIP_PROXY = $j.proxy
[Environment]::SetEnvironmentVariable("STATIC_IP", $j.ip, "User"); $env:STATIC_IP = $j.ip
Write-Host "   STATIC_IP = $($j.ip)   (subscription valid until $($j.validity) - renew before then)"

Write-Host "== 3. scheduled tasks"
& (Join-Path $root "setup_swing_trader.ps1")

Write-Host "== 4. evening scan for the next session (takes a few minutes)"
& $py (Join-Path $root "swing_trader.py") evening

Write-Host "== 5. pre-flight check"
& $py (Join-Path $root "preflight.py") check
$go = ($LASTEXITCODE -eq 0)

if ($GoLive) {
    if ($go) {
        $cfgPath = Join-Path $data "state\trader_config.json"
        $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
        $cfg.live = $true
        $cfg | ConvertTo-Json -Depth 5 | Set-Content $cfgPath -Encoding UTF8
        Write-Host "`nLIVE: the 08:58 task will place real orders." -ForegroundColor Green
    } else {
        Write-Host "`nNot switched to live - fix the FAIL items and run again." -ForegroundColor Yellow
    }
}
```
### `code/NSEUIENGINE/make_tasks_run_logged_off.ps1`
```
<#
.SYNOPSIS
  Make every NSE engine scheduled task run whether you are logged on or not.

    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEUIENGINE\make_tasks_run_logged_off.ps1

  The tasks were registered "run only when the user is logged on" (Interactive), so nothing ran while you were
  signed out. This switches them to "run whether the user is logged on or not" with your Windows password stored
  in Task Scheduler (Password logon - this keeps your profile, the registry variables and the DPAPI-encrypted Fyers
  secrets working; "do not store password"/S4U would break them). It also turns on Wake to run and Run as soon as
  possible after a missed start.

  Asks for your Windows password once. If you sign in with a PIN or Windows Hello, enter the account's password
  (for a Microsoft account: the Microsoft account password). Re-run this after re-running any setup / register
  script, because those register the tasks as Interactive again, and after you change your Windows password.

  Still needed: the PC must be ON or asleep (not shut down or hibernated) at 08:00 and 08:58 on trading days.
  Windows pop-up alerts (msg.exe) are not shown while you are logged off - read state\ALERTS.txt instead.
#>
param([string[]]$Match = @("NSE*", "*NSEDATAENGINE*", "*NSEMLENGINE*", "*NSEALGOENGINE*", "*NSEUIENGINE*"))
$ErrorActionPreference = "Stop"
$tasks = Get-ScheduledTask | Where-Object { $n = $_.TaskName; ($Match | Where-Object { $n -like $_ }).Count -gt 0 } |
    Sort-Object TaskName -Unique
if (-not $tasks) { throw "no NSE scheduled tasks found - run the setup / register scripts first" }

Write-Host "Tasks found:"
foreach ($t in $tasks) {
    Write-Host ("  {0,-34} logon: {1,-12} user: {2}" -f $t.TaskName, $t.Principal.LogonType, $t.Principal.UserId)
}
$user = "$env:USERDOMAIN\$env:USERNAME"
$cred = Get-Credential -UserName $user -Message "Windows password for the NSE scheduled tasks (run while logged off)"
$pw = $cred.GetNetworkCredential().Password

$ok = 0
foreach ($t in $tasks) {
    $s = $t.Settings
    $s.WakeToRun = $true
    $s.StartWhenAvailable = $true
    try {
        Set-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -User $cred.UserName -Password $pw `
            -Settings $s | Out-Null
        $now = Get-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath
        Write-Host ("  OK   {0,-34} logon: {1}" -f $t.TaskName, $now.Principal.LogonType) -ForegroundColor Green
        $ok++
    } catch {
        Write-Host ("  FAIL {0,-34} {1}" -f $t.TaskName, $_.Exception.Message) -ForegroundColor Red
    }
}
$pw = $null
Write-Host "`n$ok of $($tasks.Count) tasks now run whether you are logged on or not."
Write-Host "If a task failed with 'The user name or password is incorrect', run this again with the right password."
Write-Host "Power: Settings > System > Power - set 'When plugged in, put my device to sleep' as you like, but do not"
Write-Host "shut the PC down on trading nights; 'Wake to run' can wake it from sleep, not from shutdown."
```
### `code/NSEUIENGINE/register_eod_tasks.ps1`
```
<#
.SYNOPSIS
  NSEUIENGINE end-of-day scheduled task: the signal x ML ledger + the NSEMLENGINE forecast-accuracy report.

    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEUIENGINE\register_eod_tasks.ps1

  NSEUIENGINE-SignalLog   Mon-Fri 22:00   signal_ml_tracker.py --wait-ml 120
      runs after NSE data update (19:30) -> NSEMLENGINE-Predict (20:00) -> Trader-Evening (20:30);
      waits up to 2 h for the day's NSEMLENGINE forecast, records the next session's signals with it, fills in
      outcomes from the day's prices and rebuilds reports\signal_ml_report.txt. Log: logs\signal_ml_tracker.log
    then ml_accuracy.py (runs even if the ledger step failed): compares every NSEMLENGINE forecast whose session now
      has real prices with what happened -> reports\ml_accuracy_report.txt, ml_accuracy_history.csv,
      ml_accuracy\forecast_vs_actual_<session>.csv. Log: logs\ml_accuracy.log
  NSEUIENGINE-EdgeReport  Saturday 10:00   edge_report.py
      is there an edge after costs? every signal traded on paper by its plan + the live trades vs the backtest,
      net of Fyers charges -> reports\edge_report.txt, reports\edge\paper_trades.csv, edge_history.csv.
      Log: logs\edge_report.log
  Runs with the Python that runs NSEDATAENGINE (pandas/polars): -Python, default "python".
  Remove: Unregister-ScheduledTask NSEUIENGINE-SignalLog -Confirm:$false
#>
param([string]$At = "22:00", [string]$Python = "python", [string]$EdgeAt = "10:00")
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$data = if ($env:NSEUIENGINE_DATA_DIR) { $env:NSEUIENGINE_DATA_DIR } elseif ($env:NSE_DATA_ROOT) { Join-Path $env:NSE_DATA_ROOT "NSEUIENGINE" } else { $root }   # state / reports / logs
New-Item -ItemType Directory -Force -Path (Join-Path $data "logs") | Out-Null
$cmd = "set `"PYTHONUTF8=1`" && `"$Python`" `"$root\signal_ml_tracker.py`" --wait-ml 120 >> `"$data\logs\signal_ml_tracker.log`" 2>&1 & `"$Python`" `"$root\ml_accuracy.py`" >> `"$data\logs\ml_accuracy.log`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $cmd" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "NSEUIENGINE-SignalLog" -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "NSEUIENGINE: signal x NSEMLENGINE forecast ledger + forecast accuracy (end of day)" -Force | Out-Null
$cmd2 = "set `"PYTHONUTF8=1`" && `"$Python`" `"$root\edge_report.py`" >> `"$data\logs\edge_report.log`" 2>&1"
$action2 = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $cmd2" -WorkingDirectory $root
$trigger2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At $EdgeAt
$settings2 = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "NSEUIENGINE-EdgeReport" -Action $action2 -Trigger $trigger2 -Settings $settings2 `
    -Principal $principal -Description "NSEUIENGINE: weekly edge report - paper + live vs backtest, after charges" -Force | Out-Null
Write-Host "Registered NSEUIENGINE-EdgeReport (Saturday $EdgeAt). Run now: Start-ScheduledTask NSEUIENGINE-EdgeReport"
Write-Host "Registered NSEUIENGINE-SignalLog (Mon-Fri $At). Run now: Start-ScheduledTask NSEUIENGINE-SignalLog"
Write-Host "Evening chain: 19:30 NSE data update -> 20:00 NSEMLENGINE-Predict -> 20:30 NSEUIENGINE-Trader-Evening -> 22:00 NSEUIENGINE-SignalLog"
```
### `code/NSEUIENGINE/set_static_ip.ps1`
```
<#
  NSEUIENGINE: set STATICIP_PROXY and STATIC_IP (Windows user environment variables) from state\staticip.csv.
  Tests the staticip.in proxy (http and https) and only saves a setting that really leaves from the static IP.
    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEUIENGINE\set_static_ip.ps1
#>
param([string]$CsvPath = "")
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$data = if ($env:NSEUIENGINE_DATA_DIR) { $env:NSEUIENGINE_DATA_DIR } elseif ($env:NSE_DATA_ROOT) { Join-Path $env:NSE_DATA_ROOT "NSEUIENGINE" } else { $root }   # state / reports / logs
if (-not $CsvPath) { $CsvPath = Join-Path $data "state\staticip.csv" }
$py = Join-Path $root ".venv\Scripts\python.exe"
$out = & $py (Join-Path $root "preflight.py") proxy --csv $CsvPath
$result = Join-Path $data "state\static_ip_result.txt"
if ($LASTEXITCODE -ne 0) {
    "FAILED $(Get-Date -Format s): the proxy did not return the static IP" | Set-Content $result
    Write-Host "FAILED: the proxy did not return the static IP (see above)" -ForegroundColor Red; exit 1 }
$j = ($out | Select-Object -Last 1) | ConvertFrom-Json
[Environment]::SetEnvironmentVariable("STATICIP_PROXY", $j.proxy, "User")
[Environment]::SetEnvironmentVariable("STATIC_IP", $j.ip, "User")
$scheme = ($j.proxy -split "://")[0]
$chk = [Environment]::GetEnvironmentVariable("STATIC_IP", "User")
"SAVED $(Get-Date -Format s): STATIC_IP=$chk scheme=$(($j.proxy -split '://')[0]) proxy_set=$([bool][Environment]::GetEnvironmentVariable('STATICIP_PROXY','User'))" | Set-Content $result
Write-Host "SAVED: STATIC_IP = $($j.ip), STATICIP_PROXY = $scheme proxy via staticip.in (valid until $($j.validity))" -ForegroundColor Green
```
### `code/NSEUIENGINE/setup_fyers_token.ps1`
```
<#
.SYNOPSIS
  NSEUIENGINE: one-time setup of the daily Fyers access-token refresh.

    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEUIENGINE\setup_fyers_token.ps1

  1. creates .venv in this folder and installs requirements.txt
  2. sets the user environment variable NSEUIENGINE_HOME
  3. asks for the Fyers credentials (App ID, secret key, Fyers ID, PIN, TOTP secret); secrets are DPAPI-encrypted
  4. gets the first token now (browser login allowed, so you can approve the app once if Fyers asks)
  5. registers the scheduled task "NSEUIENGINE-FyersToken": daily at -At (default 08:00) and at logon,
     wakes the PC, runs a missed run as soon as possible, no console window (pythonw)

  Options
    -At "08:00"            time of the daily run (PC clock must be IST)
    -Scope User|Machine    User (default): variables for your account, which is what all your scripts run as.
                           Machine: variables for every account on the PC - run this script as Administrator.
    -RunWhenLoggedOff      run even when you are not logged on (asks for your Windows password once).
                           Default runs only while you are logged on.
    -Proxy                 send login calls through %STATICIP_PROXY%
    -SkipCredentials       don't ask for credentials again (re-register the task only)

  Remove:  Unregister-ScheduledTask NSEUIENGINE-FyersToken -Confirm:$false
#>
param(
    [string]$At = "08:00",
    [ValidateSet("User", "Machine")][string]$Scope = "User",
    [switch]$RunWhenLoggedOff,
    [switch]$Proxy,
    [switch]$SkipCredentials,
    [switch]$NoTask,      # credentials only: no first token, no scheduled task (new PC while the old one is still live)
    [string]$TaskName = "NSEUIENGINE-FyersToken"
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$data = if ($env:NSEUIENGINE_DATA_DIR) { $env:NSEUIENGINE_DATA_DIR } elseif ($env:NSE_DATA_ROOT) { Join-Path $env:NSE_DATA_ROOT "NSEUIENGINE" } else { $root }   # state / reports / logs
$scopeArg = $Scope.ToLower()

if ($Scope -eq "Machine") {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) { throw "-Scope Machine needs an elevated PowerShell (Run as Administrator)" }
}

# 1. venv
$venv = Join-Path $root ".venv"
$py = Join-Path $venv "Scripts\python.exe"
$pyw = Join-Path $venv "Scripts\pythonw.exe"
if (-not (Test-Path $py)) {
    Write-Host "Creating virtual environment ..."
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv $venv } else { & python -m venv $venv }
    if (-not (Test-Path $py)) { throw "could not create $venv - is Python 3.10+ installed?" }
}
& $py -m pip install --disable-pip-version-check -q -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# 2. home variable (same convention as NSEDATAENGINE_HOME)
[Environment]::SetEnvironmentVariable("NSEUIENGINE_HOME", $root, "User")
$env:NSEUIENGINE_HOME = $root

$manager = Join-Path $root "fyers_token_manager.py"
$extra = @("--scope", $scopeArg)
if ($Proxy) { $extra += "--proxy" }

# 3. credentials
if (-not $SkipCredentials) {
    & $py $manager --setup
    if ($LASTEXITCODE -ne 0) { throw "credential setup failed" }
}

if ($NoTask) {
    Write-Host "`nCredentials saved. -NoTask: no token fetched and no task registered (run again without -NoTask at the switch-over)."
    return
}
# 4. first token now (interactive: browser login allowed as fallback)
Write-Host "`nGetting today's token ..."
& $py $manager --force --interactive @extra
if ($LASTEXITCODE -ne 0) { Write-Warning "First run failed - see $data\logs\fyers_token.log. The task is registered anyway." }

# 5. scheduled task
$argLine = "`"$manager`" " + ($extra -join " ")
$action = New-ScheduledTaskAction -Execute $pyw -Argument $argLine -WorkingDirectory $root
$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At $At),
    (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME")
)
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
$runLevel = if ($Scope -eq "Machine") { "Highest" } else { "Limited" }
$desc = "NSEUIENGINE: refresh the Fyers API access token into environment variables (FYERS_ACCESS_TOKEN, FYERS_WS_TOKEN)"

if ($RunWhenLoggedOff) {
    # Password logon keeps DPAPI (the encrypted secrets file) usable when nobody is logged on; S4U would not.
    $cred = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" -Message "Windows password for the scheduled task"
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings `
        -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel $runLevel `
        -Description $desc -Force | Out-Null
} else {
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive `
        -RunLevel $runLevel
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings `
        -Principal $principal -Description $desc -Force | Out-Null
}

Write-Host "`nRegistered '$TaskName': daily at $At and at logon ($Scope variables)."
Write-Host "Run it now:   Start-ScheduledTask $TaskName"
Write-Host "Check:        & '$py' '$manager' --status"
Write-Host "Log:          $data\logs\fyers_token.log"
Write-Host "Open a NEW terminal / restart your IDE to see the variables there (or use fyers_token.py, which reads the registry)."
```
### `code/NSEUIENGINE/setup_swing_trader.ps1`
```
<#
.SYNOPSIS
  NSEUIENGINE: register the swing trader's scheduled tasks.

    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEUIENGINE\setup_swing_trader.ps1

  NSEUIENGINE-Trader-Evening   Mon-Fri 20:30   swing_trader.py evening  (NSEDATAENGINE scan + tomorrow's plan)
  NSEUIENGINE-Trader-Morning   Mon-Fri 08:58   swing_trader.py morning  (the trading session until 15:31)
                               and at logon    (resumes the session after a restart; does nothing outside hours)

  Both run only while you are logged on (the Fyers token's secrets are DPAPI-protected), wake the PC, and run a
  missed start as soon as possible. Needs setup_fyers_token.ps1 to have been run first (venv + daily token).

  Trading stays in DRY RUN until you set "live": true in state\trader_config.json.
  "broker": "fyers" (default) or "shoonya" in the config; with shoonya the tasks run NSEALGOENGINE's Python.
  Afterwards run make_tasks_run_logged_off.ps1 again (this script registers the tasks as logged-on only).

  Remove: Unregister-ScheduledTask NSEUIENGINE-Trader-Morning,NSEUIENGINE-Trader-Evening -Confirm:$false
#>
param([string]$Morning = "08:58", [string]$Evening = "20:30")
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$data = if ($env:NSEUIENGINE_DATA_DIR) { $env:NSEUIENGINE_DATA_DIR } elseif ($env:NSE_DATA_ROOT) { Join-Path $env:NSE_DATA_ROOT "NSEUIENGINE" } else { $root }   # state / reports / logs
$py = Join-Path $root ".venv\Scripts\python.exe"
$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"
$trader = Join-Path $root "swing_trader.py"
if (-not (Test-Path $py)) { throw "no .venv in $root - run setup_fyers_token.ps1 first" }

# config (created with live=false on first run) + a first look at the state
& $py $trader status | Out-Null

# "broker": "shoonya" -> the trader needs NSEALGOENGINE's Python (its Shoonya adapter + NorenRestApiPy)
$cfg0 = Get-Content (Join-Path $data "state\trader_config.json") -Raw | ConvertFrom-Json
if ("$($cfg0.broker)".ToLower() -eq "shoonya") {
    $ae = if ($cfg0.nsealgoengine_home) { $cfg0.nsealgoengine_home } elseif ($env:NSEALGOENGINE_HOME) { $env:NSEALGOENGINE_HOME } else { "%NSE_ROOT%\code\NSEALGOENGINE" }
    $py = Join-Path $ae ".venv\Scripts\python.exe"; $pyw = Join-Path $ae ".venv\Scripts\pythonw.exe"
    if (-not (Test-Path $py)) { throw "broker is shoonya but $py is missing - run NSEALGOENGINE scripts\windows\setup.ps1 and setup_shoonya.ps1" }
    & $py -c "import NorenRestApiPy" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "NorenRestApiPy is not installed in $ae\.venv - run setup_shoonya.ps1" }
    Write-Host "broker: shoonya -> tasks run with $pyw"
}

foreach ($v in "STATICIP_PROXY", "STATIC_IP") {
    if (-not [Environment]::GetEnvironmentVariable($v, "User") -and -not [Environment]::GetEnvironmentVariable($v, "Machine")) {
        Write-Warning "$v is not set. With require_static_ip=true (SEBI static-IP rule) the trader refuses to place orders until it is."
    }
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Warning "'python' (the Python that runs NSEDATAENGINE) is not on PATH - set data_python in state\trader_config.json"
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$days = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 8) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2)
$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$trader`" morning" -WorkingDirectory $root
$triggers = @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $Morning),
              (New-ScheduledTaskTrigger -AtLogOn -User $user))
Register-ScheduledTask -TaskName "NSEUIENGINE-Trader-Morning" -Action $action -Trigger $triggers -Settings $settings `
    -Principal $principal -Description "NSEUIENGINE swing trader: trading session (entries, stops, targets, exits)" -Force | Out-Null

$settings2 = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$action2 = New-ScheduledTaskAction -Execute $pyw -Argument "`"$trader`" evening" -WorkingDirectory $root
Register-ScheduledTask -TaskName "NSEUIENGINE-Trader-Evening" -Action $action2 `
    -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $Evening) -Settings $settings2 `
    -Principal $principal -Description "NSEUIENGINE swing trader: NSEDATAENGINE scan + next session plan" -Force | Out-Null

$cfg = Get-Content (Join-Path $data "state\trader_config.json") -Raw | ConvertFrom-Json
Write-Host "`nRegistered NSEUIENGINE-Trader-Morning (Mon-Fri $Morning + at logon) and NSEUIENGINE-Trader-Evening (Mon-Fri $Evening)."
Write-Host ("Mode: " + $(if ($cfg.live) { "LIVE - real orders" } else { "DRY RUN - set `"live`": true in state\trader_config.json to trade" }))
Write-Host "Plan now:     & '$py' '$trader' evening --no-scan"
Write-Host "Status:       & '$py' '$trader' status"
Write-Host "Emergency:    & '$py' '$trader' kill --flatten"
Write-Host "Log:          $data\logs\swing_trader.log"
```
### `code/docs/archive/move_to_shared_data.ps1`
```
<#
.SYNOPSIS
  Moves the data of all four engines into ONE shared data area, one folder per engine (option A):

      C:\NSE\DATA\NSEDATAENGINE   data, data_fresh, data_fresh_2019, data_ref, backtest_results, portfolio_results, signals
      C:\NSE\DATA\NSEMLENGINE     (was NSEMLENGINE\work: dataset, models, predictions, results, logs)
      C:\NSE\DATA\NSEALGOENGINE   data, runs, .cache, recordings (created when nsealgo first runs)
      C:\NSE\DATA\NSEUIENGINE     state (incl. the encrypted secrets and the trader's state), reports, logs
      C:\NSE\DATA\_archive        NSEDATAENGINE\backup_pre_contract_20261003 + the old data files in %NSE_ROOT%
      C:\NSE\DATA\_migration      journal of every move (for -Rollback)

  Code stays where it is. Every engine finds its folder through environment variables (set here):
  NSE_DATA_ROOT, NSEDATAENGINE_*_DIR, NSEMLENGINE_WORK_DIR, NSEALGOENGINE_DATA_DIR, NSEUIENGINE_DATA_DIR.

.USAGE
  powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEDATAENGINE\migrate\move_to_shared_data.ps1            # plan only
  powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEDATAENGINE\migrate\move_to_shared_data.ps1 -Execute   # move
  powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEDATAENGINE\migrate\move_to_shared_data.ps1 -Rollback  # undo

  Folders are MOVED (renamed - instant, no extra space, when C:\NSE is on the same drive as %NSE_ROOT%), each one
  checked (file count + bytes) and journalled. Refuses to start while an NSE scheduled task or an engine's
  Python process is running. Run it after 22:30 on a weekday or at the weekend; close Excel / LibreOffice / IDEs
  that have files of these folders open.
#>
param(
    [string]$Root = "C:\NSE\DATA",
    [string]$Base = "%NSE_ROOT%",
    [switch]$Execute,
    [switch]$Rollback
)
$ErrorActionPreference = "Stop"
$JDir = Join-Path $Root "_migration"

function Get-Stats([string]$p) {
    if (-not (Test-Path -LiteralPath $p)) { return $null }
    $it = Get-Item -LiteralPath $p -Force
    if (-not $it.PSIsContainer) { return [pscustomobject]@{ Files = 1; Bytes = [int64]$it.Length } }
    $f = @(Get-ChildItem -LiteralPath $p -Recurse -Force -File -ErrorAction SilentlyContinue)
    $b = ($f | Measure-Object -Property Length -Sum).Sum
    return [pscustomobject]@{ Files = $f.Count; Bytes = [int64]$(if ($b) { $b } else { 0 }) }
}
function Fmt([int64]$b) { if ($b -ge 1GB) { "{0:N1} GB" -f ($b / 1GB) } elseif ($b -ge 1MB) { "{0:N0} MB" -f ($b / 1MB) } else { "{0:N0} KB" -f ($b / 1KB) } }

# ---------------------------------------------------------------------------------------------- rollback
if ($Rollback) {
    $jfile = Get-ChildItem (Join-Path $JDir "migration_*.json") -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1
    if (-not $jfile) { throw "no migration journal in $JDir" }
    $J = Get-Content $jfile.FullName -Raw | ConvertFrom-Json
    Write-Host "Rolling back $($jfile.Name)"
    $done = @($J.moves | Where-Object { $_.done })
    [array]::Reverse($done)
    foreach ($m in $done) {
        if (Test-Path -LiteralPath $m.src) { Write-Warning "skip (source exists again): $($m.src)"; continue }
        $parent = Split-Path -Parent $m.src
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        Move-Item -LiteralPath $m.dst -Destination $m.src
        Write-Host "  back: $($m.dst) -> $($m.src)"
    }
    foreach ($p in $J.env.PSObject.Properties) {
        $old = $p.Value.old
        [Environment]::SetEnvironmentVariable($p.Name, $(if ($old) { $old } else { $null }), "User")
        Write-Host ("  env {0} = {1}" -f $p.Name, $(if ($old) { $old } else { "<removed>" }))
    }
    $reg = Join-Path $Base "NSEUIENGINE\register_eod_tasks.ps1"
    if (Test-Path $reg) { Remove-Item Env:NSE_DATA_ROOT, Env:NSEUIENGINE_DATA_DIR -ErrorAction SilentlyContinue; & $reg | Out-Null }
    Write-Host "Rollback finished. Open a new terminal before running anything." -ForegroundColor Green
    exit 0
}

# ---------------------------------------------------------------------------------------------- plan
$moves = New-Object System.Collections.ArrayList
function Add-Move($eng, $src, $dst) { [void]$moves.Add([pscustomobject]@{ engine = $eng; src = $src; dst = $dst; files = 0; bytes = 0; done = $false }) }
foreach ($d in "data", "data_fresh", "data_fresh_2019", "data_ref", "backtest_results", "portfolio_results", "signals") {
    Add-Move "NSEDATAENGINE" "$Base\NSEDATAENGINE\$d" "$Root\NSEDATAENGINE\$d"
}
Add-Move "NSEMLENGINE" "$Base\NSEMLENGINE\work" "$Root\NSEMLENGINE"
foreach ($d in "data", "runs", ".cache", "recordings") { Add-Move "NSEALGOENGINE" "$Base\NSEALGOENGINE\$d" "$Root\NSEALGOENGINE\$d" }
foreach ($d in "state", "reports", "logs") { Add-Move "NSEUIENGINE" "$Base\NSEUIENGINE\$d" "$Root\NSEUIENGINE\$d" }
Add-Move "_archive" "$Base\NSEDATAENGINE\backup_pre_contract_20261003" "$Root\_archive\NSEDATAENGINE\backup_pre_contract_20261003"
Get-ChildItem -LiteralPath $Base -File -Force | Where-Object { $_.Extension -in ".parquet", ".zip" -or $_.Name -eq "data_quality_report.csv" } |
    ForEach-Object { Add-Move "_archive" $_.FullName "$Root\_archive\temp_root\$($_.Name)" }

$envs = [ordered]@{
    "NSE_DATA_ROOT"               = $Root
    "NSEDATAENGINE_DATA_DIR"      = "$Root\NSEDATAENGINE\data"
    "NSEDATAENGINE_EVENTS_DIR"    = "$Root\NSEDATAENGINE\data\events"
    "NSEDATAENGINE_FRESH_DIR"     = "$Root\NSEDATAENGINE\data_fresh"
    "NSEDATAENGINE_FRESH2019_DIR" = "$Root\NSEDATAENGINE\data_fresh_2019"
    "NSEDATAENGINE_REF_DIR"       = "$Root\NSEDATAENGINE\data_ref"
    "NSEDATAENGINE_BACKTEST_DIR"  = "$Root\NSEDATAENGINE\backtest_results"
    "NSEDATAENGINE_PORTFOLIO_DIR" = "$Root\NSEDATAENGINE\portfolio_results"
    "NSEDATAENGINE_SIGNALS_DIR"   = "$Root\NSEDATAENGINE\signals"
    "NSEMLENGINE_WORK_DIR"        = "$Root\NSEMLENGINE"
    "NSEALGOENGINE_DATA_DIR"      = "$Root\NSEALGOENGINE"
    "NSEUIENGINE_DATA_DIR"        = "$Root\NSEUIENGINE"
}

Write-Host "`nShared data area: $Root   (code stays in $Base)`n"
$total = [int64]0
$problems = @()
foreach ($m in $moves) {
    $s = Get-Stats $m.src
    if (-not $s) { $m.files = -1; continue }
    $m.files = $s.Files; $m.bytes = $s.Bytes; $total += $s.Bytes
    if (Test-Path -LiteralPath $m.dst) {
        $ds = Get-Stats $m.dst
        if ($ds.Files -gt 0) { $problems += "destination already has files: $($m.dst)" }
    }
}
Write-Host ("{0,-14} {1,9} {2,9}  {3}" -f "Engine", "Files", "Size", "From  ->  To")
foreach ($m in ($moves | Where-Object { $_.files -ge 0 })) {
    Write-Host ("{0,-14} {1,9} {2,9}  {3}  ->  {4}" -f $m.engine, $m.files, (Fmt $m.bytes), $m.src, $m.dst)
}
$skipped = @($moves | Where-Object { $_.files -lt 0 } | ForEach-Object { $_.src })
if ($skipped) { Write-Host ("Not present (nothing to move): " + ($skipped -join ", ")) }
Write-Host ("Total: {0}" -f (Fmt $total))
Write-Host "`nUser environment variables:"
foreach ($k in $envs.Keys) {
    $old = [Environment]::GetEnvironmentVariable($k, "User")
    Write-Host ("  {0,-28} {1}  ->  {2}" -f $k, $(if ($old) { $old } else { "<not set>" }), $envs[$k])
}

# safety checks
$sameVol = ([IO.Path]::GetPathRoot($Root)).ToUpper() -eq ([IO.Path]::GetPathRoot($Base)).ToUpper()
if (-not $sameVol) { Write-Warning "$Root is on another drive: folders will be COPIED then deleted (slow, needs $(Fmt $total) free)" }
$running = @()
if (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue) {
    $running = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match "NSE|nsealgo" -and $_.State -eq "Running" })
}
if ($running) { $problems += "scheduled task(s) running: " + (($running | ForEach-Object TaskName) -join ", ") }
$procs = @()
if (Get-Command Get-CimInstance -ErrorAction SilentlyContinue) {
    $procs = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "NSE(DATA|ML|UI|ALGO)ENGINE|nseml|nsealgo" })
}
if ($procs) { $problems += "engine Python process(es) running: " + (($procs | ForEach-Object { "$($_.ProcessId)" }) -join ", ") }
$locks = @($moves | Where-Object { $_.files -gt 0 -and (Get-Item -LiteralPath $_.src).PSIsContainer } |
    ForEach-Object { Get-ChildItem -LiteralPath $_.src -Recurse -Force -Filter ".~lock*" -ErrorAction SilentlyContinue })
if ($locks) { Write-Warning ("LibreOffice lock files found (a file may be open): " + (($locks | ForEach-Object FullName) -join ", ")) }

if ($problems) {
    Write-Host "`nCannot move now:" -ForegroundColor Red
    $problems | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    if ($Execute) { exit 1 }
}
if (-not $Execute) { Write-Host "`nPlan only. Run again with -Execute to move." -ForegroundColor Yellow; exit 0 }

# ---------------------------------------------------------------------------------------------- execute
New-Item -ItemType Directory -Force -Path $JDir | Out-Null
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$jpath = Join-Path $JDir "migration_$ts.json"
$envJ = [ordered]@{}
foreach ($k in $envs.Keys) { $envJ[$k] = @{ old = [Environment]::GetEnvironmentVariable($k, "User"); new = $envs[$k] } }
function Save-Journal { @{ when = $ts; root = $Root; base = $Base; moves = $moves; env = $envJ } | ConvertTo-Json -Depth 5 | Set-Content $jpath -Encoding UTF8 }
Save-Journal

foreach ($m in $moves) {
    if ($m.files -lt 0) { continue }
    $parent = Split-Path -Parent $m.dst
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    if ((Test-Path -LiteralPath $m.dst) -and (Get-Item -LiteralPath $m.dst).PSIsContainer) { Remove-Item -LiteralPath $m.dst -Force }  # empty
    try {
        Move-Item -LiteralPath $m.src -Destination $m.dst
    } catch {
        Save-Journal
        Write-Host "`nFAILED moving $($m.src): $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "Nothing else was moved after this. Fix the cause (open file?) and run -Execute again, or -Rollback." -ForegroundColor Red
        exit 1
    }
    $s = Get-Stats $m.dst
    if ($s.Files -ne $m.files -or $s.Bytes -ne $m.bytes) {
        Save-Journal
        throw "verification failed for $($m.dst): $($s.Files) files / $($s.Bytes) bytes, expected $($m.files) / $($m.bytes)"
    }
    $m.done = $true
    Save-Journal
    Write-Host ("  moved  {0,-60} {1,8} files  {2,9}" -f $m.dst, $m.files, (Fmt $m.bytes))
}
foreach ($d in "NSEDATAENGINE", "NSEMLENGINE", "NSEALGOENGINE", "NSEUIENGINE") {
    New-Item -ItemType Directory -Force -Path (Join-Path $Root $d) | Out-Null
}
foreach ($k in $envs.Keys) {
    [Environment]::SetEnvironmentVariable($k, $envs[$k], "User")
    Set-Item -Path "Env:$k" -Value $envs[$k]
}
Write-Host "`nEnvironment variables set." -ForegroundColor Green

# the one task whose command line contains a data path
$reg = Join-Path $Base "NSEUIENGINE\register_eod_tasks.ps1"
if (Test-Path $reg) { & $reg | Out-Null; Write-Host "Re-registered NSEUIENGINE-SignalLog (log now in $Root\NSEUIENGINE\logs)" }

# checks
Write-Host "`n== NSEDATAENGINE: python nse_contract.py paths"
Push-Location "$Base\NSEDATAENGINE"; & python nse_contract.py paths; Pop-Location
Write-Host "`n== NSEMLENGINE work folder"
$mlpy = if ($env:NSEMLENGINE_PYTHON) { $env:NSEMLENGINE_PYTHON } else { "python" }
& $mlpy -c "import sys; sys.path.insert(0, r'$Base\NSEMLENGINE'); import nseml.config as c; print(c.work_dir())"
$uipy = "$Base\NSEUIENGINE\.venv\Scripts\python.exe"
if (Test-Path $uipy) {
    Write-Host "`n== NSEUIENGINE"
    & $uipy -c "import sys; sys.path.insert(0, r'$Base\NSEUIENGINE'); import swing_trader as T; print('state  ', T.STATE_DIR); print('journal', T.JOURNAL_FILE); print('signals', T.signals_dir(T.load_config()))"
    & $uipy "$Base\NSEUIENGINE\fyers_token_manager.py" --status
}
Write-Host "`nDone. Journal: $jpath" -ForegroundColor Green
Write-Host "Open a NEW terminal / restart IDEs so they see the new variables. Undo: ...\move_to_shared_data.ps1 -Rollback"
```
### `code/docs/archive/run_validation.bat`
```
@echo off
REM Windows batch file to run the NSE trading system validation with Kaggle data
REM Converts Kaggle format to pipeline format, then runs validation

echo.
echo ===============================================================
echo  NSE STOCK SIGNAL SYSTEM - PARQUET VALIDATION
echo  (Kaggle Dataset Edition)
echo ===============================================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.8+ and add it to your PATH
    pause
    exit /b 1
)

REM Install required dependencies
echo Installing dependencies (pyarrow, scikit-learn, scipy)...
pip install -q pyarrow scikit-learn scipy numpy pandas

echo.
echo ===============================================================
echo STEP 1: Converting Kaggle format to pipeline format...
echo ===============================================================
python prepare_kaggle_data.py final_cleaned.parquet kaggle_prepared.parquet

if errorlevel 1 (
    echo ERROR: Data conversion failed
    pause
    exit /b 1
)

echo.
echo ===============================================================
echo STEP 2: Computing features and generating signals...
echo ===============================================================
python run_pipeline_parquet.py --input kaggle_prepared.parquet --validate --fit --top 50

echo.
echo ===============================================================
echo STEP 3: Detailed theory validation...
echo ===============================================================
echo Check data/signals/panel_features.parquet for the full panel
python theory_validation.py data/signals/panel_features.parquet

echo.
echo ===============================================================
echo VALIDATION COMPLETE
echo ===============================================================
echo.
echo Results saved to:
echo   - data/signals/long_term_latest.csv (today's ranked signals)
echo   - data/signals/panel_features.parquet (full panel with features)
echo   - kaggle_prepared.parquet (converted data for reuse)
echo.
pause
```
### `code/scripts/validation/run_validation.bat`
```
@echo off
REM Windows batch file to run the NSE trading system validation with Kaggle data
REM Converts Kaggle format to pipeline format, then runs validation

echo.
echo ===============================================================
echo  NSE STOCK SIGNAL SYSTEM - PARQUET VALIDATION
echo  (Kaggle Dataset Edition)
echo ===============================================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.8+ and add it to your PATH
    pause
    exit /b 1
)

REM Install required dependencies
echo Installing dependencies (pyarrow, scikit-learn, scipy)...
pip install -q pyarrow scikit-learn scipy numpy pandas

echo.
echo ===============================================================
echo STEP 1: Converting Kaggle format to pipeline format...
echo ===============================================================
python prepare_kaggle_data.py final_cleaned.parquet kaggle_prepared.parquet

if errorlevel 1 (
    echo ERROR: Data conversion failed
    pause
    exit /b 1
)

echo.
echo ===============================================================
echo STEP 2: Computing features and generating signals...
echo ===============================================================
python run_pipeline_parquet.py --input kaggle_prepared.parquet --validate --fit --top 50

echo.
echo ===============================================================
echo STEP 3: Detailed theory validation...
echo ===============================================================
echo Check data/signals/panel_features.parquet for the full panel
python theory_validation.py data/signals/panel_features.parquet

echo.
echo ===============================================================
echo VALIDATION COMPLETE
echo ===============================================================
echo.
echo Results saved to:
echo   - data/signals/long_term_latest.csv (today's ranked signals)
echo   - data/signals/panel_features.parquet (full panel with features)
echo   - kaggle_prepared.parquet (converted data for reuse)
echo.
pause
```