@echo off
setlocal
set "PY=%NSEMLENGINE_PYTHON%"
if "%PY%"=="" if not "%NSE_ROOT%"=="" set "PY=%NSE_ROOT%\.venv\Scripts\python.exe"
if "%PY%"=="" for %%I in ("%~dp0..\..\.venv\Scripts\python.exe") do if exist "%%~fI" set "PY=%%~fI"
if "%PY%"=="" set "PY=python"
set "NSEMLENGINE_CONFIG=%~dp0config_alt.yaml"
set "NSEMLENGINE_WORK_DIR=%NSEMLENGINE_ALT_WORK_DIR%"
if "%NSEMLENGINE_WORK_DIR%"=="" if not "%NSE_ROOT%"=="" set "NSEMLENGINE_WORK_DIR=%NSE_ROOT%\data\NSEMLENGINE\work_alt"
if "%NSEMLENGINE_WORK_DIR%"=="" for %%I in ("%~dp0..\..\data\NSEMLENGINE\work_alt") do set "NSEMLENGINE_WORK_DIR=%%~fI"
pushd "%~dp0"
"%PY%" -m nseml %*
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%