<#
  NSEUIENGINE: set STATICIP_PROXY and STATIC_IP (Windows user environment variables) from state\staticip.csv.
  Tests the staticip.in proxy (http and https) and only saves a setting that really leaves from the static IP.
    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEUIENGINE\set_static_ip.ps1
#>
param([string]$CsvPath = "")
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$data = if ($env:NSEUIENGINE_DATA_DIR) { $env:NSEUIENGINE_DATA_DIR } elseif ($env:NSE_DATA_ROOT) { Join-Path $env:NSE_DATA_ROOT "NSEUIENGINE" } else { $root }   # state / reports / logs
if (-not $CsvPath) { $CsvPath = Join-Path $data "state\staticip.csv" }
$py = Join-Path $root ".venv\Scripts\python.exe"
$out = & $py (Join-Path $root "preflight.py") proxy --csv $CsvPath
$result = Join-Path $data "state\static_ip_result.txt"
if ($LASTEXITCODE -ne 0) {
    "FAILED $(Get-Date -Format s): the proxy did not return the static IP" | Set-Content $result
    Write-Host "FAILED: the proxy did not return the static IP (see above)" -ForegroundColor Red; exit 1 }
$j = ($out | Select-Object -Last 1) | ConvertFrom-Json
[Environment]::SetEnvironmentVariable("STATICIP_PROXY", $j.proxy, "User")
[Environment]::SetEnvironmentVariable("STATIC_IP", $j.ip, "User")
$scheme = ($j.proxy -split "://")[0]
$chk = [Environment]::GetEnvironmentVariable("STATIC_IP", "User")
"SAVED $(Get-Date -Format s): STATIC_IP=$chk scheme=$(($j.proxy -split '://')[0]) proxy_set=$([bool][Environment]::GetEnvironmentVariable('STATICIP_PROXY','User'))" | Set-Content $result
Write-Host "SAVED: STATIC_IP = $($j.ip), STATICIP_PROXY = $scheme proxy via staticip.in (valid until $($j.validity))" -ForegroundColor Green
