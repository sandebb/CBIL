@echo off
REM NSEMLENGINE at-close forecast - scheduled task "NSEMLENGINE-PredictClose", Mon-Fri 15:31.
REM 29-Oct-2026: switched to the GPU model (ORIG-GPU: work_orig_gpu). Waits (up to 10 min) for the
REM NSEDATAENGINE 15:30 snapshot, forecasts the next session with the newest GPU nightly models
REM -> work_orig_gpu\predictions\predictions_close_YYYYMMDD.csv + latest_close.csv.
REM Previous (CPU) version kept in run_predict_close.bat.bak_pre_gpu. Log: work_orig_gpu\logs\
cd /d "%~dp0"
if not "%NSE_ROOT%"=="" (set "WORK=%NSE_ROOT%\data\NSEMLENGINE\work_orig_gpu") else (for %%I in ("%~dp0..\..\data\NSEMLENGINE\work_orig_gpu") do set "WORK=%%~fI")
if not exist "%WORK%\logs" mkdir "%WORK%\logs"
set "PYTHONUTF8=1"
call "%~dp0nseml_model.cmd" orig_gpu predict-close >> "%WORK%\logs\scheduled_runs.txt" 2>&1
exit /b %ERRORLEVEL%
