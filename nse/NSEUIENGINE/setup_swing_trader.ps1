<#
.SYNOPSIS
  NSEUIENGINE: register the swing trader's scheduled tasks.

    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEUIENGINE\setup_swing_trader.ps1

  NSEUIENGINE-Trader-Evening   Mon-Fri 20:30   swing_trader.py evening  (NSEDATAENGINE scan + tomorrow's plan)
  NSEUIENGINE-Trader-Morning   Mon-Fri 08:58   swing_trader.py morning  (the trading session until 15:31)
                               and at logon    (resumes the session after a restart; does nothing outside hours)

  Both run only while you are logged on (the Fyers token's secrets are DPAPI-protected), wake the PC, and run a
  missed start as soon as possible. Needs setup_fyers_token.ps1 to have been run first (venv + daily token).

  Trading stays in DRY RUN until you set "live": true in state\trader_config.json.
  "broker": "fyers" (default) or "shoonya" in the config; with shoonya the tasks run NSEALGOENGINE's Python.
  Afterwards run make_tasks_run_logged_off.ps1 again (this script registers the tasks as logged-on only).

  Remove: Unregister-ScheduledTask NSEUIENGINE-Trader-Morning,NSEUIENGINE-Trader-Evening -Confirm:$false
#>
param([string]$Morning = "08:58", [string]$Evening = "20:30")
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$data = if ($env:NSEUIENGINE_DATA_DIR) { $env:NSEUIENGINE_DATA_DIR } elseif ($env:NSE_DATA_ROOT) { Join-Path $env:NSE_DATA_ROOT "NSEUIENGINE" } elseif ($env:NSE_ROOT) { Join-Path $env:NSE_ROOT "data\NSEUIENGINE" } else { (Resolve-Path (Join-Path $root "..\..\data\NSEUIENGINE")).Path } elseif ($env:NSE_ROOT) { Join-Path $env:NSE_ROOT "data\NSEUIENGINE" } else { (Resolve-Path (Join-Path $root "..\..\data\NSEUIENGINE")).Path }   # state / reports / logs
$py = Join-Path $root ".venv\Scripts\python.exe"
$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"
$trader = Join-Path $root "swing_trader.py"
if (-not (Test-Path $py)) { throw "no .venv in $root - run setup_fyers_token.ps1 first" }

# config (created with live=false on first run) + a first look at the state
& $py $trader status | Out-Null

# "broker": "shoonya" -> the trader needs NSEALGOENGINE's Python (its Shoonya adapter + NorenRestApiPy)
$cfg0 = Get-Content (Join-Path $data "state\trader_config.json") -Raw | ConvertFrom-Json
if ("$($cfg0.broker)".ToLower() -eq "shoonya") {
    $ae = if ($cfg0.nsealgoengine_home) { $cfg0.nsealgoengine_home } elseif ($env:NSEALGOENGINE_HOME) { $env:NSEALGOENGINE_HOME } else { "%NSE_ROOT%\code\NSEALGOENGINE" }
    $py = Join-Path $ae ".venv\Scripts\python.exe"; $pyw = Join-Path $ae ".venv\Scripts\pythonw.exe"
    if (-not (Test-Path $py)) { throw "broker is shoonya but $py is missing - run NSEALGOENGINE scripts\windows\setup.ps1 and setup_shoonya.ps1" }
    & $py -c "import NorenRestApiPy" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "NorenRestApiPy is not installed in $ae\.venv - run setup_shoonya.ps1" }
    Write-Host "broker: shoonya -> tasks run with $pyw"
}

foreach ($v in "STATICIP_PROXY", "STATIC_IP") {
    if (-not [Environment]::GetEnvironmentVariable($v, "User") -and -not [Environment]::GetEnvironmentVariable($v, "Machine")) {
        Write-Warning "$v is not set. With require_static_ip=true (SEBI static-IP rule) the trader refuses to place orders until it is."
    }
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Warning "'python' (the Python that runs NSEDATAENGINE) is not on PATH - set data_python in state\trader_config.json"
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$days = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 8) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2)
$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$trader`" morning" -WorkingDirectory $root
$triggers = @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $Morning),
              (New-ScheduledTaskTrigger -AtLogOn -User $user))
Register-ScheduledTask -TaskName "NSEUIENGINE-Trader-Morning" -Action $action -Trigger $triggers -Settings $settings `
    -Principal $principal -Description "NSEUIENGINE swing trader: trading session (entries, stops, targets, exits)" -Force | Out-Null

$settings2 = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$action2 = New-ScheduledTaskAction -Execute $pyw -Argument "`"$trader`" evening" -WorkingDirectory $root
Register-ScheduledTask -TaskName "NSEUIENGINE-Trader-Evening" -Action $action2 `
    -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $Evening) -Settings $settings2 `
    -Principal $principal -Description "NSEUIENGINE swing trader: NSEDATAENGINE scan + next session plan" -Force | Out-Null

$cfg = Get-Content (Join-Path $data "state\trader_config.json") -Raw | ConvertFrom-Json
Write-Host "`nRegistered NSEUIENGINE-Trader-Morning (Mon-Fri $Morning + at logon) and NSEUIENGINE-Trader-Evening (Mon-Fri $Evening)."
Write-Host ("Mode: " + $(if ($cfg.live) { "LIVE - real orders" } else { "DRY RUN - set `"live`": true in state\trader_config.json to trade" }))
Write-Host "Plan now:     & '$py' '$trader' evening --no-scan"
Write-Host "Status:       & '$py' '$trader' status"
Write-Host "Emergency:    & '$py' '$trader' kill --flatten"
Write-Host "Log:          $data\logs\swing_trader.log"
