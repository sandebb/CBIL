@echo off
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not "%NSE_ROOT%"=="" (set "MLDATA=%NSE_ROOT%\data\NSEMLENGINE") else (for %%I in ("%~dp0..\..\data\NSEMLENGINE") do set "MLDATA=%%~fI")
if not exist "%MLDATA%\work_orig_gpu\logs" mkdir "%MLDATA%\work_orig_gpu\logs"
if not exist "%MLDATA%\work_alt_gpu\logs" mkdir "%MLDATA%\work_alt_gpu\logs"
if not exist "%MLDATA%\work_alt_close_gpu\logs" mkdir "%MLDATA%\work_alt_close_gpu\logs"
call "%~dp0nseml_model.cmd" orig_gpu predict --wait 0 >> "%MLDATA%\work_orig_gpu\logs\gpu_predict_run.txt" 2>&1
call "%~dp0nseml_model.cmd" alt_gpu predict --wait 0 >> "%MLDATA%\work_alt_gpu\logs\gpu_predict_run.txt" 2>&1
call "%~dp0nseml_model.cmd" alt_close_gpu predict --wait 0 >> "%MLDATA%\work_alt_close_gpu\logs\gpu_predict_run.txt" 2>&1