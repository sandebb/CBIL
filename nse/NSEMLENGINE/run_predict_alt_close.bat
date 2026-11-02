@echo off
REM ALT-CLOSE model, nightly (task NSEMLENGINE-PredictAltClose).
REM 29-Oct-2026: switched to the GPU model (ALT-CLOSE-GPU: config_alt_close_gpu.yaml, work_alt_close_gpu)
REM on the Radeon 8060S. Previous (CPU) version kept in run_predict_alt_close.bat.bak_pre_gpu.
REM Rebuilds its dataset, extends the delivery nowcast, retrains when due, forecasts, then writes
REM work_alt_close_gpu\predictions\close_check.txt (15:37 vs nightly + paper trade).
cd /d "%~dp0"
if not "%NSE_ROOT%"=="" (set "WORK=%NSE_ROOT%\data\NSEMLENGINE\work_alt_close_gpu") else (for %%I in ("%~dp0..\..\data\NSEMLENGINE\work_alt_close_gpu") do set "WORK=%%~fI")
if not exist "%WORK%\logs" mkdir "%WORK%\logs"
set "PYTHONUTF8=1"
call "%~dp0nseml_model.cmd" alt_close_gpu predict >> "%WORK%\logs\scheduled_runs.txt" 2>&1
exit /b %ERRORLEVEL%
