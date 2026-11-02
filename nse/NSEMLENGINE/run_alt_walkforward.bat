@echo off
REM Walk-forward test of the ALTERNATE models on the same 12 test quarters as the original model (phase1), one model
REM after another, then the fold-by-fold comparisons (blend = lgbm + deeplob average, what the live forecasts use).
REM   run_alt_walkforward.bat                  alt, alt_close and alt_link
REM   run_alt_walkforward.bat alt_link         only the model(s) named
REM   run_alt_walkforward.bat alt_intraday     ALT-INTRADAY = ALT + intraday inputs (nightly only; needs NSEDATAENGINE
REM                                            data_ref\intraday_daily.parquet from nse_intraday.py)
REM Each model: build (5-15 min; ALT-LINK's first build also draws its link map) + 12 folds of lgbm + deeplob
REM (about 3.5-4 h on the HP). Resumable: run it again and finished folds are skipped.
REM Progress: work_MODEL\results\phase1_MODEL\log.txt    Results: compare_*.txt in work_alt*\results\
REM Needs NSEDATAENGINE's short selling / MTF history first:  python nse_alt_data.py backfill --start 2019-01-01 --only short_selling,mtf
setlocal EnableDelayedExpansion
set "FAILED="
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PY=%NSEMLENGINE_PYTHON%"
if "%PY%"=="" set "PY=python"
set "MODELS=%*"
if "%MODELS%"=="" set "MODELS=alt alt_close alt_link"
for %%M in (%MODELS%) do (
  echo ==== %%M: inputs
  call "%~dp0nseml_model.cmd" %%M paths
  if errorlevel 1 (echo STOPPED: inputs of %%M are missing - see above & exit /b 1)
)
for %%M in (%MODELS%) do (
  echo.
  echo ==== %%M: dataset
  call "%~dp0nseml_model.cmd" %%M build
  if errorlevel 1 (
    echo FAILED: build of %%M - skipped, the other models still run; fix and rerun: run_alt_walkforward.bat %%M
    set "FAILED=!FAILED! %%M"
  ) else (
    echo ==== %%M: walk-forward, last 12 folds
    call "%~dp0nseml_model.cmd" %%M baselines --models lgbm,deeplob,blend --last 12 --name phase1_%%M
    if errorlevel 1 (
      echo FAILED: walk-forward of %%M - rerun run_alt_walkforward.bat %%M, finished folds are kept
      set "FAILED=!FAILED! %%M"
    )
  )
)
set "REF=work\results\phase1_pc"
if not exist "%REF%" set "REF=work\results\phase1"
echo.
echo ==== comparisons (model blend; A = reference, B = challenger)
if exist "work_alt\results\phase1_alt" "%PY%" compare_models.py --a %REF% --b work_alt\results\phase1_alt
if exist "work_alt_close\results\phase1_alt_close" "%PY%" compare_models.py --a %REF% --b work_alt_close\results\phase1_alt_close
if exist "work_alt_link\results\phase1_alt_link" "%PY%" compare_models.py --a %REF% --b work_alt_link\results\phase1_alt_link
if exist "work_alt_link\results\phase1_alt_link" if exist "work_alt_close\results\phase1_alt_close" "%PY%" compare_models.py --a work_alt_close\results\phase1_alt_close --b work_alt_link\results\phase1_alt_link
if exist "work_alt_intraday\results\phase1_alt_intraday" "%PY%" compare_models.py --a %REF% --b work_alt_intraday\results\phase1_alt_intraday
if exist "work_alt_intraday\results\phase1_alt_intraday" if exist "work_alt\results\phase1_alt" "%PY%" compare_models.py --a work_alt\results\phase1_alt --b work_alt_intraday\results\phase1_alt_intraday
if exist "work_alt_intraday_gpu\results\phase1_alt_intraday_gpu" "%PY%" compare_models.py --a %REF% --b work_alt_intraday_gpu\results\phase1_alt_intraday_gpu
if exist "work_alt_intraday_gpu\results\phase1_alt_intraday_gpu" if exist "work_alt\results\phase1_alt" "%PY%" compare_models.py --a work_alt\results\phase1_alt --b work_alt_intraday_gpu\results\phase1_alt_intraday_gpu
echo.
if not "!FAILED!"=="" echo NOT FINISHED:!FAILED! - see FAILED lines above
echo Done. Comparison files: compare_*.txt in work_alt\results, work_alt_close\results, work_alt_link\results and work_alt_intraday\results
