@echo off
cd /d "%~dp0"
if not "%NSE_ROOT%"=="" (set "WORK=%NSE_ROOT%\data\NSEMLENGINE\work_%~1") else (for %%I in ("%~dp0..\..\data\NSEMLENGINE\work_%~1") do set "WORK=%%~fI")
if not exist "%WORK%\logs" mkdir "%WORK%\logs"
set "PYTHONUTF8=1"
call "%~dp0nseml_model.cmd" %1 %2 >> "%WORK%\logs\scheduled_runs.txt" 2>&1