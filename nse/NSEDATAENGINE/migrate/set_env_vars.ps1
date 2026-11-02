<#
.SYNOPSIS
    Sets the NSEDATAENGINE folder environment variables for the current Windows user.

.DESCRIPTION
    The folders (data, data_fresh, data_ref, events, backtest_results, portfolio_results, NSEALGOENGINE source)
    and their variable names are defined ONCE, in contract\schema.yaml (`locations:`). This script asks
    nse_contract.py for them ("python nse_contract.py env --home <root>") and stores each one as a user
    environment variable, so every script - and the scheduled task - finds its files through them.
    Old variable names listed in -LegacyVars are removed.
    Called by register_project.ps1 and unpack_project.ps1; can also be run on its own after moving a folder.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File C:\temp\NSEDATAENGINE\migrate\set_env_vars.ps1
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot,
    [string[]]$LegacyVars = @('NSE_ALGO_HOME'),
    [string]$Python = 'python',
    [string[]]$PythonArgs = @()
)
$ErrorActionPreference = 'Stop'
if (-not $ProjectRoot) {
    $here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $ProjectRoot = Split-Path -Parent $here
}
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$isWin = ($env:OS -eq 'Windows_NT')
$contractPy = Join-Path $ProjectRoot 'nse_contract.py'
if (-not (Test-Path $contractPy)) { throw "nse_contract.py not found in $ProjectRoot" }

$lines = @(& $Python @PythonArgs $contractPy env --home $ProjectRoot)
if ($LASTEXITCODE -ne 0 -or $lines.Count -eq 0) {
    throw "python nse_contract.py env failed (is PyYAML installed? python -m pip install PyYAML)"
}
foreach ($line in $lines) {
    if ($line -match '^([A-Za-z0-9_]+)=(.+)$') {
        $name, $value = $Matches[1], $Matches[2].Trim()
        if ($isWin) { [Environment]::SetEnvironmentVariable($name, $value, 'User') }
        Set-Item -Path "Env:$name" -Value $value
        Write-Host ("Environment variable {0,-28} = {1}" -f $name, $value)
    }
}
foreach ($old in $LegacyVars) {
    if (-not $old) { continue }
    if ($isWin -and [Environment]::GetEnvironmentVariable($old, 'User')) {
        [Environment]::SetEnvironmentVariable($old, $null, 'User')
        Write-Host "Removed old environment variable $old"
    }
    Remove-Item -Path "Env:$old" -ErrorAction SilentlyContinue
}
