<#
.SYNOPSIS
    Sets the NSEMLENGINE environment variables for the current Windows user (no admin rights needed),
    and with -Gpu builds a Python 3.12 virtual environment with PyTorch for the Radeon GPU.

.DESCRIPTION
    Always:  NSEMLENGINE_HOME, NSEMLENGINE_WORK_DIR (and NSEDATAENGINE_HOME if it is not set yet).
    -Gpu:    creates <project>\.venv with Python 3.12 (AMD's ROCm PyTorch wheels for Windows are cp312 only),
             installs requirements.txt and AMD's torch 2.9.1 + ROCm 7.2.1 wheels (the same ones NSEALGOENGINE
             uses), checks that the GPU is visible, and sets NSEMLENGINE_PYTHON to that venv's python.exe.
             Needs the "AMD Software: PyTorch on Windows Edition" graphics driver installed first.
    nseml.cmd runs "python -m nseml" with %NSEMLENGINE_PYTHON% when it is set, else the python on PATH.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEMLENGINE\setup_env.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File %NSE_ROOT%\code\NSEMLENGINE\setup_env.ps1 -Gpu
#>
param(
    [string]$ProjectRoot,
    [string]$WorkDir,
    [string]$DataEngineHome,
    [switch]$Gpu
)
$ErrorActionPreference = 'Stop'
if (-not $ProjectRoot) { $ProjectRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path } }
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
if (-not $WorkDir) { $WorkDir = Join-Path $ProjectRoot 'work' }
$vars = [ordered]@{ NSEMLENGINE_HOME = $ProjectRoot; NSEMLENGINE_WORK_DIR = $WorkDir }
# NSEDATAENGINE_HOME is owned by NSEDATAENGINE (its register_project.ps1); only set it if missing.
$existing = [Environment]::GetEnvironmentVariable('NSEDATAENGINE_HOME', 'User')
if ($DataEngineHome) { $vars['NSEDATAENGINE_HOME'] = (Resolve-Path $DataEngineHome).Path }
elseif (-not $existing) {
    $guess = Join-Path (Split-Path -Parent $ProjectRoot) 'NSEDATAENGINE'
    if (Test-Path $guess) { $vars['NSEDATAENGINE_HOME'] = $guess }
}

if ($Gpu) {
    $venv = Join-Path $ProjectRoot '.venv'
    $py = Join-Path $venv 'Scripts\python.exe'
    if (-not (Test-Path $py)) {
        Write-Host 'Creating .venv with Python 3.12 ...'
        & py -3.12 -m venv $venv
        if (-not (Test-Path $py)) { throw 'Python 3.12 not found (py -3.12). Install it (winget install Python.Python.3.12) and run again.' }
    }
    & $py -m pip install --disable-pip-version-check -q --upgrade pip
    # everything except torch from requirements.txt, then AMD's ROCm build of torch
    $req = Get-Content (Join-Path $ProjectRoot 'requirements.txt') | Where-Object { $_ -notmatch '^\s*torch' }
    $tmp = Join-Path $env:TEMP 'nseml_requirements_gpu.txt'
    $req | Set-Content -Path $tmp -Encoding ASCII
    & $py -m pip install --disable-pip-version-check -q -r $tmp
    if ($LASTEXITCODE -ne 0) { throw 'pip install of the requirements failed' }
    $r = 'https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1'
    & $py -m pip install --disable-pip-version-check --no-cache-dir "$r/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl" `
        "$r/rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl" "$r/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl" `
        "$r/rocm-7.2.1.tar.gz"
    & $py -m pip install --disable-pip-version-check --no-cache-dir "$r/torch-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl"
    if ($LASTEXITCODE -ne 0) { throw 'ROCm PyTorch install failed' }
    & $py -c "import torch; ok = torch.cuda.is_available(); print('PyTorch', torch.__version__, '| GPU:', torch.cuda.get_device_name(0) if ok else 'NOT available - install the AMD PyTorch driver; deeplob will run on the CPU')"
    $vars['NSEMLENGINE_PYTHON'] = $py
}

foreach ($k in $vars.Keys) {
    [Environment]::SetEnvironmentVariable($k, $vars[$k], 'User')
    Set-Item -Path "Env:$k" -Value $vars[$k]
    Write-Host ("{0,-22} = {1}" -f $k, $vars[$k])
}
Write-Host 'Done. Open a NEW terminal, then:  %NSE_ROOT%\code\NSEMLENGINE\nseml.cmd paths'
