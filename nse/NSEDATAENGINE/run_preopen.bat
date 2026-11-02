@echo off
set "PY=%NSE_ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" for %%I in ("%~dp0..\..\.venv\Scripts\python.exe") do if exist "%%~fI" set "PY=%%~fI"
if not exist "%PY%" set "PY=python"
REM Records NSE's pre-open auction (09:00-09:15) - run by the scheduled task "NSEDATAENGINE-PreOpen", Mon-Fri 08:58.
REM Output: data\preopen\YYYY-MM-DD.parquet   Log: data\logs\preopen_log.txt   (see nse_preopen.py)
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "DATADIR=%NSEDATAENGINE_DATA_DIR%"
if "%DATADIR%"=="" if not "%NSE_ROOT%"=="" set "DATADIR=%NSE_ROOT%\data\NSEDATAENGINE\data"
if "%DATADIR%"=="" for %%I in ("%~dp0..\..\data\NSEDATAENGINE\data") do set "DATADIR=%%~fI"
if not exist "%DATADIR%\logs" mkdir "%DATADIR%\logs"
"%PY%" nse_preopen.py record >> "%DATADIR%\logs\preopen_task.txt" 2>&1
