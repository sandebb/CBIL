@echo off
REM Daily top-up of the 1-minute archive (data\minute_archive): the newest session(s) for every stock, then the
REM liquidity table. Run by the scheduled task "NSEALGOENGINE-MinuteArchive" (Mon-Fri 19:05; register it with
REM deploy\windows\register_minute_archive_task.ps1). Uses the day's Fyers token from NSEUIENGINE's 08:00 login.
REM Log: runs\logs\minute_archive_task.txt
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PY=%NSE_ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" for %%I in ("%~dp0..\..\.venv\Scripts\python.exe") do if exist "%%~fI" set "PY=%%~fI"
if not exist "%PY%" set "PY=python"
set "LOGDIR=%~dp0..\..\data\NSEALGOENGINE\runs\logs"
if not "%NSE_ROOT%"=="" set "LOGDIR=%NSE_ROOT%\data\NSEALGOENGINE\runs\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
echo ==== %date% %time% minute archive >> "%LOGDIR%\minute_archive_task.txt"
"%PY%" -m nsealgo fetch-minute-archive --unattended >> "%LOGDIR%\minute_archive_task.txt" 2>&1
set "RC=%errorlevel%"
"%PY%" -m nsealgo liquidity-table >> "%LOGDIR%\minute_archive_task.txt" 2>&1
echo ==== %date% %time% done (fetch exit code %RC%) >> "%LOGDIR%\minute_archive_task.txt"
exit /b %RC%
