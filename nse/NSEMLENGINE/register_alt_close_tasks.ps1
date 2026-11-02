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
