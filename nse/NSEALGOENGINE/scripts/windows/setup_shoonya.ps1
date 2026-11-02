<#
.SYNOPSIS
  Shoonya (Finvasia) as the order broker for nsealgo: credentials, SDK, static-IP check, smoke test, switch-on.

    powershell -ExecutionPolicy Bypass -File scripts\windows\setup_shoonya.ps1              # credentials + checks
    powershell -ExecutionPolicy Bypass -File scripts\windows\setup_shoonya.ps1 -TestOrder   # + a real test order
    powershell -ExecutionPolicy Bypass -File scripts\windows\setup_shoonya.ps1 -TestOrder -Enable

  Before you start (Shoonya website / Prism > API key):
    * generate the API key: you get a vendor code (usually your user id + "_U") and an API secret
    * register your static IP 151.245.182.58 (%STATIC_IP%) for API orders - SEBI rule; orders from any other IP fail
    * enable TOTP on the Shoonya account and keep the TOTP secret (the text behind the QR code)
  Steps
    1. installs the Shoonya SDK (NorenRestApiPy) into .venv
    2. asks for user id, password, TOTP secret, vendor code, API secret -> user environment variables SHOONYA_*
       (config\system.yaml reads them). Skipped with -SkipCredentials.
    3. nsealgo netcheck shoonya   : the order path leaves through the static IP, latency
    4. nsealgo smoke-test shoonya : login, instrument master, quote, history, order book, positions
       -TestOrder adds a 1-share CNC buy limit 10% below the market for SBIN, then cancels it (never fills)
    5. -Enable (only after 4 passed WITH -TestOrder): broker.brokers.shoonya.orders false -> true in system.yaml
  Until step 5, Shoonya stays dormant: `nsealgo live` refuses to route orders to it. Fyers stays the data feed.
#>
param([switch]$TestOrder, [switch]$Enable, [switch]$SkipCredentials)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $root
$py = ".\.venv\Scripts\python.exe"; $ns = ".\.venv\Scripts\nsealgo.exe"
if (-not (Test-Path $py)) { throw "no .venv - run scripts\windows\setup.ps1 first" }

Write-Host "== 1. Shoonya SDK"
& $py -m pip install --disable-pip-version-check -q -e ".[shoonya]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

function Clean([string]$v) { return (($v -replace '[\x00-\x1F\x7F]', '').Trim()) }
function Ask([string]$label, [string]$current, [string]$default = "") {
    $hint = if ($current) { " [Enter = keep current]" } elseif ($default) { " [Enter = $default]" } else { "" }
    $v = Clean (Read-Host "$label$hint")
    if (-not $v) { $v = if ($current) { $current } else { $default } }
    return $v
}
if (-not $SkipCredentials) {
    Write-Host "`n== 2. credentials (typed visibly so paste errors show; the window is not logged)"
    $cur = @{}
    foreach ($k in "SHOONYA_USER", "SHOONYA_PASSWORD", "SHOONYA_TOTP_SECRET", "SHOONYA_VENDOR_CODE", "SHOONYA_API_SECRET") {
        $cur[$k] = [Environment]::GetEnvironmentVariable($k, "User")
    }
    $u = (Ask "Shoonya user id (e.g. FA12345)" $cur.SHOONYA_USER).ToUpper()
    $p = Ask "Shoonya login password" $cur.SHOONYA_PASSWORD
    $t = (Ask "TOTP secret (letters A-Z and 2-7, NOT the 6-digit code)" $cur.SHOONYA_TOTP_SECRET) -replace '\s', ''
    $t = $t.ToUpper()
    if ($t -notmatch '^[A-Z2-7]{16,64}=*$') { throw "that is not a TOTP secret (base32, 16+ characters). The 6-digit code changes every 30 s and cannot be used." }
    $vc = Ask "Vendor code" $cur.SHOONYA_VENDOR_CODE "$($u)_U"
    $as = Ask "API secret (API key)" $cur.SHOONYA_API_SECRET
    if ($p.Length -lt 4 -or $as.Length -lt 8) { throw "password or API secret looks too short - nothing saved" }
    $code = & $py -c "import pyotp,sys; print(pyotp.TOTP(sys.argv[1]).now())" $t
    if ($LASTEXITCODE -ne 0) { throw "the TOTP secret is not valid base32" }
    Write-Host "   TOTP now: $code  (should match your authenticator app right now)"
    $vals = @{ SHOONYA_USER = $u; SHOONYA_PASSWORD = $p; SHOONYA_TOTP_SECRET = $t; SHOONYA_VENDOR_CODE = $vc; SHOONYA_API_SECRET = $as }
    foreach ($k in $vals.Keys) {
        [Environment]::SetEnvironmentVariable($k, $vals[$k], "User")
        Set-Item "env:$k" $vals[$k]
    }
    Write-Host "   saved SHOONYA_USER=$u, SHOONYA_VENDOR_CODE=$vc and the 3 secrets as user environment variables"
}
foreach ($k in "STATICIP_PROXY", "STATIC_IP") {
    if (-not (Get-Item "env:$k" -ErrorAction SilentlyContinue)) {
        $v = [Environment]::GetEnvironmentVariable($k, "User"); if ($v) { Set-Item "env:$k" $v }
    }
}
if (-not $env:STATIC_IP) { Write-Warning "STATIC_IP is not set - run %NSE_ROOT%\code\NSEUIENGINE\SET_STATIC_IP.cmd first" }

Write-Host "`n== 3. order path: static IP + latency"
& $ns netcheck shoonya
$netOk = ($LASTEXITCODE -eq 0)

Write-Host "`n== 4. smoke test against your real Shoonya account"
if ($TestOrder) { & $ns smoke-test shoonya --place-test-order } else { & $ns smoke-test shoonya }
$smokeOk = ($LASTEXITCODE -eq 0)

if ($Enable) {
    if (-not ($netOk -and $smokeOk -and $TestOrder)) {
        Write-Host "`nNOT enabled: -Enable needs netcheck and smoke-test -TestOrder to pass in the same run." -ForegroundColor Yellow
        exit 1
    }
    $cfgPath = Join-Path $root "config\system.yaml"
    $txt = Get-Content $cfgPath -Raw -Encoding UTF8
    $new = [regex]::Replace($txt, '(?s)(\n    shoonya:\s*\{.*?)orders: false\}', '${1}orders: true} ')
    if ($new -eq $txt) { Write-Host "shoonya is already enabled (or the line was edited by hand) - check config\system.yaml" }
    else {
        Copy-Item $cfgPath "$cfgPath.bak_pre_shoonya_enable" -Force
        [IO.File]::WriteAllText($cfgPath, $new, (New-Object Text.UTF8Encoding $false))
        Write-Host "`nENABLED: intraday and delivery orders now go to Shoonya (broker.brokers.shoonya.orders: true)." -ForegroundColor Green
    }
} elseif ($netOk -and $smokeOk) {
    Write-Host "`nAll checks passed. Next: run again with -TestOrder -Enable to switch Shoonya on for orders." -ForegroundColor Green
} else {
    Write-Host "`nSome checks failed - see FAIL lines above. Shoonya stays dormant." -ForegroundColor Yellow
}
