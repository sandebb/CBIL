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
