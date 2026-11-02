@echo off
set "PY=%NSE_ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" for %%I in ("%~dp0..\..\.venv\Scripts\python.exe") do if exist "%%~fI" set "PY=%%~fI"
if not exist "%PY%" set "PY=python"
REM Today's 15:30 bars for NSEMLENGINE's at-close forecast - scheduled task "NSEDATAENGINE-AtClose", Mon-Fri 14:57.
REM Snapshots at 15:00:00 and 15:30:15 (Fyers quotes) -> data\at_close\YYYY-MM-DD.parquet  Log: data\logs\at_close_log.txt
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "DATADIR=%NSEDATAENGINE_DATA_DIR%"
if "%DATADIR%"=="" if not "%NSE_ROOT%"=="" set "DATADIR=%NSE_ROOT%\data\NSEDATAENGINE\data"
if "%DATADIR%"=="" for %%I in ("%~dp0..\..\data\NSEDATAENGINE\data") do set "DATADIR=%%~fI"
if not exist "%DATADIR%\logs" mkdir "%DATADIR%\logs"
"%PY%" nse_close_snapshot.py run >> "%DATADIR%\logs\at_close_task.txt" 2>&1
