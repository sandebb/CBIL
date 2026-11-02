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
