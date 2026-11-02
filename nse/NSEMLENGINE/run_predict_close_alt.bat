@echo off
REM ALT-CLOSE model at 15:33 (task NSEMLENGINE-PredictCloseAlt): waits for NSEDATAENGINE's 15:30 snapshot,
REM forecasts the next session with yesterday's delivery data + today's delivery nowcast.
REM 29-Oct-2026: switched to the GPU model (ALT-CLOSE-GPU: work_alt_close_gpu)
REM -> work_alt_close_gpu\predictions\latest_close.csv.
REM Previous (CPU) version kept in run_predict_close_alt.bat.bak_pre_gpu.
cd /d "%~dp0"
if not "%NSE_ROOT%"=="" (set "WORK=%NSE_ROOT%\data\NSEMLENGINE\work_alt_close_gpu") else (for %%I in ("%~dp0..\..\data\NSEMLENGINE\work_alt_close_gpu") do set "WORK=%%~fI")
if not exist "%WORK%\logs" mkdir "%WORK%\logs"
set "PYTHONUTF8=1"
call "%~dp0nseml_model.cmd" alt_close_gpu predict-close >> "%WORK%\logs\scheduled_runs.txt" 2>&1
exit /b %ERRORLEVEL%
