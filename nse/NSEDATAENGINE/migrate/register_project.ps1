<#
.SYNOPSIS
    Registers this project on THIS PC: sets its environment variable and adds it to the machine's
    project registry, so pack_project.ps1 knows what to pack when you change PCs.

.DESCRIPTION
    Run once on the current PC (and it is run automatically by unpack_project.ps1 on a new PC).

    What it does
      1. Reads ..\project_manifest.json (the project folder is the parent of this "migrate" folder).
      2. Sets the user environment variable named in the manifest (NSEDATAENGINE_HOME) to the project folder,
         plus the folder variables defined in contract\schema.yaml (via set_env_vars.ps1), and removes the
         old variable names listed in legacy_env_vars (NSE_ALGO_HOME).
         The Python scripts use it to find their data, wherever the project lives.
      3. Adds/updates the project in the project registry:
             %LOCALAPPDATA%\AlgoProjects\projects.json
         One entry per project: name, env var, folder. pack_project.ps1 packs every registered project,
         so projects you add later (e.g. your trading-system code) travel together.
      4. Optionally (-ScheduledTask) creates the Windows scheduled task described in the manifest.

    No administrator rights needed: user-level environment variable and a user-level scheduled task.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File C:\temp\NSEDATAENGINE\migrate\register_project.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File C:\temp\NSEDATAENGINE\migrate\register_project.ps1 -ScheduledTask
#>
[CmdletBinding()]
param(
    # Project folder. Default: the parent of the folder this script is in (worked out below - Windows
    # PowerShell 5.1 leaves $PSScriptRoot empty inside parameter defaults, so it cannot be used here).
    [string]$ProjectRoot,
    # Also create / replace the scheduled task from the manifest.
    [switch]$ScheduledTask,
    # Only add / refresh the project registry entry (used by prepare_move.ps1 on the old PC).
    [switch]$RegistryOnly
)
$ErrorActionPreference = 'Stop'
if (-not $ProjectRoot) {
    $here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $ProjectRoot = Split-Path -Parent $here
}

# Windows PowerShell 5.1 has no $IsWindows variable; PowerShell 7 has. This works on both.
function Test-Windows { return ($env:OS -eq 'Windows_NT') }

# Where the registry of projects lives (per user, survives reboots, not inside any project).
function Get-RegistryPath {
    $base = [Environment]::GetFolderPath('LocalApplicationData')
    return (Join-Path (Join-Path $base 'AlgoProjects') 'projects.json')
}

$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$manifestPath = Join-Path $ProjectRoot 'project_manifest.json'
if (-not (Test-Path $manifestPath)) { throw "project_manifest.json not found in $ProjectRoot" }
$m = Get-Content $manifestPath -Raw | ConvertFrom-Json

# --- 1. environment variable ---------------------------------------------------------------------
# 'User' scope = stored in the registry under HKCU\Environment, visible to new processes of this user
# (including scheduled tasks). The current window gets it too via $env:.
if (-not $RegistryOnly) {
if (Test-Windows) {
    [Environment]::SetEnvironmentVariable($m.env_var, $ProjectRoot, 'User')
}
Set-Item -Path "Env:$($m.env_var)" -Value $ProjectRoot
Write-Host "Environment variable $($m.env_var) = $ProjectRoot"

# --- 1b. folder variables from the column contract (contract\schema.yaml, `locations:`) -----------
# NSEDATAENGINE_DATA_DIR, _FRESH_DIR, _REF_DIR, ... and NSEALGO_SRC; old names (legacy_env_vars) are removed.
$setEnv = Join-Path $PSScriptRoot 'set_env_vars.ps1'
if (-not $PSScriptRoot) { $setEnv = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'set_env_vars.ps1' }
# only for a project that defines folder variables through a column contract (NSEDATAENGINE)
if ((Test-Path $setEnv) -and (Test-Path (Join-Path $ProjectRoot 'nse_contract.py'))) {
    try { & $setEnv -ProjectRoot $ProjectRoot -LegacyVars @($m.legacy_env_vars) }
    catch { Write-Warning "Folder variables not set: $_  (the scripts then use the default folders under $ProjectRoot)" }
}

}   # end of: if (-not $RegistryOnly)

# --- 2. project registry -------------------------------------------------------------------------
$regPath = Get-RegistryPath
New-Item -ItemType Directory -Force -Path (Split-Path $regPath) | Out-Null
$reg = @()
if (Test-Path $regPath) { $reg = @(Get-Content $regPath -Raw | ConvertFrom-Json) }
# replace any older entry for this project, including entries under its old names (legacy_names)
$reg = @($reg | Where-Object { $_.name -ne $m.name -and (@($m.legacy_names) -notcontains $_.name) })
$reg += [pscustomobject]@{
    name          = $m.name
    env_var       = $m.env_var
    path          = $ProjectRoot
    registered_at = (Get-Date).ToString('s')
    computer      = $env:COMPUTERNAME
}
ConvertTo-Json -InputObject @($reg) -Depth 5 | Set-Content -Path $regPath -Encoding UTF8
Write-Host "Registered project '$($m.name)' in $regPath ($($reg.Count) project(s) registered)"

# --- 3. scheduled task (optional) ----------------------------------------------------------------
if ($ScheduledTask -and $m.scheduled_task -and -not $RegistryOnly) {
    if (-not (Test-Windows)) { Write-Warning 'Scheduled tasks can only be created on Windows - skipped.' }
    else {
        $t = $m.scheduled_task
        $cmd = '"' + (Join-Path $ProjectRoot $t.script) + '"'
        # /F replaces an existing task of the same name (e.g. one pointing at the old folder)
        $taskArgs = @('/Create', '/TN', $t.name, '/TR', $cmd, '/SC', $t.schedule, '/ST', $t.time, '/F')
        if ($t.days) { $taskArgs += @('/D', $t.days) }
        & schtasks.exe @taskArgs | Out-Host
        Write-Host "Scheduled task '$($t.name)' -> $cmd ($($t.schedule) $($t.days) $($t.time))"
    }
}
Write-Host 'Done. Open a NEW terminal window for the environment variable to be visible there.'
