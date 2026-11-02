@echo off
REM ALT-INTRADAY at-close forecast - scheduled task "NSEMLENGINE-PredictClose-alt_intraday", Mon-Fri 15:37.
REM Today's full-day intraday features are built from NSEALGOENGINE's 1-minute archive, which the intraday
REM prefetch (NSEALGOENGINE-MinutePrefetch, every 15 min) has been filling with today's bars all day. This task:
REM   1) NSEALGOENGINE  nsealgo prefetch-today --unattended --no-wait-idle
REM      (top-up: today's complete bars into partial_<date>.parquet; --no-wait-idle: the market is CLOSED at 15:37,
REM       resting overnight orders are the normal state and must not skip the forecast; the idle gate is for the
REM       intraday rounds only, to keep the Fyers data calls away from live order flow)
REM   2) NSEDATAENGINE  python nse_intraday.py daily          (build today's row -> data_ref\intraday_daily.parquet)
REM   3) NSEMLENGINE    nseml_model.cmd alt_intraday_gpu predict-close   (the 15:37 forecast)
REM Uses the day's Fyers token from NSEUIENGINE's 08:00 login. Log: work_alt_intraday_gpu\logs\scheduled_runs.txt
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not "%NSE_ROOT%"=="" (set "WORK=%NSE_ROOT%\data\NSEMLENGINE\work_alt_intraday_gpu") else (for %%I in ("%~dp0..\..\data\NSEMLENGINE\work_alt_intraday_gpu") do set "WORK=%%~fI")
if not exist "%WORK%\logs" mkdir "%WORK%\logs"
set "LOG=%WORK%\logs\scheduled_runs.txt"
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "TAG=%%d"
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "TODAY=%%d"
if exist "%WORK%\predictions\predictions_close_%TAG%.*" (echo [at-close] %TAG% forecast already exists - nothing to do >> "%LOG%" & exit /b 0)
if exist "%WORK%\predictions\predictions_close_%TODAY%.*" (echo [at-close] %TODAY% forecast already exists - nothing to do >> "%LOG%" & exit /b 0)

REM 1) NSEALGOENGINE prefetch (top-up) - NSEALGOENGINE's venv python
set "ALGO=%~dp0..\NSEALGOENGINE"
set "ALGOPY=%ALGO%\.venv\Scripts\python.exe"
if not exist "%ALGOPY%" set "ALGOPY=python"
pushd "%ALGO%"
"%ALGOPY%" -m nsealgo prefetch-today --unattended --no-wait-idle >> "%LOG%" 2>&1
set "RC1=%errorlevel%"
popd
echo [at-close] nsealgo prefetch-today exit %RC1% >> "%LOG%"

REM 2) NSEDATAENGINE intraday row - system python (NSEDATAENGINE has no venv)
set "DATA=%~dp0..\NSEDATAENGINE"
pushd "%DATA%"
python nse_intraday.py daily >> "%LOG%" 2>&1
set "RC2=%errorlevel%"
popd
echo [at-close] nse_intraday.py daily exit %RC2% >> "%LOG%"

REM 3) NSEMLENGINE at-close forecast (alt_intraday_gpu)
call "%~dp0nseml_model.cmd" alt_intraday_gpu predict-close >> "%LOG%" 2>&1
set "RC=%errorlevel%"
if %RC1% neq 0 set "RC=%RC1%"
if %RC2% neq 0 set "RC=%RC2%"
exit /b %RC%
