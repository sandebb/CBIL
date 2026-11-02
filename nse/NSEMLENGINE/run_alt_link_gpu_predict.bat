@echo off
REM One-off GPU test run for ALT-LINK-GPU (29-Oct-2026): retrain + predict on the Radeon 8060S,
REM unrolled-LSTM DeepLOB (see config_alt_link_gpu.yaml). Log: work_alt_link_gpu\logs\gpu_predict_run.txt
cd /d "%~dp0"
if not "%NSE_ROOT%"=="" (set "WORK=%NSE_ROOT%\data\NSEMLENGINE\work_alt_link_gpu") else (for %%I in ("%~dp0..\..\data\NSEMLENGINE\work_alt_link_gpu") do set "WORK=%%~fI")
call "%~dp0nseml_model.cmd" alt_link_gpu predict --retrain --force --wait 0 >> "%WORK%\logs\gpu_predict_run.txt" 2>&1
exit /b %ERRORLEVEL%
