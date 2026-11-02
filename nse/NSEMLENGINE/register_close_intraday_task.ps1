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
