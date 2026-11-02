<#
.SYNOPSIS
    Stops Windows from switching the Wi-Fi card off to save power (asks for Administrator rights).
    For the trading PC (PX13): the 08:00 token, 08:58 pre-open / swing trader and the evening tasks need the
    network the moment they start, including right after the PC wakes from sleep.

        powershell -ExecutionPolicy Bypass -File D:\NSE\NSEDATAENGINE\migrate\keep_wifi_on.ps1

    For every Wi-Fi adapter:
      1. Device Manager > Power Management: untick "Allow the computer to turn off this device to save power"
         (NetAdapter power setting, PnPCapabilities = 24 in the registry, and the WMI switch behind the tick box)
      2. Power plan > Wireless Adapter Settings > Power Saving Mode = Maximum Performance (plugged in and battery)
      3. Modern Standby PCs: "Networking connectivity in Standby" = Enable, so Wi-Fi stays up in sleep and
         scheduled tasks that wake the PC find the network ready
    Then restarts the Wi-Fi adapter once (a few seconds offline) so it takes effect without a reboot, and shows
    the result. Safe to run again any time.

    -NoRestart      don't restart the adapter (the change then takes effect at the next reboot)
    -Register       also add the scheduled task "PX13-KeepWifiOn" (SYSTEM, at every startup) that re-applies this:
                    Wi-Fi driver updates from Windows Update reset the Power Management tick box
    -PluggedInOnly  change the power plan for plugged-in only (default: plugged in and on battery)

    Log: %ProgramData%\KeepWifiOn\keep_wifi_on.log
#>
[CmdletBinding()]
param(
    [switch]$NoRestart,
    [switch]$Register,
    [switch]$PluggedInOnly,
    [switch]$Quiet
)

# ---- Administrator ------------------------------------------------------------------------------------
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host "Asking for Administrator rights (device power settings) ..."
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    if ($NoRestart) { $a += '-NoRestart' }
    if ($Register) { $a += '-Register' }
    if ($PluggedInOnly) { $a += '-PluggedInOnly' }
    $p = Start-Process powershell -Verb RunAs -Wait -PassThru -ArgumentList $a
    exit $p.ExitCode
}

$ErrorActionPreference = 'Stop'
$logDir = Join-Path $env:ProgramData 'KeepWifiOn'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'keep_wifi_on.log'
function Log([string]$m, [string]$c = 'Gray') {
    if (-not $Quiet) { Write-Host $m -ForegroundColor $c }
    Add-Content -Path $log -Value ("{0}  {1}" -f (Get-Date -Format s), $m)
}
$problems = 0
function Try-Step([string]$what, [scriptblock]$do, [switch]$Optional) {
    try { & $do; Log "    ok    $what" 'Green' }
    catch {
        if ($Optional) { Log "    skip  $what : $($_.Exception.Message)" 'Yellow' }
        else { $script:problems++; Log "    FAIL  $what : $($_.Exception.Message)" 'Red' }
    }
}

Log "=== keep Wi-Fi on  ($env:COMPUTERNAME)" 'Cyan'

# ---- 1. the Wi-Fi adapters -----------------------------------------------------------------------------
# NdisPhysicalMedium 9 = Native 802.11 (Wi-Fi); Bluetooth PAN and virtual adapters are left alone
$wifi = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object {
        $_.NdisPhysicalMedium -eq 9 -or $_.PhysicalMediaType -match '802\.11' })
if (-not $wifi.Count) { Log "No Wi-Fi adapter found (Get-NetAdapter -Physical). Nothing to do for the card." 'Yellow' }

$classKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}'
foreach ($ad in $wifi) {
    Log "`n[1] $($ad.Name): $($ad.InterfaceDescription)  (status $($ad.Status))" 'Cyan'

    Try-Step 'NetAdapter power management: computer may not turn the device off' {
        Set-NetAdapterPowerManagement -Name $ad.Name -AllowComputerToTurnOffDevice Disabled -NoRestart
    } -Optional

    Try-Step 'registry PnPCapabilities = 24 (Power Management tick box off)' {
        $guid = $ad.InterfaceGuid
        $key = Get-ChildItem $classKey -ErrorAction SilentlyContinue | Where-Object {
            (Get-ItemProperty $_.PSPath -Name NetCfgInstanceId -ErrorAction SilentlyContinue).NetCfgInstanceId -eq $guid
        } | Select-Object -First 1
        if (-not $key) { throw "adapter $guid not found under the network class key" }
        Set-ItemProperty -Path $key.PSPath -Name PnPCapabilities -Value 24 -Type DWord
    }

    Try-Step 'WMI MSPower_DeviceEnable = off (the same tick box)' {
        $pnp = $ad.PnPDeviceID
        $w = @(Get-CimInstance -Namespace root\wmi -ClassName MSPower_DeviceEnable -ErrorAction SilentlyContinue |
                Where-Object { $_.InstanceName -like "$pnp*" })
        if (-not $w.Count) { Log "          (the driver does not offer this switch - the registry value above covers it)"; return }
        foreach ($i in $w) { if ($i.Enable) { Set-CimInstance -InputObject $i -Property @{ Enable = $false } } }
    } -Optional

    # driver "advanced" properties differ per vendor (MediaTek, Intel, Qualcomm): shown, not changed
    $adv = @(Get-NetAdapterAdvancedProperty -Name $ad.Name -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -match 'power|sav|sleep|MIMO|U-APSD|idle' })
    foreach ($x in $adv) { Log ("          driver setting  {0,-40} = {1}" -f $x.DisplayName, $x.DisplayValue) }
}

# ---- 2. power plan: Wireless Adapter Settings ----------------------------------------------------------
Log "`n[2] Power plan: Wireless Adapter Settings > Power Saving Mode = Maximum Performance" 'Cyan'
$subWifi = '19cbb8fa-5279-450e-9fac-8a3d5fedd0c1'; $setWifi = '12bbebe6-58d6-4636-95bb-3217ef867c1a'
Try-Step 'plugged in' {
    & powercfg /setacvalueindex SCHEME_CURRENT $subWifi $setWifi 0; if ($LASTEXITCODE) { throw "powercfg exit $LASTEXITCODE" }
}
if (-not $PluggedInOnly) {
    Try-Step 'on battery' {
        & powercfg /setdcvalueindex SCHEME_CURRENT $subWifi $setWifi 0; if ($LASTEXITCODE) { throw "powercfg exit $LASTEXITCODE" }
    }
}

# ---- 3. Modern Standby: keep the network up while asleep -------------------------------------------------
$standby = (& powercfg /a) -join "`n"
if ($standby -match 'S0 Low Power Idle') {
    Log "`n[3] Modern Standby PC: Networking connectivity in Standby = Enable" 'Cyan'
    $subNone = 'fea3413e-7e05-4911-9a71-700331f1c294'; $setNet = 'f15576e8-98b7-4186-b944-eafa664402d9'
    & powercfg -attributes $subNone $setNet -ATTRIB_HIDE 2>$null | Out-Null       # show it in Power Options too
    Try-Step 'plugged in' {
        & powercfg /setacvalueindex SCHEME_CURRENT $subNone $setNet 1; if ($LASTEXITCODE) { throw "powercfg exit $LASTEXITCODE" }
    }
    if (-not $PluggedInOnly) {
        Try-Step 'on battery' {
            & powercfg /setdcvalueindex SCHEME_CURRENT $subNone $setNet 1; if ($LASTEXITCODE) { throw "powercfg exit $LASTEXITCODE" }
        }
    }
} else {
    Log "`n[3] Not a Modern Standby PC - skipped (S3 sleep turns Wi-Fi off; Wake to run brings it back)" 'Cyan'
}
& powercfg /setactive SCHEME_CURRENT | Out-Null

# ---- 4. take effect now --------------------------------------------------------------------------------
if ($wifi.Count -and -not $NoRestart) {
    Log "`n[4] Restarting the Wi-Fi adapter (a few seconds offline) ..." 'Cyan'
    foreach ($ad in $wifi) { Try-Step "restart $($ad.Name)" { Restart-NetAdapter -Name $ad.Name -Confirm:$false } }
    Start-Sleep -Seconds 10
} elseif ($wifi.Count) {
    Log "`n[4] -NoRestart: the Power Management change applies at the next reboot" 'Yellow'
}

# ---- 5. scheduled task that re-applies it at startup -----------------------------------------------------
if ($Register) {
    Log "`n[5] Scheduled task PX13-KeepWifiOn (SYSTEM, at startup)" 'Cyan'
    Try-Step 'register' {
        $act = New-ScheduledTaskAction -Execute 'powershell.exe' `
            -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -NoRestart -Quiet"
        $trg = New-ScheduledTaskTrigger -AtStartup
        $prn = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
        $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
        Register-ScheduledTask -TaskName 'PX13-KeepWifiOn' -Action $act -Trigger $trg -Principal $prn -Settings $set `
            -Description 'Re-applies keep_wifi_on.ps1 (Wi-Fi power saving off) after driver updates' -Force | Out-Null
    }
}

# ---- result -----------------------------------------------------------------------------------------------
Log "`n=== Result" 'Cyan'
foreach ($ad in $wifi) {
    $pm = Get-NetAdapterPowerManagement -Name $ad.Name -ErrorAction SilentlyContinue
    $now = Get-NetAdapter -Name $ad.Name
    Log ("  {0}: status {1}, link {2}, computer may turn it off: {3}" -f $ad.Name, $now.Status, $now.LinkSpeed,
        $(if ($pm) { $pm.AllowComputerToTurnOffDevice } else { 'unknown' }))
}
$q = (& powercfg /query SCHEME_CURRENT $subWifi $setWifi) -join "`n"
if ($q -match 'Current AC Power Setting Index:\s*(0x[0-9a-f]+)') { Log "  Wi-Fi power saving (plugged in): $($Matches[1])  (0x00000000 = Maximum Performance)" }
if ($problems) { Log "$problems step(s) failed - see above / $log" 'Red'; exit 1 }
Log 'Done.' 'Green'
if (-not $Quiet -and $Host.Name -eq 'ConsoleHost') { Start-Sleep -Seconds 5 }
exit 0
