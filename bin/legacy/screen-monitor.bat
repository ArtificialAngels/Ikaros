@echo off
REM See docs/scripts/bin/screen-monitor.md

set "SCRIPT_DIR=%~dp0"
set "SCRIPT=%SCRIPT_DIR%screen-activity-monitor.ps1"

if "%1"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" status
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
)
