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
