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
