@echo off
setlocal
set "PY=%NSEMLENGINE_PYTHON%"
if "%PY%"=="" if not "%NSE_ROOT%"=="" set "PY=%NSE_ROOT%\.venv\Scripts\python.exe"
if "%PY%"=="" for %%I in ("%~dp0..\..\.venv\Scripts\python.exe") do if exist "%%~fI" set "PY=%%~fI"
if "%PY%"=="" set "PY=python"
pushd "%~dp0"
"%PY%" -m nseml %*
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%