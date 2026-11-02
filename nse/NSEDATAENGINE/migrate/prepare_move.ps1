<#
.SYNOPSIS
    OLD PC: packs every NSE project into one migration bundle for setup_new_pc.ps1.

.DESCRIPTION
        powershell -ExecutionPolicy Bypass -File C:\temp\NSEDATAENGINE\migrate\prepare_move.ps1 -Destination E:\move

    1. registers every project folder under -Root that has a project_manifest.json (NSEDATAENGINE,
       NSEALGOENGINE, NSEMLENGINE, NSEUIENGINE, and any project added later) in
       %LOCALAPPDATA%\AlgoProjects\projects.json - existing scheduled tasks are not touched
    2. saves the user variables that are settings, not paths (STATICIP_PROXY, STATIC_IP, FYERS_ID,
       FYERS_CLIENT_ID, FYERS_REDIRECT_URI, PYTHONUTF8) to user_env.json in the bundle. Folder variables are
       recomputed on the new PC; daily Fyers tokens are not copied (they expire)
    3. packs every project with pack_project.ps1 (SHA-256 per zip) and puts setup_new_pc.ps1 in the bundle

    Then copy the bundle folder to the new PC and run setup_new_pc.ps1 from it.

    The bundle can contain credentials (NSEALGOENGINE\.env if present, STATICIP_PROXY). Keep it on a
    private drive and delete it after the move.

.PARAMETER IncludeRegenerable
    Also pack files that can be rebuilt (feature files, NSEMLENGINE dataset). Bigger bundle, faster setup.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Destination,
    [string]$Root = 'C:\temp',
    [switch]$IncludeRegenerable,
    [switch]$RequiredOnly
)
$ErrorActionPreference = 'Stop'
$here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$register = Join-Path $here 'register_project.ps1'
$pack = Join-Path $here 'pack_project.ps1'

# ---- 1. register every project --------------------------------------------------------------------
Write-Host "[1] Registering projects under $Root" -ForegroundColor Cyan
$names = @()
foreach ($d in @(Get-ChildItem -Path $Root -Directory)) {
    $mf = Join-Path $d.FullName 'project_manifest.json'
    if (-not (Test-Path $mf)) { continue }
    $m = Get-Content $mf -Raw | ConvertFrom-Json
    # -RegistryOnly: only the registry entry - variables and scheduled tasks on this PC stay as they are
    & $register -ProjectRoot $d.FullName -RegistryOnly
    $names += $m.name
}
if (-not $names.Count) { throw "No project_manifest.json found under $Root" }

# ---- 2. pack -------------------------------------------------------------------------------------
Write-Host "`n[2] Packing $($names -join ', ')" -ForegroundColor Cyan
$before = @(Get-ChildItem -Path $Destination -Directory -Filter 'migration_*' -ErrorAction SilentlyContinue | ForEach-Object FullName)
$packArgs = @{ Destination = $Destination; Project = $names }
if ($IncludeRegenerable) { $packArgs.IncludeRegenerable = $true }
if ($RequiredOnly) { $packArgs.RequiredOnly = $true }
& $pack @packArgs
$bundle = Get-ChildItem -Path $Destination -Directory -Filter 'migration_*' | Where-Object { $before -notcontains $_.FullName } |
    Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $bundle) { throw "pack_project.ps1 did not create a bundle in $Destination" }

# ---- 3. settings variables -------------------------------------------------------------------------
Write-Host "`n[3] Saving user settings variables" -ForegroundColor Cyan
$keep = @('STATICIP_PROXY', 'STATIC_IP', 'FYERS_ID', 'FYERS_CLIENT_ID', 'FYERS_REDIRECT_URI', 'PYTHONUTF8')
$saved = [ordered]@{}
foreach ($k in $keep) {
    $v = [Environment]::GetEnvironmentVariable($k, 'User')
    if ($v) { $saved[$k] = $v; Write-Host "  $k" }
}
ConvertTo-Json -InputObject $saved | Set-Content -Path (Join-Path $bundle.FullName 'user_env.json') -Encoding UTF8
Copy-Item (Join-Path $here 'setup_new_pc.ps1') $bundle.FullName -Force

Write-Host "`nBundle ready: $($bundle.FullName)" -ForegroundColor Green
Write-Host 'On the new PC: copy the folder, then in PowerShell inside it:'
Write-Host '    powershell -ExecutionPolicy Bypass -File .\setup_new_pc.ps1'
