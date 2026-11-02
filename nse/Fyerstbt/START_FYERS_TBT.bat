@echo off
set "PY=%NSE_ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" for %%I in ("%~dp0..\..\.venv\Scripts\python.exe") do if exist "%%~fI" set "PY=%%~fI"
if not exist "%PY%" set "PY=python"
rem Double-click launcher only - all logic is in fyers_tbt_streamer.py. Nothing is asked for or saved.
"%PY%" "%~dp0fyers_tbt_streamer.py" %*
pause
