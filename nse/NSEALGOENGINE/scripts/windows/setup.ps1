<#
.SYNOPSIS
  One-time Windows 11 setup for nsealgo. Run from the project root in PowerShell:
    powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
  Options:
    -Extras "dev,zerodha"     pip extras to install (broker SDKs you use; see pyproject.toml)
    -PowerPlan                switch to the Ultimate Performance plan (measure with `nsealgo bench` first/after)
    -Gpu                      PyTorch for the Radeon GPU (AMD ROCm 7.2.1 wheels). Needs Python 3.12 and, first,
                              the "AMD Software: PyTorch on Windows Edition 7.2.1" graphics driver.
    -DefenderDataExclusions   (Administrator) exclude ONLY nsealgo's data folders from real-time scanning.
                              Never exclude python.exe or the virtual environment: that would stop Defender
                              scanning every file any Python program touches, including packages pip installs.
#>
param([string]$Extras = "dev", [switch]$PowerPlan, [switch]$DefenderDataExclusions, [switch]$Gpu)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $root

# 1. Virtual environment (Python 3.10+ via the py launcher)
if (-not (Test-Path ".venv\Scripts\python.exe")) { if ($Gpu) { py -3.12 -m venv .venv } else { py -3 -m venv .venv } }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[$Extras]"
# Some broker SDKs pull in PyPI packages named like standard-library modules ('asyncio' from fyers-apiv3, 'uuid' from
# upstox-python-sdk); they shadow Python's own modules and break things, so they are removed again.
foreach ($shadow in "asyncio", "uuid") {
    & .\.venv\Scripts\python.exe -m pip show $shadow 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { & .\.venv\Scripts\python.exe -m pip uninstall -y -q $shadow }
}

# 1b. Optional: PyTorch on the Radeon 8060S (AMD's Windows wheels, ROCm 7.2.1; cp312 only)
if ($Gpu) {
    $v = & .\.venv\Scripts\python.exe -c "import sys; print('%d.%d' % sys.version_info[:2])"
    if ($v -ne "3.12") { throw "-Gpu needs Python 3.12 (AMD's ROCm wheels are cp312). Install it, delete .venv, and re-run." }
    $r = "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1"
    & .\.venv\Scripts\python.exe -m pip install --no-cache-dir "$r/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl" `
        "$r/rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl" "$r/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl" `
        "$r/rocm-7.2.1.tar.gz"
    & .\.venv\Scripts\python.exe -m pip install --no-cache-dir "$r/torch-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl"
    & .\.venv\Scripts\python.exe -c "import torch; ok = torch.cuda.is_available(); print('PyTorch', torch.__version__, '| ROCm', torch.version.hip, '| GPU:', torch.cuda.get_device_name(0) if ok else 'NOT available - check the AMD PyTorch driver')"
    & .\.venv\Scripts\nsealgo.exe gpu --fresh
}

# 2. UTF-8 mode for this user's Python processes (rupee signs in logs and reports, BOM-free files)
[Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "User")
$env:PYTHONUTF8 = "1"

# 3. Optional: Ultimate Performance power plan (reuses the scheme if it already exists)
if ($PowerPlan) {
    $line = powercfg /list | Select-String "Ultimate Performance" | Select-Object -First 1
    if (-not $line) { $line = powercfg -duplicatescheme e9a42b02-d5df-448d-aa00-03f14749eb61 }
    $guid = ([regex]'[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}').Match("$line").Value
    powercfg /setactive $guid
    Write-Host "Ultimate Performance active ($guid). Revert with: powercfg /setactive SCHEME_BALANCED"
}

# 4. Optional: Defender exclusions for data folders only (needs Administrator; respect your org's policy)
if ($DefenderDataExclusions) {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) { throw "Re-run PowerShell as Administrator to use -DefenderDataExclusions" }
    foreach ($d in @("data", "runs", "recordings", ".cache", "bench_run")) {
        $p = Join-Path $root $d
        New-Item -ItemType Directory -Force -Path $p | Out-Null
        Add-MpPreference -ExclusionPath $p
        Write-Host "Defender exclusion added: $p"
    }
}

# 5. Sanity checks
& .\.venv\Scripts\nsealgo.exe hardware
& .\.venv\Scripts\nsealgo.exe doctor
Write-Host "`nNext: nsealgo demo; nsealgo bench; see docs\WINDOWS.md"
