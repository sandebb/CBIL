@echo off
set "PY=%NSE_ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" for %%I in ("%~dp0..\..\.venv\Scripts\python.exe") do if exist "%%~fI" set "PY=%%~fI"
if not exist "%PY%" set "PY=python"
REM Updates the NSE equity dataset: downloads every missing trading day, then rebuilds the feature file.
REM Safe to run daily, weekly or monthly - it only fetches what is missing.
REM %~dp0 = the folder this .bat file is in, so it works wherever the project is unpacked.
cd /d "%~dp0"
REM Data folder: %NSEDATAENGINE_DATA_DIR% (set by migrate\register_project.ps1 from contract\schema.yaml),
REM else the data folder next to this file.
set "DATADIR=%NSEDATAENGINE_DATA_DIR%"
if "%DATADIR%"=="" if not "%NSE_ROOT%"=="" set "DATADIR=%NSE_ROOT%\data\NSEDATAENGINE\data"
if "%DATADIR%"=="" for %%I in ("%~dp0..\..\data\NSEDATAENGINE\data") do set "DATADIR=%%~fI"
if not exist "%DATADIR%\logs" mkdir "%DATADIR%\logs"
"%PY%" nse_updater.py update >> "%DATADIR%\logs\scheduled_runs.txt" 2>&1
REM NSE sector of every stock (full refresh weekly, missing stocks daily): data_ref\sectors.parquet
"%PY%" nse_sectors.py >> "%DATADIR%\logs\scheduled_runs.txt" 2>&1
REM delivery %, bulk/block deals, insider trades, ASM/GSM lists, shareholding, pledge, index changes (data_ref)
"%PY%" nse_alt_data.py daily >> "%DATADIR%\logs\scheduled_runs.txt" 2>&1
REM intraday features from NSEALGOENGINE's 1-minute archive (task NSEALGOENGINE-MinuteArchive 19:05): data_ref\intraday_daily.parquet
"%PY%" nse_intraday.py daily >> "%DATADIR%\logs\scheduled_runs.txt" 2>&1
