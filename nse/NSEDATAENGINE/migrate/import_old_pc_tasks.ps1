<#
.SYNOPSIS
    NEW PC (PX13): registers the NSE scheduled tasks from the copies saved on the old PC
    (<Root>\_move\old_pc_tasks_xml, written by disable_old_pc_tasks.ps1), with the old folder (C:\temp) moved to
    <Root> and your Windows account on this PC. Every task runs whether you are logged on or not (Password logon,
    Wake to run, run as soon as possible after a missed start) - the same settings that worked on the old PC.

        powershell -ExecutionPolicy Bypass -File D:\NSE\NSEDATAENGINE\migrate\import_old_pc_tasks.ps1

    Asks for your Windows password once (for a Microsoft account: the Microsoft account password, not the PIN).
    Tasks that already exist on this PC are kept unless you add -Replace.

    -Only NSEUIENGINE-FyersToken,NSEUIENGINE-Trader-Morning    only these tasks
    -Replace                                                    also replace tasks already registered here
    -DryRun                                                     show what would be registered, change nothing

    Log (full error text): %USERPROFILE%\AlgoSetup\import_tasks_<date>.log

    Why this exists: on the PX13 (23 Oct 2026) setup_new_pc.ps1 -TasksOnly could not register the tasks that
    use "run only when logged on" (NSEMLENGINE-*, NSEUIENGINE-*), while the password-logon ones worked. The old
    PC's own task definitions are known-good, so they are re-used here.
#>
[CmdletBinding()]
param(
    [string]$Root = 'D:\NSE',
    [string]$OldRoot = 'C:\temp',
    [string]$XmlDir,
    [string[]]$Only = @(),
    [switch]$Replace,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
if (-not $XmlDir) { $XmlDir = Join-Path $Root '_move\old_pc_tasks_xml' }
$logDir = Join-Path $env:USERPROFILE 'AlgoSetup'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ('import_tasks_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.log')
function Log([string]$m, [string]$c = 'Gray') {
    Write-Host $m -ForegroundColor $c
    Add-Content -Path $log -Value ("{0}  {1}" -f (Get-Date -Format s), $m)
}

Log "=== NSE scheduled tasks from the old PC -> $env:COMPUTERNAME" 'Cyan'
Log "log: $log"

# ---- clock: every task fires by this PC's clock -------------------------------------------------------
$tz = Get-TimeZone
Log ("clock now: {0}   time zone: {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $tz.Id)
if ($tz.Id -ne 'India Standard Time') {
    Log "WARNING: the time zone is not India Standard Time - the tasks would fire at the wrong time." 'Yellow'
    Log "         Fix: Settings > Time & language > Date & time > Time zone = (UTC+05:30) Chennai, Kolkata, Mumbai, New Delhi" 'Yellow'
}

# ---- which tasks ------------------------------------------------------------------------------------
if (-not (Test-Path $XmlDir)) { throw "not found: $XmlDir (copy C:\temp\_move from the old PC first)" }
# -cmatch (case-sensitive): plain -match 'NSE' also matches Windows' own tasks (LicENSE..., StorageSENSE, ConSENt...)
$files = @(Get-ChildItem -Path $XmlDir -Filter '*.xml' | Where-Object { $_.BaseName -cmatch '^(NSE|nsealgo)' } | Sort-Object BaseName)
if ($Only.Count) { $files = @($files | Where-Object { $Only -contains $_.BaseName }) }
if (-not $files.Count) { throw "no NSE task files in $XmlDir" }

$me = [Security.Principal.WindowsIdentity]::GetCurrent()
$sid = $me.User.Value
$account = $me.Name
Log "account: $account ($sid)"
$oldPat = [regex]::Escape($OldRoot.TrimEnd('\') + '\')
$newDir = $Root.TrimEnd('\') + '\'

# ---- build the new definitions first (nothing changes until the password is entered) -------------------
$plan = @()
foreach ($f in $files) {
    $name = $f.BaseName
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($existing -and -not $Replace) {
        Log ("  keep      {0,-38} already on this PC ({1}, {2}) - add -Replace to overwrite" -f $name, $existing.State, $existing.Principal.LogonType)
        continue
    }
    $xml = [IO.File]::ReadAllText($f.FullName)                              # UTF-16 with BOM
    $xml = $xml -ireplace $oldPat, $newDir                                  # C:\temp\... -> D:\NSE\...
    $xml = [regex]::Replace($xml, '(<Principal id="Author">\s*<UserId>)[^<]*', ('${1}' + $sid))
    $xml = [regex]::Replace($xml, '(<LogonTrigger>(?:(?!</LogonTrigger>).)*?<UserId>)[^<]*', ('${1}' + $account),
        [Text.RegularExpressions.RegexOptions]::Singleline)
    $xml = $xml -replace '<LogonType>[^<]*</LogonType>', '<LogonType>Password</LogonType>'
    # the files the task runs must exist on this PC
    $missing = @()
    foreach ($m in [regex]::Matches($xml, '[A-Za-z]:\\[^"<>|&]*?\.(exe|bat|cmd|py)\b')) {
        $p = $m.Value
        if ($p -like 'C:\Windows*') { continue }
        if (-not (Test-Path $p)) { $missing += $p }
    }
    if ($xml -match [regex]::Escape($OldRoot)) { $missing += "(still points at $OldRoot)" }
    $plan += [pscustomobject]@{ Name = $name; Xml = $xml; Missing = ($missing | Select-Object -Unique) }
    $tag = if ($existing) { 'replace' } else { 'add' }
    Log ("  {0,-9} {1}" -f $tag, $name)
    foreach ($x in $plan[-1].Missing) { Log "            WARNING file not found: $x" 'Yellow' }
}
if (-not $plan.Count) { Log "`nNothing to register." 'Green'; return }
if ($DryRun) { Log "`n-DryRun: nothing registered." 'Yellow'; return }

# ---- register ---------------------------------------------------------------------------------------
$cred = Get-Credential -UserName $account -Message "Windows password for the NSE scheduled tasks (they run while you are logged off)"
if (-not $cred) { throw 'no password entered - nothing registered' }
$pw = $cred.GetNetworkCredential().Password
$ok = 0; $bad = 0
foreach ($t in $plan) {
    try {
        Register-ScheduledTask -TaskName $t.Name -Xml $t.Xml -User $account -Password $pw -Force | Out-Null
        $s = (Get-ScheduledTask -TaskName $t.Name).Settings
        if (-not $s.WakeToRun -or -not $s.StartWhenAvailable) {
            $s.WakeToRun = $true; $s.StartWhenAvailable = $true
            Set-ScheduledTask -TaskName $t.Name -Settings $s -User $account -Password $pw | Out-Null
        }
        Log ("  OK    {0}" -f $t.Name) 'Green'; $ok++
    } catch {
        $bad++
        Log ("  FAIL  {0}: {1}" -f $t.Name, $_.Exception.Message) 'Red'
        Add-Content -Path $log -Value ("        " + ($_ | Out-String))
        if ($_.Exception.Message -match 'password|logon failure|user name') {
            Log "  Stopping: wrong Windows password (Microsoft account: use its password, not the PIN). Run again." 'Red'
            break
        }
    }
}
$pw = $null

# ---- summary ------------------------------------------------------------------------------------------
Log "`n$ok registered, $bad failed." $(if ($bad) { 'Red' } else { 'Green' })
$rows = Get-ScheduledTask | Where-Object { $_.TaskName -cmatch '^(NSE|nsealgo)' } | Sort-Object TaskName | ForEach-Object {
    $i = $_ | Get-ScheduledTaskInfo
    [pscustomobject]@{ Task = $_.TaskName; State = $_.State; Logon = $_.Principal.LogonType; NextRun = $i.NextRunTime }
}
$rows | Format-Table -AutoSize | Out-String -Width 160 | ForEach-Object { Log $_ }
Log "Fyers token now (after its credentials are fixed):  Start-ScheduledTask NSEUIENGINE-FyersToken"
if ($bad) { exit 1 }
