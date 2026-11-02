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
