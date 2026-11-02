<#
.SYNOPSIS
  Daily routine before 09:00 IST (Windows equivalent of scripts/morning.sh): log in (SEBI daily 2FA),
  verify the adapter, then start the forward test.
    powershell -ExecutionPolicy Bypass -File scripts\windows\morning.ps1 -Broker zerodha
#>
param([string]$Broker = "zerodha")
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$nsealgo = ".\.venv\Scripts\nsealgo.exe"
& $nsealgo login $Broker
if ($LASTEXITCODE -ne 0) { throw "login failed" }
& $nsealgo smoke-test $Broker --symbol SBIN
if ($LASTEXITCODE -ne 0) { throw "smoke test failed" }
& $nsealgo forwardtest --data-broker $Broker
