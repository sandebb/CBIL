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
$data = if ($env:NSEUIENGINE_DATA_DIR) { $env:NSEUIENGINE_DATA_DIR } elseif ($env:NSE_DATA_ROOT) { Join-Path $env:NSE_DATA_ROOT "NSEUIENGINE" } elseif ($env:NSE_ROOT) { Join-Path $env:NSE_ROOT "data\NSEUIENGINE" } else { (Resolve-Path (Join-Path $root "..\..\data\NSEUIENGINE")).Path } elseif ($env:NSE_ROOT) { Join-Path $env:NSE_ROOT "data\NSEUIENGINE" } else { (Resolve-Path (Join-Path $root "..\..\data\NSEUIENGINE")).Path }   # state / reports / logs
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
