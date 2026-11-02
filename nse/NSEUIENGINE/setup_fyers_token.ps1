<#
.SYNOPSIS
  NSEUIENGINE: one-time setup of the daily Fyers access-token refresh.

    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEUIENGINE\setup_fyers_token.ps1

  1. creates .venv in this folder and installs requirements.txt
  2. sets the user environment variable NSEUIENGINE_HOME
  3. asks for the Fyers credentials (App ID, secret key, Fyers ID, PIN, TOTP secret); secrets are DPAPI-encrypted
  4. gets the first token now (browser login allowed, so you can approve the app once if Fyers asks)
  5. registers the scheduled task "NSEUIENGINE-FyersToken": daily at -At (default 08:00) and at logon,
     wakes the PC, runs a missed run as soon as possible, no console window (pythonw)

  Options
    -At "08:00"            time of the daily run (PC clock must be IST)
    -Scope User|Machine    User (default): variables for your account, which is what all your scripts run as.
                           Machine: variables for every account on the PC - run this script as Administrator.
    -RunWhenLoggedOff      run even when you are not logged on (asks for your Windows password once).
                           Default runs only while you are logged on.
    -Proxy                 send login calls through %STATICIP_PROXY%
    -SkipCredentials       don't ask for credentials again (re-register the task only)

  Remove:  Unregister-ScheduledTask NSEUIENGINE-FyersToken -Confirm:$false
#>
param(
    [string]$At = "08:00",
    [ValidateSet("User", "Machine")][string]$Scope = "User",
    [switch]$RunWhenLoggedOff,
    [switch]$Proxy,
    [switch]$SkipCredentials,
    [switch]$NoTask,      # credentials only: no first token, no scheduled task (new PC while the old one is still live)
    [string]$TaskName = "NSEUIENGINE-FyersToken"
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$data = if ($env:NSEUIENGINE_DATA_DIR) { $env:NSEUIENGINE_DATA_DIR } elseif ($env:NSE_DATA_ROOT) { Join-Path $env:NSE_DATA_ROOT "NSEUIENGINE" } else { $root }   # state / reports / logs
$scopeArg = $Scope.ToLower()

if ($Scope -eq "Machine") {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) { throw "-Scope Machine needs an elevated PowerShell (Run as Administrator)" }
}

# 1. venv
$venv = Join-Path $root ".venv"
$py = Join-Path $venv "Scripts\python.exe"
$pyw = Join-Path $venv "Scripts\pythonw.exe"
if (-not (Test-Path $py)) {
    Write-Host "Creating virtual environment ..."
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv $venv } else { & python -m venv $venv }
    if (-not (Test-Path $py)) { throw "could not create $venv - is Python 3.10+ installed?" }
}
& $py -m pip install --disable-pip-version-check -q -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# 2. home variable (same convention as NSEDATAENGINE_HOME)
[Environment]::SetEnvironmentVariable("NSEUIENGINE_HOME", $root, "User")
$env:NSEUIENGINE_HOME = $root

$manager = Join-Path $root "fyers_token_manager.py"
$extra = @("--scope", $scopeArg)
if ($Proxy) { $extra += "--proxy" }

# 3. credentials
if (-not $SkipCredentials) {
    & $py $manager --setup
    if ($LASTEXITCODE -ne 0) { throw "credential setup failed" }
}

if ($NoTask) {
    Write-Host "`nCredentials saved. -NoTask: no token fetched and no task registered (run again without -NoTask at the switch-over)."
    return
}
# 4. first token now (interactive: browser login allowed as fallback)
Write-Host "`nGetting today's token ..."
& $py $manager --force --interactive @extra
if ($LASTEXITCODE -ne 0) { Write-Warning "First run failed - see $data\logs\fyers_token.log. The task is registered anyway." }

# 5. scheduled task
$argLine = "`"$manager`" " + ($extra -join " ")
$action = New-ScheduledTaskAction -Execute $pyw -Argument $argLine -WorkingDirectory $root
$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At $At),
    (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME")
)
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
$runLevel = if ($Scope -eq "Machine") { "Highest" } else { "Limited" }
$desc = "NSEUIENGINE: refresh the Fyers API access token into environment variables (FYERS_ACCESS_TOKEN, FYERS_WS_TOKEN)"

if ($RunWhenLoggedOff) {
    # Password logon keeps DPAPI (the encrypted secrets file) usable when nobody is logged on; S4U would not.
    $cred = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" -Message "Windows password for the scheduled task"
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings `
        -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel $runLevel `
        -Description $desc -Force | Out-Null
} else {
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive `
        -RunLevel $runLevel
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings `
        -Principal $principal -Description $desc -Force | Out-Null
}

Write-Host "`nRegistered '$TaskName': daily at $At and at logon ($Scope variables)."
Write-Host "Run it now:   Start-ScheduledTask $TaskName"
Write-Host "Check:        & '$py' '$manager' --status"
Write-Host "Log:          $data\logs\fyers_token.log"
Write-Host "Open a NEW terminal / restart your IDE to see the variables there (or use fyers_token.py, which reads the registry)."
