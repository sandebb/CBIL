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
