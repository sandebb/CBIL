<#
.SYNOPSIS
  Moves the data of all four engines into ONE shared data area, one folder per engine (option A):

      C:\NSE\DATA\NSEDATAENGINE   data, data_fresh, data_fresh_2019, data_ref, backtest_results, portfolio_results, signals
      C:\NSE\DATA\NSEMLENGINE     (was NSEMLENGINE\work: dataset, models, predictions, results, logs)
      C:\NSE\DATA\NSEALGOENGINE   data, runs, .cache, recordings (created when nsealgo first runs)
      C:\NSE\DATA\NSEUIENGINE     state (incl. the encrypted secrets and the trader's state), reports, logs
      C:\NSE\DATA\_archive        NSEDATAENGINE\backup_pre_contract_20261003 + the old data files in C:\temp
      C:\NSE\DATA\_migration      journal of every move (for -Rollback)

  Code stays where it is. Every engine finds its folder through environment variables (set here):
  NSE_DATA_ROOT, NSEDATAENGINE_*_DIR, NSEMLENGINE_WORK_DIR, NSEALGOENGINE_DATA_DIR, NSEUIENGINE_DATA_DIR.

.USAGE
  powershell -ExecutionPolicy Bypass -File C:\temp\NSEDATAENGINE\migrate\move_to_shared_data.ps1            # plan only
  powershell -ExecutionPolicy Bypass -File C:\temp\NSEDATAENGINE\migrate\move_to_shared_data.ps1 -Execute   # move
  powershell -ExecutionPolicy Bypass -File C:\temp\NSEDATAENGINE\migrate\move_to_shared_data.ps1 -Rollback  # undo

  Folders are MOVED (renamed - instant, no extra space, when C:\NSE is on the same drive as C:\temp), each one
  checked (file count + bytes) and journalled. Refuses to start while an NSE scheduled task or an engine's
  Python process is running. Run it after 22:30 on a weekday or at the weekend; close Excel / LibreOffice / IDEs
  that have files of these folders open.
#>
param(
    [string]$Root = "C:\NSE\DATA",
    [string]$Base = "C:\temp",
    [switch]$Execute,
    [switch]$Rollback
)
$ErrorActionPreference = "Stop"
$JDir = Join-Path $Root "_migration"

function Get-Stats([string]$p) {
    if (-not (Test-Path -LiteralPath $p)) { return $null }
    $it = Get-Item -LiteralPath $p -Force
    if (-not $it.PSIsContainer) { return [pscustomobject]@{ Files = 1; Bytes = [int64]$it.Length } }
    $f = @(Get-ChildItem -LiteralPath $p -Recurse -Force -File -ErrorAction SilentlyContinue)
    $b = ($f | Measure-Object -Property Length -Sum).Sum
    return [pscustomobject]@{ Files = $f.Count; Bytes = [int64]$(if ($b) { $b } else { 0 }) }
}
function Fmt([int64]$b) { if ($b -ge 1GB) { "{0:N1} GB" -f ($b / 1GB) } elseif ($b -ge 1MB) { "{0:N0} MB" -f ($b / 1MB) } else { "{0:N0} KB" -f ($b / 1KB) } }

# ---------------------------------------------------------------------------------------------- rollback
if ($Rollback) {
    $jfile = Get-ChildItem (Join-Path $JDir "migration_*.json") -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1
    if (-not $jfile) { throw "no migration journal in $JDir" }
    $J = Get-Content $jfile.FullName -Raw | ConvertFrom-Json
    Write-Host "Rolling back $($jfile.Name)"
    $done = @($J.moves | Where-Object { $_.done })
    [array]::Reverse($done)
    foreach ($m in $done) {
        if (Test-Path -LiteralPath $m.src) { Write-Warning "skip (source exists again): $($m.src)"; continue }
        $parent = Split-Path -Parent $m.src
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        Move-Item -LiteralPath $m.dst -Destination $m.src
        Write-Host "  back: $($m.dst) -> $($m.src)"
    }
    foreach ($p in $J.env.PSObject.Properties) {
        $old = $p.Value.old
        [Environment]::SetEnvironmentVariable($p.Name, $(if ($old) { $old } else { $null }), "User")
        Write-Host ("  env {0} = {1}" -f $p.Name, $(if ($old) { $old } else { "<removed>" }))
    }
    $reg = Join-Path $Base "NSEUIENGINE\register_eod_tasks.ps1"
    if (Test-Path $reg) { Remove-Item Env:NSE_DATA_ROOT, Env:NSEUIENGINE_DATA_DIR -ErrorAction SilentlyContinue; & $reg | Out-Null }
    Write-Host "Rollback finished. Open a new terminal before running anything." -ForegroundColor Green
    exit 0
}

# ---------------------------------------------------------------------------------------------- plan
$moves = New-Object System.Collections.ArrayList
function Add-Move($eng, $src, $dst) { [void]$moves.Add([pscustomobject]@{ engine = $eng; src = $src; dst = $dst; files = 0; bytes = 0; done = $false }) }
foreach ($d in "data", "data_fresh", "data_fresh_2019", "data_ref", "backtest_results", "portfolio_results", "signals") {
    Add-Move "NSEDATAENGINE" "$Base\NSEDATAENGINE\$d" "$Root\NSEDATAENGINE\$d"
}
Add-Move "NSEMLENGINE" "$Base\NSEMLENGINE\work" "$Root\NSEMLENGINE"
foreach ($d in "data", "runs", ".cache", "recordings") { Add-Move "NSEALGOENGINE" "$Base\NSEALGOENGINE\$d" "$Root\NSEALGOENGINE\$d" }
foreach ($d in "state", "reports", "logs") { Add-Move "NSEUIENGINE" "$Base\NSEUIENGINE\$d" "$Root\NSEUIENGINE\$d" }
Add-Move "_archive" "$Base\NSEDATAENGINE\backup_pre_contract_20261003" "$Root\_archive\NSEDATAENGINE\backup_pre_contract_20261003"
Get-ChildItem -LiteralPath $Base -File -Force | Where-Object { $_.Extension -in ".parquet", ".zip" -or $_.Name -eq "data_quality_report.csv" } |
    ForEach-Object { Add-Move "_archive" $_.FullName "$Root\_archive\temp_root\$($_.Name)" }

$envs = [ordered]@{
    "NSE_DATA_ROOT"               = $Root
    "NSEDATAENGINE_DATA_DIR"      = "$Root\NSEDATAENGINE\data"
    "NSEDATAENGINE_EVENTS_DIR"    = "$Root\NSEDATAENGINE\data\events"
    "NSEDATAENGINE_FRESH_DIR"     = "$Root\NSEDATAENGINE\data_fresh"
    "NSEDATAENGINE_FRESH2019_DIR" = "$Root\NSEDATAENGINE\data_fresh_2019"
    "NSEDATAENGINE_REF_DIR"       = "$Root\NSEDATAENGINE\data_ref"
    "NSEDATAENGINE_BACKTEST_DIR"  = "$Root\NSEDATAENGINE\backtest_results"
    "NSEDATAENGINE_PORTFOLIO_DIR" = "$Root\NSEDATAENGINE\portfolio_results"
    "NSEDATAENGINE_SIGNALS_DIR"   = "$Root\NSEDATAENGINE\signals"
    "NSEMLENGINE_WORK_DIR"        = "$Root\NSEMLENGINE"
    "NSEALGOENGINE_DATA_DIR"      = "$Root\NSEALGOENGINE"
    "NSEUIENGINE_DATA_DIR"        = "$Root\NSEUIENGINE"
}

Write-Host "`nShared data area: $Root   (code stays in $Base)`n"
$total = [int64]0
$problems = @()
foreach ($m in $moves) {
    $s = Get-Stats $m.src
    if (-not $s) { $m.files = -1; continue }
    $m.files = $s.Files; $m.bytes = $s.Bytes; $total += $s.Bytes
    if (Test-Path -LiteralPath $m.dst) {
        $ds = Get-Stats $m.dst
        if ($ds.Files -gt 0) { $problems += "destination already has files: $($m.dst)" }
    }
}
Write-Host ("{0,-14} {1,9} {2,9}  {3}" -f "Engine", "Files", "Size", "From  ->  To")
foreach ($m in ($moves | Where-Object { $_.files -ge 0 })) {
    Write-Host ("{0,-14} {1,9} {2,9}  {3}  ->  {4}" -f $m.engine, $m.files, (Fmt $m.bytes), $m.src, $m.dst)
}
$skipped = @($moves | Where-Object { $_.files -lt 0 } | ForEach-Object { $_.src })
if ($skipped) { Write-Host ("Not present (nothing to move): " + ($skipped -join ", ")) }
Write-Host ("Total: {0}" -f (Fmt $total))
Write-Host "`nUser environment variables:"
foreach ($k in $envs.Keys) {
    $old = [Environment]::GetEnvironmentVariable($k, "User")
    Write-Host ("  {0,-28} {1}  ->  {2}" -f $k, $(if ($old) { $old } else { "<not set>" }), $envs[$k])
}

# safety checks
$sameVol = ([IO.Path]::GetPathRoot($Root)).ToUpper() -eq ([IO.Path]::GetPathRoot($Base)).ToUpper()
if (-not $sameVol) { Write-Warning "$Root is on another drive: folders will be COPIED then deleted (slow, needs $(Fmt $total) free)" }
$running = @()
if (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue) {
    $running = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match "NSE|nsealgo" -and $_.State -eq "Running" })
}
if ($running) { $problems += "scheduled task(s) running: " + (($running | ForEach-Object TaskName) -join ", ") }
$procs = @()
if (Get-Command Get-CimInstance -ErrorAction SilentlyContinue) {
    $procs = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "NSE(DATA|ML|UI|ALGO)ENGINE|nseml|nsealgo" })
}
if ($procs) { $problems += "engine Python process(es) running: " + (($procs | ForEach-Object { "$($_.ProcessId)" }) -join ", ") }
$locks = @($moves | Where-Object { $_.files -gt 0 -and (Get-Item -LiteralPath $_.src).PSIsContainer } |
    ForEach-Object { Get-ChildItem -LiteralPath $_.src -Recurse -Force -Filter ".~lock*" -ErrorAction SilentlyContinue })
if ($locks) { Write-Warning ("LibreOffice lock files found (a file may be open): " + (($locks | ForEach-Object FullName) -join ", ")) }

if ($problems) {
    Write-Host "`nCannot move now:" -ForegroundColor Red
    $problems | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    if ($Execute) { exit 1 }
}
if (-not $Execute) { Write-Host "`nPlan only. Run again with -Execute to move." -ForegroundColor Yellow; exit 0 }

# ---------------------------------------------------------------------------------------------- execute
New-Item -ItemType Directory -Force -Path $JDir | Out-Null
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$jpath = Join-Path $JDir "migration_$ts.json"
$envJ = [ordered]@{}
foreach ($k in $envs.Keys) { $envJ[$k] = @{ old = [Environment]::GetEnvironmentVariable($k, "User"); new = $envs[$k] } }
function Save-Journal { @{ when = $ts; root = $Root; base = $Base; moves = $moves; env = $envJ } | ConvertTo-Json -Depth 5 | Set-Content $jpath -Encoding UTF8 }
Save-Journal

foreach ($m in $moves) {
    if ($m.files -lt 0) { continue }
    $parent = Split-Path -Parent $m.dst
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    if ((Test-Path -LiteralPath $m.dst) -and (Get-Item -LiteralPath $m.dst).PSIsContainer) { Remove-Item -LiteralPath $m.dst -Force }  # empty
    try {
        Move-Item -LiteralPath $m.src -Destination $m.dst
    } catch {
        Save-Journal
        Write-Host "`nFAILED moving $($m.src): $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "Nothing else was moved after this. Fix the cause (open file?) and run -Execute again, or -Rollback." -ForegroundColor Red
        exit 1
    }
    $s = Get-Stats $m.dst
    if ($s.Files -ne $m.files -or $s.Bytes -ne $m.bytes) {
        Save-Journal
        throw "verification failed for $($m.dst): $($s.Files) files / $($s.Bytes) bytes, expected $($m.files) / $($m.bytes)"
    }
    $m.done = $true
    Save-Journal
    Write-Host ("  moved  {0,-60} {1,8} files  {2,9}" -f $m.dst, $m.files, (Fmt $m.bytes))
}
foreach ($d in "NSEDATAENGINE", "NSEMLENGINE", "NSEALGOENGINE", "NSEUIENGINE") {
    New-Item -ItemType Directory -Force -Path (Join-Path $Root $d) | Out-Null
}
foreach ($k in $envs.Keys) {
    [Environment]::SetEnvironmentVariable($k, $envs[$k], "User")
    Set-Item -Path "Env:$k" -Value $envs[$k]
}
Write-Host "`nEnvironment variables set." -ForegroundColor Green

# the one task whose command line contains a data path
$reg = Join-Path $Base "NSEUIENGINE\register_eod_tasks.ps1"
if (Test-Path $reg) { & $reg | Out-Null; Write-Host "Re-registered NSEUIENGINE-SignalLog (log now in $Root\NSEUIENGINE\logs)" }

# checks
Write-Host "`n== NSEDATAENGINE: python nse_contract.py paths"
Push-Location "$Base\NSEDATAENGINE"; & python nse_contract.py paths; Pop-Location
Write-Host "`n== NSEMLENGINE work folder"
$mlpy = if ($env:NSEMLENGINE_PYTHON) { $env:NSEMLENGINE_PYTHON } else { "python" }
& $mlpy -c "import sys; sys.path.insert(0, r'$Base\NSEMLENGINE'); import nseml.config as c; print(c.work_dir())"
$uipy = "$Base\NSEUIENGINE\.venv\Scripts\python.exe"
if (Test-Path $uipy) {
    Write-Host "`n== NSEUIENGINE"
    & $uipy -c "import sys; sys.path.insert(0, r'$Base\NSEUIENGINE'); import swing_trader as T; print('state  ', T.STATE_DIR); print('journal', T.JOURNAL_FILE); print('signals', T.signals_dir(T.load_config()))"
    & $uipy "$Base\NSEUIENGINE\fyers_token_manager.py" --status
}
Write-Host "`nDone. Journal: $jpath" -ForegroundColor Green
Write-Host "Open a NEW terminal / restart IDEs so they see the new variables. Undo: ...\move_to_shared_data.ps1 -Rollback"
