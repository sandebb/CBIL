@echo off
cd /d "%~dp0"
set "PYTHONUTF8=1"
if "%NSE_ROOT%"=="" for %%I in ("%~dp0..\..") do set "NSE_ROOT=%%~fI"
set "PY=%NSE_ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "NSEALGO=%NSE_ROOT%\code\NSEALGOENGINE"
set "NSEDATA=%NSE_ROOT%\code\NSEDATAENGINE"
set "WORK=%NSE_ROOT%\data\NSEMLENGINE\work_alt_intraday_gpu"
if not exist "%WORK%\logs" mkdir "%WORK%\logs"
set "LOG=%WORK%\logs\close_chain.txt"
set "DEADLINE=%CLOSE_CHAIN_DEADLINE%"
if "%DEADLINE%"=="" set "DEADLINE=1545"
set "TODAY=%CLOSE_CHAIN_DAY%"
if "%TODAY%"=="" for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "TODAY=%%d"
set "ATCLOSE=%NSEDATAENGINE_AT_CLOSE_DIR%"
if "%ATCLOSE%"=="" if not "%NSEDATAENGINE_DATA_DIR%"=="" set "ATCLOSE=%NSEDATAENGINE_DATA_DIR%\at_close"
if "%ATCLOSE%"=="" set "ATCLOSE=%NSE_ROOT%\data\NSEDATAENGINE\data\at_close"
set "SNAP=%ATCLOSE%\%TODAY%.parquet"
echo [close-chain] %TODAY% start %TIME% waiting for %SNAP% (deadline %DEADLINE%) >> "%LOG%"
:wait_snap
if exist "%SNAP%" goto snap_ok
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format HHmm"') do set "NOW=%%t"
if %NOW% GEQ %DEADLINE% goto snap_missed
timeout /t 15 /nobreak >nul
goto wait_snap
:snap_ok
echo [close-chain] %TODAY% snapshot found at %TIME%, starting top-up >> "%LOG%"
pushd "%NSEALGO%"
"%PY%" -m nsealgo prefetch-today --unattended --no-wait-idle >> "%LOG%" 2>&1
set "RC1=%errorlevel%"
popd
echo [close-chain] prefetch-today exit %RC1% >> "%LOG%"
pushd "%NSEDATA%"
"%PY%" nse_intraday.py daily >> "%LOG%" 2>&1
set "RC2=%errorlevel%"
popd
echo [close-chain] nse_intraday.py daily exit %RC2% >> "%LOG%"
call "%~dp0nseml_model.cmd" alt_intraday_gpu predict-close >> "%LOG%" 2>&1
set "RC3=%errorlevel%"
echo [close-chain] predict-close exit %RC3% at %TIME% >> "%LOG%"
set "RC=%RC3%"
if %RC1% neq 0 set "RC=%RC1%"
if %RC2% neq 0 set "RC=%RC2%"
exit /b %RC%
:snap_missed
echo [close-chain] %TODAY% NO snapshot %SNAP% by %DEADLINE% - chain aborted >> "%LOG%"
exit /b 1
