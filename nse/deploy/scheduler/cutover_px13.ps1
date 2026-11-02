[CmdletBinding()]
param(
    [string]$SourceRoot = "D:\NSE_BACKUP",
    [string]$TargetRoot = "D:\NSE",
    [switch]$InteractiveTasks,
    [switch]$SkipDataCopy,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$Timestamp  = Get-Date -Format "yyyyMMdd_HHmmss"
$TargetRoot = $TargetRoot.TrimEnd('\', '/')
$SourceRoot = $SourceRoot.TrimEnd('\', '/')
$CodeRoot   = Join-Path $TargetRoot "code"
$DataRoot   = Join-Path $TargetRoot "data"
$BackupDir  = Join-Path $DataRoot "deploy\scheduler\backups\$Timestamp"

# 1. Backup User Env Vars, Set NSE_ROOT, and Clear Legacy Env Overrides
$LegacyVars = @("NSE_DATA_ROOT","NSE_ALGO_HOME","NSEALGO_SRC","NSEALGOENGINE_HOME","NSEALGOENGINE_DATA_DIR","NSEDATAENGINE_HOME","NSEDATAENGINE_DATA_DIR","NSEDATAENGINE_FRESH_DIR","NSEDATAENGINE_REF_DIR","NSEDATAENGINE_EVENTS_DIR","NSEDATAENGINE_BACKTEST_DIR","NSEDATAENGINE_PORTFOLIO_DIR","NSEDATAENGINE_SIGNALS_DIR","NSEDATAENGINE_FRESH2019_DIR","NSEDATAENGINE_PREOPEN_DIR","NSEDATAENGINE_AT_CLOSE_DIR","NSEDATAENGINE_TBT_DIR","NSEDATAENGINE_MINUTE_DIR","NSEMLENGINE_HOME","NSEMLENGINE_WORK_DIR","NSEMLENGINE_CONFIG","NSEUIENGINE_HOME","NSEUIENGINE_DATA_DIR","FYERSTBT_DATA_DIR")
if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    Get-ItemProperty -Path "HKCU:\Environment" -ErrorAction SilentlyContinue | ConvertTo-Json -Depth 4 | Set-Content -Path (Join-Path $BackupDir "user_env_backup_$Timestamp.json") -Encoding UTF8
    [Environment]::SetEnvironmentVariable("NSE_ROOT", $TargetRoot, "User")
    [Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "User")
    $env:NSE_ROOT = $TargetRoot
    $env:PYTHONUTF8 = "1"
    foreach ($v in $LegacyVars) {
        [Environment]::SetEnvironmentVariable($v, $null, "User")
        Remove-Item "Env:\$v" -ErrorAction SilentlyContinue
    }
}


# Create single root .venv at $TargetRoot\.venv, install runtime_deps as sitecustomize.py, and remove nested engine venvs
$RootVenv = Join-Path $TargetRoot ".venv"
$RootPy   = Join-Path $RootVenv "Scripts\python.exe"
$RootPyw  = Join-Path $RootVenv "Scripts\pythonw.exe"
if (-not $DryRun) {
    if (-not (Test-Path $RootPy)) {
        Write-Host "[Creating Single Root .venv] $RootVenv ..." -ForegroundColor Cyan
        python -m venv $RootVenv
    }
    $SitePkgs = Join-Path $RootVenv "Lib\site-packages"
    if ((Test-Path $SitePkgs) -and (Test-Path (Join-Path $CodeRoot "runtime_deps.py"))) {
        Copy-Item -Path (Join-Path $CodeRoot "runtime_deps.py") -Destination (Join-Path $SitePkgs "sitecustomize.py") -Force
        Copy-Item -Path (Join-Path $CodeRoot "runtime_deps.py") -Destination (Join-Path $SitePkgs "runtime_deps.py") -Force
    }
    Get-ChildItem -Path $CodeRoot -Include ".venv","venv" -Directory -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "[Removing nested venv] $($_.FullName)" -ForegroundColor DarkGray
        Remove-Item -Path $_.FullName -Recurse -Force
    }
}

# 2. Copy Existing Data Files from SourceRoot into Mirrored DataRoot
function Copy-DataTree ([string[]]$Sources, [string]$DestRel) {
    $dst = Join-Path $DataRoot $DestRel
    foreach ($src in $Sources) {
        if ($src -and (Test-Path $src) -and ((Resolve-Path $src).Path.TrimEnd('\') -ine $dst.TrimEnd('\'))) {
            if ($DryRun) { Write-Host "[DryRun] Copy $src -> $dst" }
            else {
                New-Item -ItemType Directory -Force -Path $dst | Out-Null
                $null = & robocopy $src $dst /E /XO /R:2 /W:1 /MT:8 /NFL /NDL /NJH /NJS
                Write-Host "[Copied] $src -> $dst" -ForegroundColor Green
            }
        }
    }
}
if (-not $SkipDataCopy -and (Test-Path $SourceRoot)) {
    $S = $SourceRoot
    Copy-DataTree @("$S\NSEDATAENGINE\data","$S\DATA\NSEDATAENGINE\data","$S\shared\data") "NSEDATAENGINE\data"
    Copy-DataTree @("$S\NSEDATAENGINE\data_fresh","$S\DATA\NSEDATAENGINE\data_fresh") "NSEDATAENGINE\data_fresh"
    Copy-DataTree @("$S\NSEDATAENGINE\data_fresh_2019","$S\DATA\NSEDATAENGINE\data_fresh_2019") "NSEDATAENGINE\data_fresh_2019"
    Copy-DataTree @("$S\NSEDATAENGINE\data_ref","$S\DATA\NSEDATAENGINE\data_ref") "NSEDATAENGINE\data_ref"
    Copy-DataTree @("$S\NSEDATAENGINE\backtest_results","$S\DATA\NSEDATAENGINE\backtest_results") "NSEDATAENGINE\backtest_results"
    Copy-DataTree @("$S\NSEDATAENGINE\portfolio_results","$S\DATA\NSEDATAENGINE\portfolio_results") "NSEDATAENGINE\portfolio_results"
    Copy-DataTree @("$S\NSEDATAENGINE\signals","$S\DATA\NSEDATAENGINE\signals") "NSEDATAENGINE\signals"
    Copy-DataTree @("$S\NSEDATAENGINE\tbt","$S\DATA\NSEDATAENGINE\tbt") "NSEDATAENGINE\tbt"
    Copy-DataTree @("$S\NSEDATAENGINE\logs","$S\DATA\NSEDATAENGINE\logs") "NSEDATAENGINE\logs"
    foreach ($wf in @("work","work_alt","work_alt_close","work_alt_close_gpu","work_alt_gpu","work_alt_intraday","work_alt_intraday_gpu","work_alt_link","work_alt_link_gpu","work_orig_gpu")) {
        Copy-DataTree @("$S\NSEMLENGINE\$wf","$S\DATA\NSEMLENGINE\$wf","$S\DATA\NSEMLENGINE_$($wf.ToUpper())") "NSEMLENGINE\$wf"
    }
    Copy-DataTree @("$S\NSEALGOENGINE\data","$S\DATA\NSEALGOENGINE\data") "NSEALGOENGINE\data"
    Copy-DataTree @("$S\NSEALGOENGINE\runs","$S\DATA\NSEALGOENGINE\runs") "NSEALGOENGINE\runs"
    Copy-DataTree @("$S\NSEALGOENGINE\recordings","$S\DATA\NSEALGOENGINE\recordings") "NSEALGOENGINE\recordings"
    Copy-DataTree @("$S\NSEALGOENGINE\.cache","$S\NSEALGOENGINE\cache","$S\DATA\NSEALGOENGINE\cache") "NSEALGOENGINE\cache"
    Copy-DataTree @("$S\NSEALGOENGINE\logs","$S\DATA\NSEALGOENGINE\logs") "NSEALGOENGINE\logs"
    Copy-DataTree @("$S\NSEUIENGINE\state","$S\DATA\NSEUIENGINE\state") "NSEUIENGINE\state"
    Copy-DataTree @("$S\NSEUIENGINE\reports","$S\DATA\NSEUIENGINE\reports") "NSEUIENGINE\reports"
    Copy-DataTree @("$S\NSEUIENGINE\logs","$S\DATA\NSEUIENGINE\logs") "NSEUIENGINE\logs"
    Copy-DataTree @("$S\Fyerstbt\logs","$S\DATA\Fyerstbt\logs") "Fyerstbt\logs"
}

# 3. Programmatically Backup & Delete All Existing NSE Scheduled Tasks
$TaskXmlDir = Join-Path $BackupDir "tasks_xml"
if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $TaskXmlDir | Out-Null }
$ExistingTasks = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
    ($_.TaskPath -eq "\") -and (($_.TaskName -match "^(NSE|nsealgo|Fyers)") -or (($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments) $($_.WorkingDirectory)" }) -join " " -match "(NSEDATAENGINE|NSEMLENGINE|NSEALGOENGINE|NSEUIENGINE|Fyerstbt|nsealgo|fyers_token|swing_trader)"))
}
foreach ($t in $ExistingTasks) {
    $xmlOut = Join-Path $TaskXmlDir "$(($t.TaskName -replace '[\\/:*?""<>|]','_')).xml"
    if ($DryRun) { Write-Host "[DryRun] Backup & Delete Task: $($t.TaskName)" }
    else {
        Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath | Set-Content -Path $xmlOut -Encoding UTF8
        Unregister-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -Confirm:$false
        Write-Host "[Backed Up & Deleted] $($t.TaskName) -> $xmlOut" -ForegroundColor Yellow
    }
}

# 4. Recreate All Scheduled Tasks Fresh for New Mirrored Folder Structure
$User = "$env:USERDOMAIN\$env:USERNAME"
$Cred = if (-not $InteractiveTasks -and -not $DryRun) { Get-Credential -UserName $User -Message "Windows password for NSE Scheduled Tasks" } else { $null }
$Days = @("Monday","Tuesday","Wednesday","Thursday","Friday")
$DirData = Join-Path $CodeRoot "NSEDATAENGINE"; $DirMl = Join-Path $CodeRoot "NSEMLENGINE"; $DirAlgo = Join-Path $CodeRoot "NSEALGOENGINE"; $DirUi = Join-Path $CodeRoot "NSEUIENGINE"
$LogAlgo = Join-Path $DataRoot "NSEALGOENGINE\logs"; $LogUi = Join-Path $DataRoot "NSEUIENGINE\logs"

function New-NseTask ([string]$Name, [string]$Exe, [string]$ArgsStr, [string]$Cwd, [object[]]$Trig) {
    if ($DryRun) { Write-Host "[DryRun] Register Task: $Name -> $Exe $ArgsStr (cwd: $Cwd)"; return }
    $act = New-ScheduledTaskAction -Execute $Exe -Argument $ArgsStr -WorkingDirectory $Cwd
    $set = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 4) -MultipleInstances IgnoreNew
    if ($InteractiveTasks -or $null -eq $Cred) {
        $p = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $Name -Action $act -Trigger $Trig -Settings $set -Principal $p -Force | Out-Null
    } else {
        Register-ScheduledTask -TaskName $Name -Action $act -Trigger $Trig -Settings $set -User $Cred.UserName -Password $Cred.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null
    }
    Write-Host "[Created Fresh] $Name" -ForegroundColor Green
}

New-NseTask "NSEUIENGINE-FyersToken" "$RootPyw" "`"$DirUi\fyers_token_manager.py`"" $DirUi @((New-ScheduledTaskTrigger -Daily -At "08:00"),(New-ScheduledTaskTrigger -AtLogOn))
New-NseTask "nsealgo-forward" "cmd.exe" "/c `"set PYTHONUTF8=1 && set NSE_ROOT=$TargetRoot && `"$RootPy`" -m nsealgo forwardtest --daemon >> `"$LogAlgo\forward.log`" 2>&1`"" $DirAlgo @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "08:30"))
New-NseTask "NSEDATAENGINE-PreOpen" "cmd.exe" "/c `"set NSE_ROOT=$TargetRoot && `"$DirData\run_preopen.bat`"`"" $DirData @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "08:58"))
New-NseTask "NSEUIENGINE-Trader-Morning" "$RootPy" "`"$DirUi\swing_trader.py`" morning" $DirUi @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "08:58"),(New-ScheduledTaskTrigger -AtLogOn))
New-NseTask "NSEDATAENGINE-AtClose" "cmd.exe" "/c `"set NSE_ROOT=$TargetRoot && `"$DirData\run_at_close.bat`"`"" $DirData @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "14:57"))
New-NseTask "NSEMLENGINE-PredictClose" "cmd.exe" "/c `"set NSE_ROOT=$TargetRoot && `"$DirMl\run_predict_close.bat`"`"" $DirMl @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "15:31"))
New-NseTask "NSEMLENGINE-PredictCloseAlt" "cmd.exe" "/c `"set NSE_ROOT=$TargetRoot && `"$DirMl\run_predict_close_alt.bat`"`"" $DirMl @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "15:33"))
New-NseTask "NSEMLENGINE-PredictClose-alt_link" "cmd.exe" "/c `"set NSE_ROOT=$TargetRoot && `"$DirMl\run_model.bat`" alt_link predict-close`"" $DirMl @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "15:35"))
New-NseTask "NSEMLENGINE-PredictClose-alt_intraday" "cmd.exe" "/c `"set NSE_ROOT=$TargetRoot && `"$DirMl\run_predict_close_alt_intraday.bat`"`"" $DirMl @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "15:37"))
New-NseTask "NSEALGOENGINE-MinuteArchive" "cmd.exe" "/c `"set NSE_ROOT=$TargetRoot && `"$DirAlgo\run_minute_archive.cmd`"`"" $DirAlgo @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "19:05"))
New-NseTask "NSE data update" "cmd.exe" "/c `"set NSE_ROOT=$TargetRoot && `"$DirData\run_update.bat`"`"" $DirData @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "19:30"))
New-NseTask "NSEMLENGINE-Predict" "cmd.exe" "/c `"set NSE_ROOT=$TargetRoot && `"$DirMl\run_predict.bat`"`"" $DirMl @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "20:00"))
New-NseTask "NSEUIENGINE-Trader-Evening" "$RootPy" "`"$DirUi\swing_trader.py`" evening" $DirUi @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "20:30"))
New-NseTask "NSEMLENGINE-PredictAlt" "cmd.exe" "/c `"set NSE_ROOT=$TargetRoot && `"$DirMl\run_predict_alt.bat`"`"" $DirMl @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "20:40"))
New-NseTask "NSEMLENGINE-PredictAltClose" "cmd.exe" "/c `"set NSE_ROOT=$TargetRoot && `"$DirMl\run_predict_alt_close.bat`"`"" $DirMl @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "21:20"))
New-NseTask "NSEMLENGINE-Predict-alt_link" "cmd.exe" "/c `"set NSE_ROOT=$TargetRoot && `"$DirMl\run_model.bat`" alt_link predict`"" $DirMl @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "22:00"))
New-NseTask "NSEUIENGINE-SignalLog" "cmd.exe" "/c `"set PYTHONUTF8=1 && set NSE_ROOT=$TargetRoot && `"$RootPy`" `"$DirUi\signal_ml_tracker.py`" --wait-ml 120 >> `"$LogUi\signal_ml_tracker.log`" 2>&1 && `"$RootPy`" `"$DirUi\ml_accuracy.py`" >> `"$LogUi\signal_ml_tracker.log`" 2>&1`"" $DirUi @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At "22:00"))
New-NseTask "NSEUIENGINE-EdgeReport" "cmd.exe" "/c `"set PYTHONUTF8=1 && set NSE_ROOT=$TargetRoot && `"$RootPy`" `"$DirUi\edge_report.py`" >> `"$LogUi\edge_report.log`" 2>&1`"" $DirUi @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At "10:00"))
if (-not $DryRun -and (Test-Path "$DirAlgo\deploy\windows\register_minute_prefetch_task.ps1")) {
    & "$DirAlgo\deploy\windows\register_minute_prefetch_task.ps1" -Interactive:$InteractiveTasks
}
