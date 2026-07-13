@echo off
REM 屏幕活动监控 — bat 快捷入口
REM 用法: screen-monitor [start|stop|status|report|log|clear]

set "SCRIPT_DIR=%~dp0"
set "SCRIPT=%SCRIPT_DIR%screen-activity-monitor.ps1"

if "%1"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" status
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
)
