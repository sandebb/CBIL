<#
.SYNOPSIS
  Repairs the scheduled-task actions registered by cutover_cbil.ps1 (or cutover_px13.ps1) without
  touching triggers, principal, stored credentials, or task settings.

.DESCRIPTION
  BACKGROUND (01 Nov 2026): the cutover scripts registered every task with an action like

      cmd.exe /c "if "%NSE_ROOT%"=="" set "NSE_ROOT=D:\CBIL" && set ... && cd /d ... && <command>"

  In cmd.exe, an un-parenthesized IF owns EVERY &&/& chain that follows its condition - the whole
  remainder of the line becomes the IF body. Because NSE_ROOT is already set (user + machine scope),
  the condition was false, so EVERY task silently skipped its entire body and reported exit 0
  (verified empirically: reproduced in a .bat on the PC; all 19 tasks LastResult 0, all log dirs empty,
  Fyers token never refreshed after cutover).

  FIX: strip the broken `if "%NSE_ROOT%"=="" ` prefix from the action arguments, leaving a plain,
  unconditional chain:

      cmd.exe /c "set "NSE_ROOT=D:\CBIL" && set "PYTHONUTF8=1" && cd /d ... && <command>"

  (Verified working on the PC.) cutover_cbil.ps1's New-NseRootTask was fixed the same way for future
  registrations; this script repairs the tasks that already exist.

  The fix is idempotent: tasks whose arguments no longer contain the broken prefix are skipped.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\fix_task_actions.ps1 -DryRun
  powershell -NoProfile -ExecutionPolicy Bypass -File .\fix_task_actions.ps1

.NOTES
  Does not need the user's password: only the Action is replaced (Set-ScheduledTask), the principal
  (Password logon), triggers and settings are left as-is.
#>
[CmdletBinding()]
param(
    [string]$TargetRoot = "D:\CBIL",
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"

# The 19 tasks registered by cutover_cbil.ps1 (keep in sync with that script's New-NseRootTask calls)
$TaskNames = @(
    "NSEUIENGINE-FyersToken",
    "nsealgo-forward",
    "NSEDATAENGINE-PreOpen",
    "NSEUIENGINE-Trader-Morning",
    "NSEDATAENGINE-AtClose",
    "NSEMLENGINE-PredictClose",
    "NSEMLENGINE-PredictCloseAlt",
    "NSEMLENGINE-PredictClose-alt_link",
    "NSEMLENGINE-PredictClose-alt_intraday",
    "NSEALGOENGINE-MinuteArchive",
    "NSEALGOENGINE-MinutePrefetch",   # registered separately by register_minute_prefetch_task.ps1, same broken pattern
    "NSE data update",
    "NSEMLENGINE-Predict",
    "NSEUIENGINE-Trader-Evening",
    "NSEMLENGINE-PredictAlt",
    "NSEMLENGINE-PredictAltClose",
    "NSEMLENGINE-Predict-alt_link",
    "NSEUIENGINE-SignalLog",
    "NSEUIENGINE-EdgeReport"
)

$BrokenPrefix = 'if "%NSE_ROOT%"=="" '   # exact literal as registered by the old cutover template

$fixed = 0; $ok = 0; $missing = 0
foreach ($t in $TaskNames) {
    $task = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Warning "  [MISSING] $t - task not found"
        $missing++
        continue
    }
    $act = $task.Actions | Where-Object { $_.Execute -eq "cmd.exe" } | Select-Object -First 1
    if (-not $act) {
        Write-Warning "  [SKIP]    $t - first action is not cmd.exe (Execute='$($task.Actions[0].Execute)')"
        continue
    }
    $args0 = [string]$act.Arguments
    $idx = $args0.IndexOf($BrokenPrefix)
    if ($idx -lt 0) {
        Write-Host "  [OK]      $t - no broken IF prefix (already fixed)" -ForegroundColor DarkGray
        $ok++
        continue
    }
    # Sanity: the broken prefix must sit right after the opening quote of /c, otherwise do not touch it
    $before = $args0.Substring(0, $idx).TrimEnd()
    if ($before -notmatch '/c ?"$') {
        Write-Warning "  [SKIP]    $t - broken prefix not in expected position (before='$before'); fix by hand"
        continue
    }
    $newArgs = $args0.Remove($idx, $BrokenPrefix.Length)
    if ($DryRun) {
        Write-Host "  [DRYRUN]  $t"
        Write-Host "            old: $args0"
        Write-Host "            new: $newArgs"
        continue
    }
    $newAct = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $newArgs -WorkingDirectory $act.WorkingDirectory
    $null = Set-ScheduledTask -TaskName $t -Action $newAct
    Write-Host "  [FIXED]   $t" -ForegroundColor Green
    $fixed++
}

Write-Host ""
Write-Host "Summary: $fixed fixed, $ok already ok, $missing missing (of $($TaskNames.Count))"
if (-not $DryRun -and $fixed -gt 0) {
    Write-Host "Next: start one task to prove it really runs, e.g."
    Write-Host "  Start-ScheduledTask -TaskName NSEUIENGINE-EdgeReport"
    Write-Host "  Start-Sleep -Seconds 60; (Get-ScheduledTaskInfo NSEUIENGINE-EdgeReport).LastTaskResult"
    Write-Host "  and confirm $TargetRoot\data\NSEUIENGINE\logs\edge_report.log grew."
}
