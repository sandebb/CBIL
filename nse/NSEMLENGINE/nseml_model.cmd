@echo off
REM Run "python -m nseml <command>" for an ALTERNATE model:  nseml_model.cmd MODEL command [options]
REM   MODEL = the name in config_MODEL.yaml (alt, alt_close, alt_link, ...); its files live in work_MODEL\
REM   nseml_model.cmd alt_link build              (the first build also draws ALT-LINK's link map)
REM   nseml_model.cmd alt_link baselines --models lgbm,deeplob,blend --last 12 --name phase1_alt_link
REM   nseml_model.cmd alt_link predict         /   nseml_model.cmd alt_link predict-close   /   nseml_model.cmd alt_link link-map
REM The original model is run with nseml.cmd (config.yaml, work\) and is never touched by this.
setlocal
set "MODEL=%~1"
if "%MODEL%"=="" (echo usage: nseml_model.cmd MODEL command [options]   e.g. nseml_model.cmd alt_link build & exit /b 2)
if not exist "%~dp0config_%MODEL%.yaml" (echo config_%MODEL%.yaml not found in %~dp0 & exit /b 2)
set "PY=%NSE_ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" for %%I in ("%~dp0..\..\.venv\Scripts\python.exe") do if exist "%%~fI" set "PY=%%~fI"
if not exist "%PY%" set "PY=python"
set "NSEMLENGINE_CONFIG=%~dp0config_%MODEL%.yaml"
if not "%NSE_ROOT%"=="" (set "NSEMLENGINE_WORK_DIR=%NSE_ROOT%\data\NSEMLENGINE\work_%MODEL%") else (for %%I in ("%~dp0..\..\data\NSEMLENGINE\work_%MODEL%") do set "NSEMLENGINE_WORK_DIR=%%~fI")
REM everything after the model name exactly as typed (%%1..%%9 would split "lgbm,deeplob,blend" at the commas)
set "ARGS=%*"
call set "ARGS=%%ARGS:*%MODEL%=%%"
pushd "%~dp0"
"%PY%" -m nseml %ARGS%
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
