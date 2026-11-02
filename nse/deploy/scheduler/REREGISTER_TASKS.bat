@echo off
rem ============================================================
rem  One-time setup (01 Nov 2026): creates ALL 19 NSE scheduled
rem  tasks with the FIXED action (unconditional NSE_ROOT set).
rem  A Windows password dialog appears - enter your user password once.
rem  Works even when no tasks exist at all. Safe to rerun (-Force).
rem ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_tasks_fresh.ps1"
echo.
pause
