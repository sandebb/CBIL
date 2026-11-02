param([string]$NseRoot = $env:NSE_ROOT)
if (-not $NseRoot) { $NseRoot = "D:\CBIL" }
$cXml = Export-ScheduledTask -TaskName "NSEMLENGINE-PredictClose-alt_intraday"
$cXml = $cXml.Replace("NSEMLENGINE-PredictClose-alt_intraday", "NSEMLENGINE-CloseChain")
$cXml = $cXml.Replace(("T15" + [char]58 + "37"), ("T15" + [char]58 + "31"))
$cXml = $cXml.Replace("run_predict_close_alt_intraday.bat", "run_close_chain.bat")
Register-ScheduledTask -TaskName "NSEMLENGINE-CloseChain" -Xml $cXml -Force | Out-Null
Write-Host "Registered NSEMLENGINE-CloseChain at 15:31 Mon-Fri"
