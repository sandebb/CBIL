@echo off
REM NSEMLENGINE ALT model nightly forecasts - scheduled task "NSEMLENGINE-PredictAlt" (register_alt_task.ps1).
REM 29-Oct-2026: switched to the GPU model (ALT-GPU: config_alt_gpu.yaml, work_alt_gpu) on the Radeon 8060S.
REM Previous (CPU) version kept in run_predict_alt.bat.bak_pre_gpu. -> work_alt_gpu\predictions\latest.csv
cd /d "%~dp0"
if not "%NSE_ROOT%"=="" (set "WORK=%NSE_ROOT%\data\NSEMLENGINE\work_alt_gpu") else (for %%I in ("%~dp0..\..\data\NSEMLENGINE\work_alt_gpu") do set "WORK=%%~fI")
if not exist "%WORK%\logs" mkdir "%WORK%\logs"
set "PYTHONUTF8=1"
call "%~dp0nseml_model.cmd" alt_gpu predict >> "%WORK%\logs\scheduled_runs.txt" 2>&1
exit /b %ERRORLEVEL%
