@echo off
REM NSEMLENGINE nightly forecasts - scheduled task "NSEMLENGINE-Predict" (register_tasks.ps1).
REM 29-Oct-2026: switched to the GPU model (ORIG-GPU: config_orig_gpu.yaml, work_orig_gpu) on the Radeon 8060S.
REM Previous (CPU) version kept in run_predict.bat.bak_pre_gpu. Waits for the NSEDATAENGINE 19:30 update,
REM retrains when due, writes work_orig_gpu\predictions\latest.csv.
cd /d "%~dp0"
if not "%NSE_ROOT%"=="" (set "WORK=%NSE_ROOT%\data\NSEMLENGINE\work_orig_gpu") else (for %%I in ("%~dp0..\..\data\NSEMLENGINE\work_orig_gpu") do set "WORK=%%~fI")
if not exist "%WORK%\logs" mkdir "%WORK%\logs"
set "PYTHONUTF8=1"
call "%~dp0nseml_model.cmd" orig_gpu predict >> "%WORK%\logs\scheduled_runs.txt" 2>&1
exit /b %ERRORLEVEL%
