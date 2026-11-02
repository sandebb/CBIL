<#
.SYNOPSIS
    OLD PC, switch-over day: disables every NSE scheduled task here (the new PC takes over), after saving each task
    as XML in <Root>\_move\old_pc_tasks_xml so it can be put back.

        powershell -ExecutionPolicy Bypass -File C:\temp\NSEDATAENGINE\migrate\disable_old_pc_tasks.ps1
        ... -Undo        enables them again (if the new PC is not ready after all)

    Run it BEFORE the new PC registers its tasks (setup_new_pc.ps1 -TasksOnly): two PCs must never both run the
    swing trader (real orders) or both download / forecast into the same day.
#>
[CmdletBinding()]
param([string]$Root = 'C:\temp', [switch]$Undo)
$ErrorActionPreference = 'Stop'
# -cmatch, anchored: plain -match 'NSE' also hits Windows' own tasks (LicENSE..., StorageSENSE, ConSENt...)
$tasks = @(Get-ScheduledTask | Where-Object { $_.TaskName -cmatch '^(NSE|nsealgo)' })
if (-not $tasks.Count) { Write-Host 'No NSE scheduled tasks on this PC.'; return }
if ($Undo) {
    $tasks | ForEach-Object { Enable-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath | Out-Null; Write-Host "  enabled  $($_.TaskName)" }
    return
}
$xml = Join-Path $Root '_move\old_pc_tasks_xml'
New-Item -ItemType Directory -Force -Path $xml | Out-Null
foreach ($t in $tasks) {
    Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath | Set-Content -Path (Join-Path $xml "$($t.TaskName).xml") -Encoding Unicode
    if ($t.State -eq 'Running') { Stop-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath }
    Disable-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath | Out-Null
    Write-Host "  disabled $($t.TaskName)"
}
Write-Host ("`n{0} task(s) disabled; copies in {1}. Undo: this script with -Undo" -f $tasks.Count, $xml) -ForegroundColor Green
