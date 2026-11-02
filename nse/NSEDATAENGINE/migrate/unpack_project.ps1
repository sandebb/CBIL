<#
.SYNOPSIS
    NEW PC: restores every project in a migration bundle made by pack_project.ps1.

.DESCRIPTION
    Run from inside the bundle folder (a copy of this script is placed there by pack_project.ps1).
    For each project in bundle_manifest.json:
      1. asks where the project should live (Enter = same path as on the old PC); -Paths skips the prompt
      2. checks every zip against its SHA-256 from the old PC (a corrupted copy is refused, not unpacked)
      3. unpacks the zips in their numbered order into that folder, keeping every relative path, so the
         scripts find their data exactly as before (existing files are overwritten)
      4. sets the project's user environment variable (e.g. NSEDATAENGINE_HOME) and registers the project in
         %LOCALAPPDATA%\AlgoProjects\projects.json, so the next move works the same way
      5. installs the Python packages from the project's requirements file into the Python on PATH
      6. runs the project's verify command(s) (e.g. "python nse_updater.py check")
      7. asks whether to re-create the scheduled task at the new path (-ScheduledTask / -NoScheduledTask)
    A log of everything is written to <project>\migrate\unpack_log.txt.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\unpack_project.ps1
.EXAMPLE
    .\unpack_project.ps1 -Paths @{ nse_data = 'D:\algo\nse_data' } -ScheduledTask
#>
[CmdletBinding()]
param(
    # Bundle folder. Default: the folder this script is in (set below; see note in register_project.ps1).
    [string]$BundleDir,
    # project name -> install folder; projects not listed are asked for interactively
    [hashtable]$Paths = @{},
    [switch]$ScheduledTask,
    [switch]$NoScheduledTask,
    [switch]$SkipPython
)
$ErrorActionPreference = 'Stop'
if (-not $BundleDir) {
    $BundleDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
}
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Test-Windows { return ($env:OS -eq 'Windows_NT') }

function Get-RegistryPath {
    $base = [Environment]::GetFolderPath('LocalApplicationData')
    return (Join-Path (Join-Path $base 'AlgoProjects') 'projects.json')
}

# Find a Python 3 to use: the "py" launcher (standard on Windows) or "python" on PATH.
function Get-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) { return @('py', '-3') }
    foreach ($n in @('python', 'python3')) {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        # Windows ships a "python.exe" stub that only opens the Microsoft Store - skip it
        if ($c -and $c.Source -notlike '*WindowsApps*') { return @($n) }
    }
    return $null
}

# Unpack a zip into $root, overwriting files; entries use forward slashes, converted to this OS.
function Expand-ZipOverwrite([string]$zipPath, [string]$root) {
    $zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        foreach ($e in $zip.Entries) {
            if (-not $e.Name) { continue }                       # folder entry
            $rel = $e.FullName -replace '/', [IO.Path]::DirectorySeparatorChar
            $target = [IO.Path]::GetFullPath((Join-Path $root $rel))
            if (-not $target.StartsWith([IO.Path]::GetFullPath($root))) { throw "unsafe path in zip: $($e.FullName)" }
            New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($e, $target, $true)
        }
        return $zip.Entries.Count
    } finally { $zip.Dispose() }
}

# ------------------------------------------------------------------------------------------------
$BundleDir = (Resolve-Path $BundleDir).Path
$bm = Get-Content (Join-Path $BundleDir 'bundle_manifest.json') -Raw | ConvertFrom-Json
Write-Host "Bundle from $($bm.source_computer), created $($bm.created): $(@($bm.projects).Count) project(s)" -ForegroundColor Cyan

foreach ($p in @($bm.projects)) {
    Write-Host "`n=== $($p.name) - $($p.description)" -ForegroundColor Cyan
    # --- 1. where should it go? -----------------------------------------------------------------
    $default = $p.source_path
    if ($Paths.ContainsKey($p.name)) { $root = $Paths[$p.name] }
    else {
        $ans = Read-Host "Folder for project '$($p.name)' [Enter = $default]"
        $root = if ([string]::IsNullOrWhiteSpace($ans)) { $default } else { $ans.Trim('"', ' ') }
    }
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $root = (Resolve-Path $root).Path
    $logDir = Join-Path $root 'migrate'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $logFile = Join-Path $logDir 'unpack_log.txt'
    function Log([string]$msg) { $line = "$(Get-Date -Format s)  $msg"; Write-Host "  $msg"; Add-Content -Path $logFile -Value $line }
    Log "unpacking '$($p.name)' to $root (bundle $BundleDir, from $($bm.source_computer):$($p.source_path))"

    # --- 2 + 3. verify and unpack in order --------------------------------------------------------
    $ok = $true
    foreach ($part in (@($p.parts) | Sort-Object order)) {
        $zipPath = Join-Path $BundleDir ($part.file -replace '/', [IO.Path]::DirectorySeparatorChar)
        if (-not (Test-Path $zipPath)) {
            if ($part.required) { Log "MISSING required part $($part.file) - project not complete"; $ok = $false }
            else { Log "optional part $($part.file) not in bundle - skipped" }
            continue
        }
        $hash = (Get-FileHash -Algorithm SHA256 -Path $zipPath).Hash
        if ($hash -ne $part.sha256) {
            Log "CHECKSUM MISMATCH for $($part.file) - file was damaged while copying; copy it again. Not unpacked."
            if ($part.required) { $ok = $false }
            continue
        }
        $n = Expand-ZipOverwrite $zipPath $root
        Log ("{0:D2} {1}: {2} files restored" -f [int]$part.order, $part.name, $n)
    }
    if (-not $ok) { Write-Warning "Project '$($p.name)' is incomplete - fix the problems above and run this script again."; continue }

    # --- 4. environment variable + registry -----------------------------------------------------
    if (Test-Windows) { [Environment]::SetEnvironmentVariable($p.env_var, $root, 'User') }
    Set-Item -Path "Env:$($p.env_var)" -Value $root
    $regPath = Get-RegistryPath
    New-Item -ItemType Directory -Force -Path (Split-Path $regPath) | Out-Null
    $reg = @()
    if (Test-Path $regPath) { $reg = @(Get-Content $regPath -Raw | ConvertFrom-Json) }
    $reg = @($reg | Where-Object { $_.name -ne $p.name -and (@($p.legacy_names) -notcontains $_.name) })
    $reg += [pscustomobject]@{ name = $p.name; env_var = $p.env_var; path = $root
                              registered_at = (Get-Date).ToString('s'); computer = $env:COMPUTERNAME }
    ConvertTo-Json -InputObject @($reg) -Depth 5 | Set-Content -Path $regPath -Encoding UTF8
    Log "environment variable $($p.env_var) = $root; registered in $regPath"

    # --- 5 + 6. Python packages and verification ---------------------------------------------------
    if (-not $SkipPython) {
        # @() keeps a one-element result as an array (PowerShell would otherwise unwrap it to a string)
        $py = @(Get-Python | Where-Object { $_ })
        if ($py.Count -eq 0) {
            Log 'Python not found. Install Python 3.11+ from python.org (tick "Add python.exe to PATH"), then run:'
            Log "  cd `"$root`"; python -m pip install -r $($p.python_requirements); python $($p.verify_commands -join '; python ')"
        } else {
            $pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)
            if ($p.python_requirements -and (Test-Path (Join-Path $root $p.python_requirements))) {
                Log "installing Python packages from $($p.python_requirements) ..."
                & $pyExe @pyArgs -m pip install --user --disable-pip-version-check -q -r (Join-Path $root $p.python_requirements) 2>&1 |
                    ForEach-Object { Add-Content -Path $logFile -Value "    $_" }
            }
            # folder variables from contract\schema.yaml (needs the packages above)
            $setEnv = Join-Path $root 'migrate\set_env_vars.ps1'
            if (Test-Path $setEnv) {
                try { & $setEnv -ProjectRoot $root -LegacyVars @($p.legacy_env_vars) -Python $pyExe -PythonArgs $pyArgs | ForEach-Object { Log "    $_" } }
                catch { Log "folder variables not set: $_" }
            }
            Push-Location $root
            try {
                foreach ($vc in @($p.verify_commands)) {
                    if (-not $vc) { continue }
                    Log "verify: python $vc"
                    $out = & $pyExe @pyArgs @($vc -split ' ') 2>&1
                    $out | ForEach-Object { Log "    $_" }
                }
            } finally { Pop-Location }
        }
    }

    # --- 7. scheduled task -----------------------------------------------------------------------
    if ($p.scheduled_task -and -not $NoScheduledTask) {
        $t = $p.scheduled_task
        $create = $ScheduledTask
        if (-not $create) { $create = ((Read-Host "Create scheduled task '$($t.name)' ($($t.schedule) $($t.days) $($t.time))? [y/N]") -match '^[yY]') }
        if ($create) {
            if (-not (Test-Windows)) { Log 'scheduled tasks need Windows - skipped' }
            else {
                $cmd = '"' + (Join-Path $root $t.script) + '"'
                $taskArgs = @('/Create', '/TN', $t.name, '/TR', $cmd, '/SC', $t.schedule, '/ST', $t.time, '/F')
                if ($t.days) { $taskArgs += @('/D', $t.days) }
                & schtasks.exe @taskArgs | Out-Null
                Log "scheduled task '$($t.name)' -> $cmd"
            }
        }
    }
    Log "project '$($p.name)' ready"
}
Write-Host "`nDone. Open a NEW terminal so the environment variables are visible there." -ForegroundColor Green
