<#
.SYNOPSIS
  Accurate PC clock for trading and latency measurement (asks for Administrator rights).
  Windows Time service: automatic start, time.google.com + time.cloudflare.com, sync every ~17 min, correct the
  clock at once instead of slowly. Then shows the offset (should be within about +/-0.01 s).
    powershell -ExecutionPolicy Bypass -File migrate\set_time_sync.ps1
#>
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host "Asking for Administrator rights (Windows time settings) ..."
    $p = Start-Process powershell -Verb RunAs -Wait -PassThru -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    exit $p.ExitCode
}
sc.exe config w32time start= auto | Out-Null
reg add HKLM\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\NtpClient /v SpecialPollInterval /t REG_DWORD /d 900 /f | Out-Null
reg add HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Config /v MaxAllowedPhaseOffset /t REG_DWORD /d 0 /f | Out-Null
net stop w32time 2>$null | Out-Null
net start w32time | Out-Null
w32tm /config /manualpeerlist:"time.google.com,0x8 time.cloudflare.com,0x8" /syncfromflags:manual /update | Out-Null
Start-Sleep -Seconds 3
w32tm /resync /force
w32tm /stripchart /computer:time.google.com /samples:3 /dataonly
Write-Host "Done. The offsets above should be close to +00.00s."
if ($Host.Name -eq "ConsoleHost") { Start-Sleep -Seconds 5 }
exit 0
