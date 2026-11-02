<#
.SYNOPSIS
  Creates the complete set of 19 NSE scheduled tasks from scratch (works when NO tasks exist).
  Fixed action template - no cmd IF parser trap (01 Nov 2026).

.DESCRIPTION
  Each task action is:

      cmd.exe /c "set "NSE_ROOT=D:\CBIL" && set "PYTHONUTF8=1" && cd /d "%NSE_ROOT%\code\<ENGINE>" && <command>"

  NSE_ROOT is set UNCONDITIONALLY (the old cutover used `if "%NSE_ROOT%"=="" set ... && ...` which
  cmd.exe parses as "the whole && chain is the IF body" - so with NSE_ROOT set, every task was a
  silent no-op reporting exit 0. Fixed template verified empirically with a repro .bat on this PC.)

  Each task:
    - Password logon for the current user (one Windows credential dialog; tasks then run logged-off,
      WakeToRun, StartWhenAvailable, allowed on battery, IgnoreNew - same as the 31 Oct originals),
    - ExecutionTimeLimit 4 h (10 min for MinutePrefetch, as in the original XML backups),
    - triggers exactly as in the 31 Oct backups (data\deploy\scheduler\backups\*\tasks_xml).

  Idempotent: uses -Force, so rerunning updates the tasks instead of failing.
  Pre-flight: verifies every .bat/.cmd/.py referenced below exists before registering anything.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\create_tasks_fresh.ps1
#>
[CmdletBinding()]
param(
    [string]$TargetRoot = "D:\CBIL"
)
$ErrorActionPreference = "Stop"

$Days = @("Monday","Tuesday","Wednesday","Thursday","Friday")

# =================================================================================================================================
# The 19 tasks (name, engine folder under code\, the command after cd /d, triggers, time limit)
# Sources: cutover_cbil.ps1 definitions + NSEALGOENGINE-MinutePrefetch.xml backup (repeating trigger).
# =================================================================================================================================
function WeeklyAt { param([string[]]$Dow, [string]$At) return (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Dow -At $At) }
function LogOnTrig { return (New-ScheduledTaskTrigger -AtLogOn) }

# MinutePrefetch (repeating trigger): this Windows build lacks New-ScheduledTaskTriggerRepetition, so it is
# registered from the XML template below (exact repeating trigger from the 31 Oct backup, fixed action).
$PrefetchXml = Join-Path $PSScriptRoot "task_templates\NSEALGOENGINE-MinutePrefetch.xml"

$Tasks = @(
    @{ Name="NSEUIENGINE-FyersToken"; Engine="NSEUIENGINE"; LimitMin=240
       Cmd='"%NSE_ROOT%\.venv\Scripts\pythonw.exe" "%NSE_ROOT%\code\NSEUIENGINE\fyers_token_manager.py"'
       Files=@("code\NSEUIENGINE\fyers_token_manager.py")
       Triggers=@( (WeeklyAt @("Monday","Tuesday","Wednesday","Thursday","Friday") "08:00"), (LogOnTrig) ) }
    @{ Name="nsealgo-forward"; Engine="NSEALGOENGINE"; LimitMin=240
       Cmd='"%NSE_ROOT%\.venv\Scripts\python.exe" -m nsealgo forwardtest --daemon >> "%NSE_ROOT%\data\NSEALGOENGINE\logs\forward.log" 2>&1'
       Files=@()
       Triggers=@( (WeeklyAt $Days "08:30") ) }
    @{ Name="NSEDATAENGINE-PreOpen"; Engine="NSEDATAENGINE"; LimitMin=240
       Cmd='call "%NSE_ROOT%\code\NSEDATAENGINE\run_preopen.bat"'
       Files=@("code\NSEDATAENGINE\run_preopen.bat")
       Triggers=@( (WeeklyAt $Days "08:58") ) }
    @{ Name="NSEUIENGINE-Trader-Morning"; Engine="NSEUIENGINE"; LimitMin=240
       Cmd='"%NSE_ROOT%\.venv\Scripts\python.exe" "%NSE_ROOT%\code\NSEUIENGINE\swing_trader.py" morning'
       Files=@("code\NSEUIENGINE\swing_trader.py")
       Triggers=@( (WeeklyAt $Days "08:58"), (LogOnTrig) ) }
    @{ Name="NSEDATAENGINE-AtClose"; Engine="NSEDATAENGINE"; LimitMin=240
       Cmd='call "%NSE_ROOT%\code\NSEDATAENGINE\run_at_close.bat"'
       Files=@("code\NSEDATAENGINE\run_at_close.bat")
       Triggers=@( (WeeklyAt $Days "14:57") ) }
    @{ Name="NSEMLENGINE-PredictClose"; Engine="NSEMLENGINE"; LimitMin=240
       Cmd='call "%NSE_ROOT%\code\NSEMLENGINE\run_predict_close.bat"'
       Files=@("code\NSEMLENGINE\run_predict_close.bat")
       Triggers=@( (WeeklyAt $Days "15:31") ) }
    @{ Name="NSEMLENGINE-PredictCloseAlt"; Engine="NSEMLENGINE"; LimitMin=240
       Cmd='call "%NSE_ROOT%\code\NSEMLENGINE\run_predict_close_alt.bat"'
       Files=@("code\NSEMLENGINE\run_predict_close_alt.bat")
       Triggers=@( (WeeklyAt $Days "15:33") ) }
    @{ Name="NSEMLENGINE-PredictClose-alt_link"; Engine="NSEMLENGINE"; LimitMin=240
       Cmd='call "%NSE_ROOT%\code\NSEMLENGINE\run_model.bat" alt_link predict-close'
       Files=@("code\NSEMLENGINE\run_model.bat")
       Triggers=@( (WeeklyAt $Days "15:35") ) }
    @{ Name="NSEMLENGINE-PredictClose-alt_intraday"; Engine="NSEMLENGINE"; LimitMin=240
       Cmd='call "%NSE_ROOT%\code\NSEMLENGINE\run_predict_close_alt_intraday.bat"'
       Files=@("code\NSEMLENGINE\run_predict_close_alt_intraday.bat")
       Triggers=@( (WeeklyAt $Days "15:37") ) }
    @{ Name="NSEALGOENGINE-MinuteArchive"; Engine="NSEALGOENGINE"; LimitMin=240
       Cmd='call "%NSE_ROOT%\code\NSEALGOENGINE\run_minute_archive.cmd"'
       Files=@("code\NSEALGOENGINE\run_minute_archive.cmd")
       Triggers=@( (WeeklyAt $Days "19:05") ) }
    @{ Name="NSEALGOENGINE-MinutePrefetch"; Engine="NSEALGOENGINE"; LimitMin=10
       Cmd='call "%NSE_ROOT%\code\NSEALGOENGINE\run_minute_prefetch.cmd"'   # (informational; registered from XML)
       Files=@("code\NSEALGOENGINE\run_minute_prefetch.cmd")
       FromXml=$true }
    @{ Name="NSE data update"; Engine="NSEDATAENGINE"; LimitMin=240
       Cmd='call "%NSE_ROOT%\code\NSEDATAENGINE\run_update.bat"'
       Files=@("code\NSEDATAENGINE\run_update.bat")
       Triggers=@( (WeeklyAt $Days "19:30") ) }
    @{ Name="NSEMLENGINE-Predict"; Engine="NSEMLENGINE"; LimitMin=240
       Cmd='call "%NSE_ROOT%\code\NSEMLENGINE\run_predict.bat"'
       Files=@("code\NSEMLENGINE\run_predict.bat")
       Triggers=@( (WeeklyAt $Days "20:00") ) }
    @{ Name="NSEUIENGINE-Trader-Evening"; Engine="NSEUIENGINE"; LimitMin=240
       Cmd='"%NSE_ROOT%\.venv\Scripts\python.exe" "%NSE_ROOT%\code\NSEUIENGINE\swing_trader.py" evening'
       Files=@("code\NSEUIENGINE\swing_trader.py")
       Triggers=@( (WeeklyAt $Days "20:30") ) }
    @{ Name="NSEMLENGINE-PredictAlt"; Engine="NSEMLENGINE"; LimitMin=240
       Cmd='call "%NSE_ROOT%\code\NSEMLENGINE\run_predict_alt.bat"'
       Files=@("code\NSEMLENGINE\run_predict_alt.bat")
       Triggers=@( (WeeklyAt $Days "20:40") ) }
    @{ Name="NSEMLENGINE-PredictAltClose"; Engine="NSEMLENGINE"; LimitMin=240
       Cmd='call "%NSE_ROOT%\code\NSEMLENGINE\run_predict_alt_close.bat"'
       Files=@("code\NSEMLENGINE\run_predict_alt_close.bat")
       Triggers=@( (WeeklyAt $Days "21:20") ) }
    @{ Name="NSEMLENGINE-Predict-alt_link"; Engine="NSEMLENGINE"; LimitMin=240
       Cmd='call "%NSE_ROOT%\code\NSEMLENGINE\run_model.bat" alt_link predict'
       Files=@("code\NSEMLENGINE\run_model.bat")
       Triggers=@( (WeeklyAt $Days "22:00") ) }
    @{ Name="NSEUIENGINE-SignalLog"; Engine="NSEUIENGINE"; LimitMin=240
       Cmd='"%NSE_ROOT%\.venv\Scripts\python.exe" "%NSE_ROOT%\code\NSEUIENGINE\signal_ml_tracker.py" --wait-ml 120 >> "%NSE_ROOT%\data\NSEUIENGINE\logs\signal_ml_tracker.log" 2>&1 && "%NSE_ROOT%\.venv\Scripts\python.exe" "%NSE_ROOT%\code\NSEUIENGINE\ml_accuracy.py" >> "%NSE_ROOT%\data\NSEUIENGINE\logs\signal_ml_tracker.log" 2>&1'
       Files=@("code\NSEUIENGINE\signal_ml_tracker.py","code\NSEUIENGINE\ml_accuracy.py")
       Triggers=@( (WeeklyAt $Days "22:00") ) }
    @{ Name="NSEUIENGINE-EdgeReport"; Engine="NSEUIENGINE"; LimitMin=240
       Cmd='"%NSE_ROOT%\.venv\Scripts\python.exe" "%NSE_ROOT%\code\NSEUIENGINE\edge_report.py" >> "%NSE_ROOT%\data\NSEUIENGINE\logs\edge_report.log" 2>&1'
       Files=@("code\NSEUIENGINE\edge_report.py")
       Triggers=@( (New-ScheduledTaskTrigger -Weekly -DaysOfWeek "Saturday" -At "10:00") ) }
)

# =================================================================================================================================
# Pre-flight: everything the tasks call must exist (fail before touching the scheduler)
# =================================================================================================================================
$problems = 0
if (-not (Test-Path (Join-Path $TargetRoot ".venv\Scripts\python.exe"))) {
    Write-Warning "  MISSING: $TargetRoot\.venv\Scripts\python.exe (root venv)"
    $problems++
}
foreach ($t in $Tasks) {
    foreach ($f in $t.Files) {
        $p = Join-Path $TargetRoot $f
        if (-not (Test-Path $p)) { Write-Warning "  MISSING for $($t.Name): $p"; $problems++ }
    }
    if (-not (Test-Path (Join-Path $TargetRoot "code\$($t.Engine)"))) {
        Write-Warning "  MISSING engine folder: code\$($t.Engine)"; $problems++
    }
}
if ($problems -gt 0) {
    Write-Host ""
    Write-Host "Aborting: $problems missing file(s)/folder(s) - nothing was registered." -ForegroundColor Red
    exit 1
}

# =================================================================================================================================
# Ask for the password ONCE (Password logon => tasks run while logged off, wake the PC)
# =================================================================================================================================
Write-Host "About to create $($Tasks.Count) scheduled tasks under user '$($env:USERDOMAIN)\$($env:USERNAME)'."
Write-Host "A Windows password dialog will appear - enter the password ONCE. It is only used to register the tasks; it is not saved by this script."
Write-Host ""
$cred = Get-Credential -UserName $env:USERNAME -Message "Windows password (used once to create the NSE scheduled tasks with Password logon)."
$pw = $cred.GetNetworkCredential().Password

# =================================================================================================================================
# Create
# =================================================================================================================================
$ok = 0
foreach ($t in $Tasks) {
    if ($t.FromXml) {
        if (-not (Test-Path $PrefetchXml)) { Write-Warning "  [SKIP] $($t.Name) - XML template missing: $PrefetchXml"; continue }
        $xmlText = [System.IO.File]::ReadAllText($PrefetchXml)
        # NOTE: the -Xml parameter set does NOT accept -RunLevel (AmbiguousParameterSet) - the template XML's
        # principal already carries LogonType=Password; RunLevel defaults to Limited (as in the originals).
        $null = Register-ScheduledTask -TaskName $t.Name -Xml $xmlText `
            -User $cred.UserName -Password $pw -Force
        Write-Host "  [CREATED]  $($t.Name)  (XML template: Mon-Fri 09:20 + repeat 15 min until 15:35)" -ForegroundColor Green
        $ok++
        continue
    }
    $argStr = "/c `"set `"NSE_ROOT=$TargetRoot`" && set `"PYTHONUTF8=1`" && cd /d `"%NSE_ROOT%\code\$($t.Engine)`" && $($t.Cmd)`""
    $act = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $argStr -WorkingDirectory "%NSE_ROOT%\code\$($t.Engine)"
    $set = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
           -ExecutionTimeLimit ([TimeSpan]::FromMinutes($t.LimitMin)) -MultipleInstances IgnoreNew
    $null = Register-ScheduledTask -TaskName $t.Name -Action $act -Trigger $t.Triggers -Settings $set `
        -User $cred.UserName -Password $pw -RunLevel Limited -Force
    Write-Host "  [CREATED]  $($t.Name)" -ForegroundColor Green
    $ok++
}

Write-Host ""
Write-Host "DONE: $ok task(s) created."
Write-Host ""
Write-Host "VERIFY (proof that a task really runs now):"
Write-Host "  Start-ScheduledTask -TaskName NSEUIENGINE-EdgeReport"
Write-Host "  Start-Sleep -Seconds 90"
Write-Host "  (Get-ScheduledTaskInfo -TaskName NSEUIENGINE-EdgeReport).LastTaskResult"
Write-Host "  dir $TargetRoot\data\NSEUIENGINE\logs\"
Write-Host "And the one Monday depends on:"
Write-Host "  Start-ScheduledTask -TaskName NSEUIENGINE-FyersToken"
Write-Host "  (check data\NSEUIENGINE\state\fyers_token_status.json after ~60 s)"
