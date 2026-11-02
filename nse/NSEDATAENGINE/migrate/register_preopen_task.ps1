<#
.SYNOPSIS
  Scheduled task "NSEDATAENGINE-PreOpen": records NSE's pre-open auction every trading morning (nse_preopen.py).

    powershell -ExecutionPolicy Bypass -File C:\temp\NSEDATAENGINE\migrate\register_preopen_task.ps1

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
