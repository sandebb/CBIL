<#
.SYNOPSIS
  Run the forward test unattended with Windows Task Scheduler (replaces deploy/*.service and crontab on Windows).
    powershell -ExecutionPolicy Bypass -File deploy\windows\register-tasks.ps1 -DataBroker "shoonya,angelone,fyers"
  Creates task "nsealgo-forward": starts at -Start (default 08:30, local time) Monday-Friday, WAKES THE PC from sleep,
  runs `nsealgo forwardtest --daemon` (it waits for pre-market, logs in, trades the session, sleeps until the next
  one), restarts on failure, and appends output to logs\forward.log. Remove: Unregister-ScheduledTask nsealgo-forward
  Requirements: the PC clock set to IST (or shift -Start accordingly); "Allow wake timers" enabled in the power
  plan; broker credentials incl. TOTP secrets in .env so logins need no interaction.
#>
param([string]$DataBroker = "", [string]$Start = "08:30", [string]$TaskName = "nsealgo-forward")
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
New-Item -ItemType Directory -Force -Path (Join-Path $root "logs") | Out-Null
$cliArgs = "forwardtest --daemon" + $(if ($DataBroker) { " --data-broker $DataBroker" } else { "" })
$cmd = "set `"PYTHONUTF8=1`" && `"$root\.venv\Scripts\nsealgo.exe`" $cliArgs >> `"$root\logs\forward.log`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $cmd" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $Start
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "nsealgo forward test (paper trading on live data)" -Force | Out-Null
Write-Host "Registered '$TaskName' (weekdays $Start, wakes the PC). Test now: Start-ScheduledTask $TaskName"
