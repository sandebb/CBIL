@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0set_static_ip.ps1"
echo.
pause
