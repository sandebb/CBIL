@echo off
REM Intraday 1-minute prefetch: fetch TODAY's bars (up to now) into <SYM>\partial_<date>.parquet, so the 15:37
REM ALT-INTRADAY forecast can run without waiting for the 19:05 full archive. Run by the scheduled task
REM "NSEALGOENGINE-MinutePrefetch" (Mon-Fri 09:20-15:35, every 15 min; register with
REM deploy\windows\register_minute_prefetch_task.ps1). Idle-gated: nsealgo waits for the live session to have
REM no open orders before calling the Fyers data API. Light: one small Fyers request per stock, idempotent.
REM Uses the day's Fyers token from NSEUIENGINE's 08:00 login. Log: runs\logs\minute_prefetch_task.txt
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PY=%NSE_ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" for %%I in ("%~dp0..\..\.venv\Scripts\python.exe") do if exist "%%~fI" set "PY=%%~fI"
if not exist "%PY%" set "PY=python"
set "LOGDIR=%~dp0..\..\data\NSEALGOENGINE\runs\logs"
if not "%NSE_ROOT%"=="" set "LOGDIR=%NSE_ROOT%\data\NSEALGOENGINE\runs\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
echo ==== %date% %time% minute prefetch >> "%LOGDIR%\minute_prefetch_task.txt"
"%PY%" -m nsealgo prefetch-today --unattended >> "%LOGDIR%\minute_prefetch_task.txt" 2>&1
set "RC=%errorlevel%"
echo ==== %date% %time% done (exit code %RC%) >> "%LOGDIR%\minute_prefetch_task.txt"
exit /b %RC%
