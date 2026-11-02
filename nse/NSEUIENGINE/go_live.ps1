<#
.SYNOPSIS
  NSEUIENGINE: one-shot setup + GO / NO-GO check for live trading.

    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEUIENGINE\go_live.ps1 -CsvPath "<staticip.in csv>"
    ... -GoLive      also switch the trader to LIVE if every check passes

  1. finds how the staticip.in proxy works (http / https) and checks traffic really leaves from your static IP
  2. saves STATICIP_PROXY and STATIC_IP as user environment variables
  3. registers the trader's scheduled tasks (setup_swing_trader.ps1)
  4. runs the evening scan for the next session (NSEDATAENGINE update + signals, ~3-5 minutes)
  5. runs preflight.py check: login, DDPI, static IP route, signals, plan, funds, tasks
#>
param([string]$CsvPath = "", [switch]$GoLive)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$data = if ($env:NSEUIENGINE_DATA_DIR) { $env:NSEUIENGINE_DATA_DIR } elseif ($env:NSE_DATA_ROOT) { Join-Path $env:NSE_DATA_ROOT "NSEUIENGINE" } elseif ($env:NSE_ROOT) { Join-Path $env:NSE_ROOT "data\NSEUIENGINE" } else { (Resolve-Path (Join-Path $root "..\..\data\NSEUIENGINE")).Path } elseif ($env:NSE_ROOT) { Join-Path $env:NSE_ROOT "data\NSEUIENGINE" } else { (Resolve-Path (Join-Path $root "..\..\data\NSEUIENGINE")).Path }   # state / reports / logs
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "no .venv in $root - run setup_fyers_token.ps1 first" }

if (-not $CsvPath) {
    $f = Get-ChildItem "$env:USERPROFILE\Downloads\*ip_details*.csv" -ErrorAction SilentlyContinue |
         Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($f) { $CsvPath = $f.FullName } else { throw "pass -CsvPath <the staticip.in csv>" }
}
Write-Host "== 1. static IP proxy ($CsvPath)"
$out = & $py (Join-Path $root "preflight.py") proxy --csv $CsvPath
if ($LASTEXITCODE -ne 0) { throw "the staticip.in proxy did not work - see the messages above" }
$j = ($out | Select-Object -Last 1) | ConvertFrom-Json

Write-Host "== 2. environment variables"
[Environment]::SetEnvironmentVariable("STATICIP_PROXY", $j.proxy, "User"); $env:STATICIP_PROXY = $j.proxy
[Environment]::SetEnvironmentVariable("STATIC_IP", $j.ip, "User"); $env:STATIC_IP = $j.ip
Write-Host "   STATIC_IP = $($j.ip)   (subscription valid until $($j.validity) - renew before then)"

Write-Host "== 3. scheduled tasks"
& (Join-Path $root "setup_swing_trader.ps1")

Write-Host "== 4. evening scan for the next session (takes a few minutes)"
& $py (Join-Path $root "swing_trader.py") evening

Write-Host "== 5. pre-flight check"
& $py (Join-Path $root "preflight.py") check
$go = ($LASTEXITCODE -eq 0)

if ($GoLive) {
    if ($go) {
        $cfgPath = Join-Path $data "state\trader_config.json"
        $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
        $cfg.live = $true
        $cfg | ConvertTo-Json -Depth 5 | Set-Content $cfgPath -Encoding UTF8
        Write-Host "`nLIVE: the 08:58 task will place real orders." -ForegroundColor Green
    } else {
        Write-Host "`nNot switched to live - fix the FAIL items and run again." -ForegroundColor Yellow
    }
}
