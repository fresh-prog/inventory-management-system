@echo off
rem Double-click to install or update the restaurant system on Windows (needs Docker Desktop).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\setup.ps1" %*
echo.
pause
