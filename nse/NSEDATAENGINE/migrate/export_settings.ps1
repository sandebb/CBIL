<#
.SYNOPSIS
    OLD PC: saves your user environment variables (broker logins, static-IP proxy, settings) and the list of NSE
    scheduled tasks into <Root>\_move, so robocopy carries them to the new PC and setup_new_pc.ps1 restores them.

        powershell -ExecutionPolicy Bypass -File C:\temp\NSEDATAENGINE\migrate\export_settings.ps1

    Saved: every USER variable except Path / TEMP / TMP / OneDrive and the daily Fyers token values (they expire).
    Values that point into the old folder (C:\temp\...) are moved to the new folder on restore.
    _move\user_env.json holds credentials (Shoonya, Fyers, proxy) in plain text - the same values that sit in your
    user variables today. Delete the _move folder on BOTH PCs once the new PC is set up.
#>
[CmdletBinding()]
param([string]$Root = 'C:\temp')
$ErrorActionPreference = 'Stop'
$out = Join-Path $Root '_move'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$skip = @('Path', 'TEMP', 'TMP', 'FYERS_ACCESS_TOKEN', 'FYERS_WS_TOKEN', 'FYERS_TOKEN_DATE', 'FYERS_TOKEN_ISSUED_AT')
$vars = [Environment]::GetEnvironmentVariables('User')
$saved = [ordered]@{ '_old_root' = $Root; '_old_pc' = $env:COMPUTERNAME; '_saved' = (Get-Date -Format s) }
foreach ($k in ($vars.Keys | Sort-Object)) {
    if ($skip -contains $k -or $k -like 'OneDrive*') { continue }
    $saved[$k] = [string]$vars[$k]
}
$f = Join-Path $out 'user_env.json'
ConvertTo-Json -InputObject $saved | Set-Content -Path $f -Encoding UTF8
Write-Host ("Saved {0} user variables -> {1}" -f ($saved.Count - 3), $f) -ForegroundColor Green
($saved.Keys | Where-Object { -not $_.StartsWith('_') }) | ForEach-Object { Write-Host "  $_" }

# -cmatch, anchored: plain -match 'NSE' also hits Windows' own tasks (LicENSE..., StorageSENSE, ConSENt...)
$tasks = Get-ScheduledTask | Where-Object { $_.TaskName -cmatch '^(NSE|nsealgo)' }
$rows = foreach ($t in $tasks) {
    $i = $t | Get-ScheduledTaskInfo
    [pscustomobject]@{ TaskName = $t.TaskName; State = $t.State; LastRun = $i.LastRunTime; LastResult = $i.LastTaskResult;
                       NextRun = $i.NextRunTime; Action = (($t.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join ' | ') }
}
$rows | Export-Csv -NoTypeInformation -Path (Join-Path $out 'old_pc_tasks.csv')
Write-Host ("`n{0} NSE scheduled tasks listed -> {1}" -f @($rows).Count, (Join-Path $out 'old_pc_tasks.csv')) -ForegroundColor Green
$rows | Format-Table TaskName, State, LastRun, LastResult -AutoSize
