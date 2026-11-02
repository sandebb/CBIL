<#
.SYNOPSIS
  Scheduled task "NSEALGOENGINE-MinutePrefetch": intraday 1-minute prefetch (today's bars -> partial_<date>.parquet).

    powershell -ExecutionPolicy Bypass -File deploy\windows\register_minute_prefetch_task.ps1

  Mon-Fri 09:20-15:35, every 15 min -> run_minute_prefetch.cmd -> nsealgo prefetch-today --unattended.
  Idle-gated: nsealgo polls the live session's monitor and waits for zero open orders before calling the Fyers
  data API (if there is no session it proceeds - nothing is being sent). Light + idempotent: one small Fyers
  request per stock; each round overwrites partial_<date>.parquet with more of the day's bars. The 19:05 full
  run (NSEALGOENGINE-MinuteArchive) folds these into the 90-day span and deletes them.
  Needs the day's Fyers token (NSEUIENGINE-FyersToken, 08:00). Wake to run is on. Runs whether logged on or not:
  asks for your Windows password once.
  -Interactive    register "only when logged on" instead (no password asked)
  -Start "09:20" -End "15:35" -Every 15

  NOTE: registered through schtasks + task XML because this PC's ScheduledTasks PowerShell module cannot set a
  trigger repetition (no RepetitionInterval property, nested pattern not constructible) - the XML path is the
  one that works, and it is verified after registration (Repetition + WakeToRun in the task XML).

  Check:  .venv\Scripts\python -m nsealgo minute-archive-status     Log: runs\logs\minute_prefetch_task.txt
  Remove: Unregister-ScheduledTask NSEALGOENGINE-MinutePrefetch -Confirm:$false
#>
param([string]$Start = "09:20", [string]$End = "15:35", [int]$Every = 15, [switch]$Interactive)
$ErrorActionPreference = "Stop"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$cmd = Join-Path $root "run_minute_prefetch.cmd"
if (-not (Test-Path $cmd)) { throw "not found: $cmd" }
$name = "NSEALGOENGINE-MinutePrefetch"

$startT = [datetime]::ParseExact($Start, "HH:mm", [globalization.CultureInfo]::InvariantCulture)
$endT   = [datetime]::ParseExact($End,   "HH:mm", [globalization.CultureInfo]::InvariantCulture)
if ($endT -le $startT) { throw "End ($End) must be after Start ($Start)" }
$everyT = $endT - $startT
$repDur = ("PT{0}H{1:00}M" -f $everyT.Hours, $everyT.Minutes)

# local timezone offset for StartBoundary (e.g. +05:30) - integer hours + minutes component (TimeSpan.Hours is an int,
# a [Math]::Abs of a double like 5.5 with a "00" format would format as 55)
$tzOff = [System.TimeZoneInfo]::Local.GetUtcOffset([datetime]::Now)
$sign = if ($tzOff.TotalHours -ge 0) { "+" } else { "-" }
$tzOff = "{0}{1:00}:{2:00}" -f $sign, [int][Math]::Floor([double]$tzOff.TotalHours), [int][Math]::Abs($tzOff.TotalMinutes % 60)
$startBoundary = "{0}T{1:00}:{2:00}:{3:00}{4}" -f [datetime]::Now.ToString("yyyy-MM-dd"), $startT.Hour, $startT.Minute, 0, $tzOff

$xml = @"
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>NSEALGOENGINE: intraday 1-minute prefetch (today's bars -&gt; partial_&lt;date&gt;.parquet), every $Every min $Start-$End</Description>
    <URI>\$name</URI>
  </RegistrationInfo>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <WakeToRun>true</WakeToRun>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$startBoundary</StartBoundary>
      <ScheduleByWeek>
        <WeeksInterval>1</WeeksInterval>
        <DaysOfWeek>
          <Monday />
          <Tuesday />
          <Wednesday />
          <Thursday />
          <Friday />
        </DaysOfWeek>
      </ScheduleByWeek>
      <Repetition>
        <Interval>PT${Every}M</Interval>
        <Duration>$repDur</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </CalendarTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>/c "$cmd"</Arguments>
      <WorkingDirectory>$root</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
$xmlFile = Join-Path $env:TEMP "bionic_${name}.xml"
[System.IO.File]::WriteAllText($xmlFile, $xml, [System.Text.UTF8Encoding]::new($false))

# re-register: drop any existing copy first (the module's -Force equivalent)
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
}

if ($Interactive) {
    $out = & schtasks /Create /XML $xmlFile /TN $name 2>&1
} else {
    $cred = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" -Message "Windows password for $name (runs while you are logged off)"
    $out = & schtasks /Create /XML $xmlFile /TN $name /RU $cred.UserName /RP $cred.GetNetworkCredential().Password 2>&1
}
foreach ($line in $out) { Write-Host $line }
if ($LASTEXITCODE -ne 0) { Remove-Item $xmlFile -ErrorAction SilentlyContinue; throw "schtasks /Create failed (exit $LASTEXITCODE)" }
Remove-Item $xmlFile -ErrorAction SilentlyContinue

# verify: the registered task XML must carry the repetition and wake-to-run
$check = (& schtasks /Query /TN $name /XML 2>&1) -join "`n"
foreach ($needle in ("<Repetition>", "<Interval>PT${Every}M</Interval>", "<WakeToRun>true</WakeToRun>")) {
    if ($check -notlike "*$needle*") { throw "registered, but $needle missing - check the task" }
}
$t = Get-ScheduledTask -TaskName $name
Write-Host ("Registered {0}: Mon-Fri {1}-{2} every {3} min, logon {4}, wake to run {5}" -f $name, $Start, $End, $Every, $t.Principal.LogonType, $t.Settings.WakeToRun) -ForegroundColor Green
Write-Host "Run it once now:  Start-ScheduledTask $name   (log: $root\runs\logs\minute_prefetch_task.txt)"
