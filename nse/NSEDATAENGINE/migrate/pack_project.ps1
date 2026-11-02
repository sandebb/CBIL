<#
.SYNOPSIS
    OLD PC: packs every registered project into a migration bundle (numbered zip files + manifest).

.DESCRIPTION
    Reads the project registry (%LOCALAPPDATA%\AlgoProjects\projects.json, written by
    register_project.ps1). For each project it reads <project>\project_manifest.json and packs its
    parts, in order, into:

        <Destination>\migration_<date>\
            unpack_project.ps1          <- run this on the new PC
            bundle_manifest.json        what is in the bundle, SHA-256 of every zip, original paths
            README.txt
            <project name>\01_scripts.zip, 02_updater_state.zip, 03_..., ...

    Parts marked "regenerable" in the manifest (the big feature files) are skipped unless
    -IncludeRegenerable is given. Use -RequiredOnly for the smallest possible bundle.

    Large files: zips are written with .NET's ZipArchive, which switches to ZIP64 automatically, so
    multi-GB parquet files are fine (Compress-Archive in Windows PowerShell 5.1 fails above 2 GB).
    Parquet / zip files are stored without re-compression (they are already compressed - recompressing
    wastes time for ~0% gain); text files are compressed.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File C:\temp\NSEDATAENGINE\migrate\pack_project.ps1 -Destination E:\move
.EXAMPLE
    ... pack_project.ps1 -Destination E:\move -Project nsedataengine -IncludeRegenerable
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Destination,
    # Only these projects (names from the registry). Default: all registered projects.
    [string[]]$Project,
    # Also pack parts that can be rebuilt (feature files).
    [switch]$IncludeRegenerable,
    # Pack only parts marked required.
    [switch]$RequiredOnly
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Get-RegistryPath {
    $base = [Environment]::GetFolderPath('LocalApplicationData')
    return (Join-Path (Join-Path $base 'AlgoProjects') 'projects.json')
}

# Expand one manifest "include" pattern into files.
#   "data\logs\**"      -> every file under data\logs (recursive)
#   "*.py", "data\x.pq" -> files matching the wildcard in that folder (not recursive)
function Resolve-Include([string]$root, [string]$pattern) {
    $pattern = $pattern -replace '[\\/]', [IO.Path]::DirectorySeparatorChar
    if ($pattern.EndsWith('**')) {
        $dir = Join-Path $root ($pattern.Substring(0, $pattern.Length - 2).TrimEnd([IO.Path]::DirectorySeparatorChar))
        if (Test-Path $dir) { return @(Get-ChildItem -Path $dir -Recurse -File) }
        return @()
    }
    $full = Join-Path $root $pattern
    $parent = Split-Path $full -Parent
    if (-not (Test-Path $parent)) { return @() }
    return @(Get-ChildItem -Path $parent -Filter (Split-Path $full -Leaf) -File)
}

# Write a zip. $items: list of @{ Path = full path; Entry = name inside the zip (forward slashes) }
function Write-Zip([string]$zipPath, $items) {
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    $zip = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($it in $items) {
            $ext = [IO.Path]::GetExtension($it.Path).ToLowerInvariant()
            $level = if ($ext -in @('.parquet', '.zip', '.gz', '.7z')) { [System.IO.Compression.CompressionLevel]::NoCompression }
                     else { [System.IO.Compression.CompressionLevel]::Optimal }
            [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $it.Path, $it.Entry, $level)
        }
    } finally { $zip.Dispose() }
}

# ------------------------------------------------------------------------------------------------
$regPath = Get-RegistryPath
if (-not (Test-Path $regPath)) {
    throw "No project registry at $regPath. Run migrate\register_project.ps1 in each project first."
}
$registry = @(Get-Content $regPath -Raw | ConvertFrom-Json)
if ($Project) { $registry = @($registry | Where-Object { $Project -contains $_.name }) }
if ($registry.Count -eq 0) { throw 'No matching registered projects.' }

$bundle = Join-Path $Destination ("migration_" + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Force -Path $bundle | Out-Null
$bundleInfo = [ordered]@{
    created         = (Get-Date).ToString('s')
    source_computer = $env:COMPUTERNAME
    projects        = @()
}

foreach ($reg in $registry) {
    # The env var wins over the registry path (the folder may have been moved since registering).
    $root = [Environment]::GetEnvironmentVariable($reg.env_var)
    if (-not $root -or -not (Test-Path $root)) { $root = $reg.path }
    if (-not (Test-Path (Join-Path $root 'project_manifest.json'))) {
        Write-Warning "Project '$($reg.name)': no project_manifest.json in $root - skipped"; continue
    }
    $m = Get-Content (Join-Path $root 'project_manifest.json') -Raw | ConvertFrom-Json
    $projDir = Join-Path $bundle $m.name
    New-Item -ItemType Directory -Force -Path $projDir | Out-Null
    Write-Host "`n=== $($m.name)  ($root)" -ForegroundColor Cyan
    $partsInfo = @()

    foreach ($part in ($m.parts | Sort-Object order)) {
        $tag = '{0:D2}_{1}' -f [int]$part.order, $part.name
        if ($part.regenerable -and -not $IncludeRegenerable) { Write-Host "  $tag : skipped (regenerable)"; continue }
        if ($RequiredOnly -and -not $part.required) { Write-Host "  $tag : skipped (optional)"; continue }
        $items = @()
        foreach ($pat in @($part.include)) {
            if (-not $pat) { continue }
            foreach ($f in (Resolve-Include $root $pat)) {
                $rel = $f.FullName.Substring($root.TrimEnd('\', '/').Length + 1) -replace '\\', '/'
                $items += @{ Path = $f.FullName; Entry = $rel }
            }
        }
        foreach ($ext in @($part.external)) {          # absolute paths -> archive/<file name>
            if ($ext -and (Test-Path $ext)) { $items += @{ Path = (Resolve-Path $ext).Path; Entry = 'archive/' + (Split-Path $ext -Leaf) } }
            elseif ($ext) { Write-Warning "  $tag : external file not found: $ext" }
        }
        # the same file can match two patterns - keep it once
        $items = @($items | Group-Object { $_.Entry } | ForEach-Object { $_.Group[0] })
        if ($items.Count -eq 0) { Write-Host "  $tag : nothing to pack"; continue }
        $zipName = "$tag.zip"
        $zipPath = Join-Path $projDir $zipName
        $sw = [Diagnostics.Stopwatch]::StartNew()
        Write-Zip $zipPath $items
        $hash = (Get-FileHash -Algorithm SHA256 -Path $zipPath).Hash
        $size = (Get-Item $zipPath).Length
        Write-Host ("  {0} : {1} files, {2:N1} MB, {3:N0}s" -f $tag, $items.Count, ($size / 1MB), $sw.Elapsed.TotalSeconds)
        $partsInfo += [ordered]@{
            order = [int]$part.order; name = $part.name; file = "$($m.name)/$zipName"; sha256 = $hash
            bytes = $size; file_count = $items.Count; required = [bool]$part.required; why = $part.why
        }
    }
    $bundleInfo.projects += [ordered]@{
        name = $m.name; description = $m.description; env_var = $m.env_var
        legacy_env_vars = $m.legacy_env_vars; legacy_names = $m.legacy_names
        source_path = $root; default_path = $m.default_path
        python_requirements = $m.python_requirements; verify_commands = $m.verify_commands
        scheduled_task = $m.scheduled_task; parts = $partsInfo
    }
}

ConvertTo-Json -InputObject $bundleInfo -Depth 8 | Set-Content -Path (Join-Path $bundle 'bundle_manifest.json') -Encoding UTF8
Copy-Item (Join-Path $PSScriptRoot 'unpack_project.ps1') $bundle
$setupScript = Join-Path $PSScriptRoot 'setup_new_pc.ps1'
if (Test-Path $setupScript) { Copy-Item $setupScript $bundle }
@"
Migration bundle created $($bundleInfo.created) on $($bundleInfo.source_computer)

On the NEW PC:
  1. Copy this whole folder to the new PC.
  2. In PowerShell (normal, not admin), in this folder:
        powershell -ExecutionPolicy Bypass -File .\setup_new_pc.ps1
     It installs Python 3.12 if needed (winget), checks (SHA-256) and unpacks every project to C:\temp\<folder>
     (-Root to change), restores the saved settings variables, runs each project's setup steps from its
     project_manifest.json (packages, virtual environments, environment variables, scheduled tasks) and
     verifies every project. NSEUIENGINE asks for the Fyers credentials again (DPAPI secrets do not move).
  Without setup_new_pc.ps1 (older bundle): powershell -ExecutionPolicy Bypass -File .\unpack_project.ps1
"@ | Set-Content -Path (Join-Path $bundle 'README.txt') -Encoding UTF8

$total = (Get-ChildItem $bundle -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ("`nBundle ready: {0}  ({1:N1} GB)" -f $bundle, ($total / 1GB)) -ForegroundColor Green
