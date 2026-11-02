[CmdletBinding()]
param(
    [string]$SourceRoot = "D:\NSE",
    [string]$TargetRoot = "D:\CBIL",
    [string]$Branch     = "refactor/monorepo-cleanup",
    [switch]$InteractiveTasks,
    [switch]$SkipDataCopy,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$Timestamp  = Get-Date -Format "yyyyMMdd_HHmmss"
$SourceRoot = $SourceRoot.TrimEnd('\', '/')
$TargetRoot = $TargetRoot.TrimEnd('\', '/')
if ($SourceRoot -ieq $TargetRoot) { throw "SourceRoot and TargetRoot must differ so $SourceRoot remains untouched!" }

$CodeRoot  = Join-Path $TargetRoot "code"
$DataRoot  = Join-Path $TargetRoot "data"
$RootVenv  = Join-Path $TargetRoot ".venv"
$RootPy    = Join-Path $RootVenv "Scripts\python.exe"
$RootPyw   = Join-Path $RootVenv "Scripts\pythonw.exe"
$BackupDir = Join-Path $DataRoot "deploy\scheduler\backups\$Timestamp"

# 1. Deploy Refactored Code into D:\CBIL (Leaving D:\NSE Untouched)
$RepoUrl = (& git -C $SourceRoot remote get-url origin 2>$null).Trim()
if (-not $DryRun) {
    if (-not (Test-Path $TargetRoot)) { New-Item -ItemType Directory -Force -Path $TargetRoot | Out-Null }
    if (-not (Test-Path (Join-Path $TargetRoot ".git"))) {
        & git clone --branch $Branch --single-branch $RepoUrl $TargetRoot
    } else {
        & git -C $TargetRoot fetch origin $Branch
        & git -C $TargetRoot checkout -B $Branch "origin/$Branch"
        & git -C $TargetRoot reset --hard "origin/$Branch"
    }
}

# 2. Set NSE_ROOT = D:\CBIL, Clear Legacy Overrides & Setup Single Root .venv
$LegacyVars = @("NSE_DATA_ROOT","NSE_ALGO_HOME","NSEALGO_SRC","NSEALGOENGINE_HOME","NSEALGOENGINE_DATA_DIR","NSEDATAENGINE_HOME","NSEDATAENGINE_DATA_DIR","NSEDATAENGINE_FRESH_DIR","NSEDATAENGINE_REF_DIR","NSEDATAENGINE_EVENTS_DIR","NSEDATAENGINE_BACKTEST_DIR","NSEDATAENGINE_PORTFOLIO_DIR","NSEDATAENGINE_SIGNALS_DIR","NSEDATAENGINE_FRESH2019_DIR","NSEDATAENGINE_PREOPEN_DIR","NSEDATAENGINE_AT_CLOSE_DIR","NSEDATAENGINE_TBT_DIR","NSEDATAENGINE_MINUTE_DIR","NSEMLENGINE_HOME","NSEMLENGINE_WORK_DIR","NSEMLENGINE_CONFIG","NSEMLENGINE_PYTHON","NSEUIENGINE_HOME","NSEUIENGINE_DATA_DIR","FYERSTBT_DATA_DIR")
if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    Get-ItemProperty -Path "HKCU:\Environment" -ErrorAction SilentlyContinue | ConvertTo-Json -Depth 4 | Set-Content -Path (Join-Path $BackupDir "user_env_backup_$Timestamp.json") -Encoding UTF8
    [Environment]::SetEnvironmentVariable("NSE_ROOT", $TargetRoot, "User")
    [Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "User")
    $env:NSE_ROOT = $TargetRoot; $env:PYTHONUTF8 = "1"
    try { [Environment]::SetEnvironmentVariable("NSE_ROOT", $TargetRoot, "Machine"); [Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "Machine") } catch {}
    foreach ($v in $LegacyVars) {
        [Environment]::SetEnvironmentVariable($v, $null, "User")
        try { [Environment]::SetEnvironmentVariable($v, $null, "Machine") } catch {}
        Remove-Item "Env:\$v" -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path $RootPy)) { python -m venv $RootVenv }
    $SitePkgs = Join-Path $RootVenv "Lib\site-packages"
    if ((Test-Path $SitePkgs) -and (Test-Path (Join-Path $CodeRoot "runtime_deps.py"))) {
        Copy-Item -Path (Join-Path $CodeRoot "runtime_deps.py") -Destination (Join-Path $SitePkgs "runtime_deps.py") -Force
        Copy-Item -Path (Join-Path $CodeRoot "runtime_deps.py") -Destination (Join-Path $SitePkgs "sitecustomize.py") -Force
    }
    if (Test-Path (Join-Path $CodeRoot "NSEALGOENGINE\pyproject.toml")) {
        & $RootPy -m pip install --no-deps -e (Join-Path $CodeRoot "NSEALGOENGINE") --quiet
    }
}

# 3. Copy ONLY Data Files (*.parquet, *.db, *.csv, *.xls*, *.html, *.txt, *.json, *.bin) from D:\NSE to D:\CBIL\data (Skip All Logs & *.md)
$Patterns = @("*.parquet","*.db","*.sqlite","*.sqlite3","*.duckdb","*.csv","*.xls","*.xlsx","*.html","*.txt","*.json","*.bin","*.pt","*.pth","*.pkl")
$ExDirs   = @("logs","log",".git",".github",".venv","venv","__pycache__",".pytest_cache",".idea",".vscode","backup_pre_contract_20261003","_old")
$ExFiles  = @("*.md","*.markdown","*.log","*_log.txt","*_task.txt","*log*.txt","scheduled_runs.txt","gpu_predict_run.txt","WALKFORWARD_RUNNING.txt","unreferenced_parquet_files.txt","*.bak*","*.from_hp","*.lock","*.py","*.pyc","*.bat","*.cmd","*.ps1","*.sh","*.yaml","*.yml","*.toml")

function Copy-FilteredData ([string[]]$Sources, [string]$DestRel) {
    $dst = Join-Path $DataRoot $DestRel
    foreach ($src in $Sources) {
        if ($src -and (Test-Path $src) -and ((Resolve-Path $src).Path.TrimEnd('\') -ine $dst.TrimEnd('\'))) {
            if ($DryRun) { Write-Host "[DryRun] Copy filtered data $src -> $dst" }
            else {
                New-Item -ItemType Directory -Force -Path $dst | Out-Null
                $roboArgs = @($src, $dst) + $Patterns + @("/S","/XO","/R:2","/W:1","/MT:8","/NFL","/NDL","/NJH","/NJS","/XD") + $ExDirs + @("/XF") + $ExFiles
                $null = & robocopy @roboArgs
                Write-Host "[Copied Data] $src -> $dst" -ForegroundColor Green
            }
        }
    }
}
if (-not $SkipDataCopy -and (Test-Path $SourceRoot)) {
    $S = $SourceRoot
    Copy-FilteredData @("$S\NSEDATAENGINE\data","$S\DATA\NSEDATAENGINE\data","$S\data\NSEDATAENGINE\data","$S\shared\data","$S\shared\db") "NSEDATAENGINE\data"
    Copy-FilteredData @("$S\NSEDATAENGINE\data_fresh","$S\DATA\NSEDATAENGINE\data_fresh","$S\data\NSEDATAENGINE\data_fresh") "NSEDATAENGINE\data_fresh"
    Copy-FilteredData @("$S\NSEDATAENGINE\data_fresh_2019","$S\DATA\NSEDATAENGINE\data_fresh_2019","$S\data\NSEDATAENGINE\data_fresh_2019") "NSEDATAENGINE\data_fresh_2019"
    Copy-FilteredData @("$S\NSEDATAENGINE\data_ref","$S\DATA\NSEDATAENGINE\data_ref","$S\data\NSEDATAENGINE\data_ref") "NSEDATAENGINE\data_ref"
    Copy-FilteredData @("$S\NSEDATAENGINE\backtest_results","$S\DATA\NSEDATAENGINE\backtest_results","$S\data\NSEDATAENGINE\backtest_results") "NSEDATAENGINE\backtest_results"
    Copy-FilteredData @("$S\NSEDATAENGINE\portfolio_results","$S\DATA\NSEDATAENGINE\portfolio_results","$S\data\NSEDATAENGINE\portfolio_results") "NSEDATAENGINE\portfolio_results"
    Copy-FilteredData @("$S\NSEDATAENGINE\signals","$S\DATA\NSEDATAENGINE\signals","$S\data\NSEDATAENGINE\signals") "NSEDATAENGINE\signals"
    Copy-FilteredData @("$S\NSEDATAENGINE\tbt","$S\DATA\NSEDATAENGINE\tbt","$S\data\NSEDATAENGINE\tbt") "NSEDATAENGINE\tbt"
    foreach ($wf in @("work","work_alt","work_alt_close","work_alt_close_gpu","work_alt_gpu","work_alt_intraday","work_alt_intraday_gpu","work_alt_link","work_alt_link_gpu","work_orig_gpu")) {
        Copy-FilteredData @("$S\NSEMLENGINE\$wf","$S\DATA\NSEMLENGINE\$wf","$S\data\NSEMLENGINE\$wf","$S\DATA\NSEMLENGINE_$($wf.ToUpper())") "NSEMLENGINE\$wf"
    }
    Copy-FilteredData @("$S\NSEALGOENGINE\data","$S\DATA\NSEALGOENGINE\data","$S\data\NSEALGOENGINE\data") "NSEALGOENGINE\data"
    Copy-FilteredData @("$S\NSEALGOENGINE\runs","$S\DATA\NSEALGOENGINE\runs","$S\data\NSEALGOENGINE\runs") "NSEALGOENGINE\runs"
    Copy-FilteredData @("$S\NSEALGOENGINE\recordings","$S\DATA\NSEALGOENGINE\recordings","$S\data\NSEALGOENGINE\recordings") "NSEALGOENGINE\recordings"
    Copy-FilteredData @("$S\NSEALGOENGINE\.cache","$S\NSEALGOENGINE\cache","$S\DATA\NSEALGOENGINE\cache") "NSEALGOENGINE\cache"
    Copy-FilteredData @("$S\NSEUIENGINE\state","$S\DATA\NSEUIENGINE\state","$S\data\NSEUIENGINE\state") "NSEUIENGINE\state"
    Copy-FilteredData @("$S\NSEUIENGINE\reports","$S\DATA\NSEUIENGINE\reports","$S\data\NSEUIENGINE\reports") "NSEUIENGINE\reports"
    if (Test-Path "$S\Fyerstbt") {
        Get-ChildItem -Path "$S\Fyerstbt" -Directory -Filter "202*" -ErrorAction SilentlyContinue | ForEach-Object { Copy-FilteredData @($_.FullName) "Fyerstbt\ticks\$($_.Name)" }
    }
    foreach ($logSub in @("NSEDATAENGINE\data\logs","NSEDATAENGINE\logs","NSEMLENGINE\work\logs","NSEMLENGINE\work_alt\logs","NSEMLENGINE\logs","NSEALGOENGINE\runs\logs","NSEALGOENGINE\logs","NSEUIENGINE\logs","Fyerstbt\logs")) {
        if (-not $DryRun) { New-Item -ItemType Directory -Force -Path (Join-Path $DataRoot $logSub) | Out-Null }
    }
}

# 4. Programmatically Backup & Delete Existing NSE Scheduled Tasks
$TaskXmlDir = Join-Path $BackupDir "tasks_xml"
if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $TaskXmlDir | Out-Null }
$ExistingTasks = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
    ($_.TaskPath -eq "\") -and (($_.TaskName -match "^(NSE|nsealgo|Fyers)") -or (($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments) $($_.WorkingDirectory)" }) -join " " -match "(NSEDATAENGINE|NSEMLENGINE|NSEALGOENGINE|NSEUIENGINE|Fyerstbt|nsealgo|fyers_token|swing_trader)"))
}
foreach ($t in $ExistingTasks) {
    $xmlOut = Join-Path $TaskXmlDir "$(($t.TaskName -replace '[\\/:*?""<>|]','_')).xml"
    if (-not $DryRun) {
        Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath | Set-Content -Path $xmlOut -Encoding UTF8
        Unregister-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -Confirm:$false
        Write-Host "[Backed Up & Deleted] $($t.TaskName) -> $xmlOut" -ForegroundColor Yellow
    }
}

# 5. Recreate All 19 Scheduled Tasks Fresh Using %NSE_ROOT% Environment Variable
$User = "$env:USERDOMAIN\$env:USERNAME"
$Cred = if (-not $InteractiveTasks -and -not $DryRun) { Get-Credential -UserName $User -Message "Windows password for NSE Scheduled Tasks" } else { $null }
$Days = @("Monday","Tuesday","Wednesday","Thursday","Friday")

function New-NseRootTask ([string]$Name, [string]$EngineRel, [string]$InnerCmd, [object[]]$Trig) {
    $workDir = "%NSE_ROOT%\code\$EngineRel"
    # FIX 01 Nov 2026 (verified empirically on the PC): a cmd `if cond cmd1 && cmd2` puts the WHOLE
    # `&&` chain INSIDE the IF body - so when NSE_ROOT was already set (it is, user+machine scope) every
    # task silently skipped its entire body and reported exit 0. These tasks are tree-specific, so set
    # NSE_ROOT unconditionally instead of guarding it (no IF, no parser trap).
    $argStr  = "/c `"set `"NSE_ROOT=$TargetRoot`" && set `"PYTHONUTF8=1`" && cd /d `"%NSE_ROOT%\code\$EngineRel`" && $InnerCmd`""
    if ($DryRun) { Write-Host "[DryRun] Register Task: $Name -> cmd.exe $argStr (cwd: $workDir)"; return }
    $act = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $argStr -WorkingDirectory $workDir
    $set = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 4) -MultipleInstances IgnoreNew
    if ($InteractiveTasks -or $null -eq $Cred) {
        $p = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $Name -Action $act -Trigger $Trig -Settings $set -Principal $p -Force | Out-Null
    } else {
        Register-ScheduledTask -TaskName $Name -Action $act -Trigger $Trig -Settings $set -User $Cred.UserName -Password $Cred.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null
    }
    Write-Host "[Created Fresh via %NSE_ROOT%] $Name" -ForegroundColor Green
}

New-NseRootTask "NSEUIENGINE-FyersToken" "NSEUIENGINE" "`"%NSE_ROOT%\.venv\Scripts\pythonw.exe`" `"%NSE_ROOT%\code\NSEUIENGINE\fyers_token_manager.py`"" @((New-ScheduledTaskTrigger -Daily -At "08:00"),(New-ScheduledTaskTrigger -AtLogOn))
New-NseRootTask "nsealgo-forward" "NSEALGOENGINE" "`"%NSE_ROOT%\.venv\Scripts\python.exe`" -m nsealgo forwardtest --daemon >> `"%NSE_ROOT%\data\NSEALGOENGINE\logs\forward.log`" 2>&1" @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "08:30"))
New-NseRootTask "NSEDATAENGINE-PreOpen" "NSEDATAENGINE" "call `"%NSE_ROOT%\code\NSEDATAENGINE\run_preopen.bat`"" @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "08:58"))
New-NseRootTask "NSEUIENGINE-Trader-Morning" "NSEUIENGINE" "`"%NSE_ROOT%\.venv\Scripts\python.exe`" `"%NSE_ROOT%\code\NSEUIENGINE\swing_trader.py`" morning" @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "08:58"),(New-ScheduledTaskTrigger -AtLogOn))
New-NseRootTask "NSEDATAENGINE-AtClose" "NSEDATAENGINE" "call `"%NSE_ROOT%\code\NSEDATAENGINE\run_at_close.bat`"" @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "14:57"))
New-NseRootTask "NSEMLENGINE-PredictClose" "NSEMLENGINE" "call `"%NSE_ROOT%\code\NSEMLENGINE\run_predict_close.bat`"" @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "15:31"))
New-NseRootTask "NSEMLENGINE-PredictCloseAlt" "NSEMLENGINE" "call `"%NSE_ROOT%\code\NSEMLENGINE\run_predict_close_alt.bat`"" @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "15:33"))
New-NseRootTask "NSEMLENGINE-PredictClose-alt_link" "NSEMLENGINE" "call `"%NSE_ROOT%\code\NSEMLENGINE\run_model.bat`" alt_link predict-close" @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "15:35"))
New-NseRootTask "NSEMLENGINE-PredictClose-alt_intraday" "NSEMLENGINE" "call `"%NSE_ROOT%\code\NSEMLENGINE\run_predict_close_alt_intraday.bat`"" @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "15:37"))
New-NseRootTask "NSEALGOENGINE-MinuteArchive" "NSEALGOENGINE" "call `"%NSE_ROOT%\code\NSEALGOENGINE\run_minute_archive.cmd`"" @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "19:05"))
New-NseRootTask "NSE data update" "NSEDATAENGINE" "call `"%NSE_ROOT%\code\NSEDATAENGINE\run_update.bat`"" @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "19:30"))
New-NseRootTask "NSEMLENGINE-Predict" "NSEMLENGINE" "call `"%NSE_ROOT%\code\NSEMLENGINE\run_predict.bat`"" @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "20:00"))
New-NseRootTask "NSEUIENGINE-Trader-Evening" "NSEUIENGINE" "`"%NSE_ROOT%\.venv\Scripts\python.exe`" `"%NSE_ROOT%\code\NSEUIENGINE\swing_trader.py`" evening" @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "20:30"))
New-NseRootTask "NSEMLENGINE-PredictAlt" "NSEMLENGINE" "call `"%NSE_ROOT%\code\NSEMLENGINE\run_predict_alt.bat`"" @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "20:40"))
New-NseRootTask "NSEMLENGINE-PredictAltClose" "NSEMLENGINE" "call `"%NSE_ROOT%\code\NSEMLENGINE\run_predict_alt_close.bat`"" @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "21:20"))
New-NseRootTask "NSEMLENGINE-Predict-alt_link" "NSEMLENGINE" "call `"%NSE_ROOT%\code\NSEMLENGINE\run_model.bat`" alt_link predict" @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "22:00"))
New-NseRootTask "NSEUIENGINE-SignalLog" "NSEUIENGINE" "`"%NSE_ROOT%\.venv\Scripts\python.exe`" `"%NSE_ROOT%\code\NSEUIENGINE\signal_ml_tracker.py`" --wait-ml 120 >> `"%NSE_ROOT%\data\NSEUIENGINE\logs\signal_ml_tracker.log`" 2>&1 && `"%NSE_ROOT%\.venv\Scripts\python.exe`" `"%NSE_ROOT%\code\NSEUIENGINE\ml_accuracy.py`" >> `"%NSE_ROOT%\data\NSEUIENGINE\logs\signal_ml_tracker.log`" 2>&1" @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "22:00"))
New-NseRootTask "NSEUIENGINE-EdgeReport" "NSEUIENGINE" "`"%NSE_ROOT%\.venv\Scripts\python.exe`" `"%NSE_ROOT%\code\NSEUIENGINE\edge_report.py`" >> `"%NSE_ROOT%\data\NSEUIENGINE\logs\edge_report.log`" 2>&1" @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At "10:00"))
