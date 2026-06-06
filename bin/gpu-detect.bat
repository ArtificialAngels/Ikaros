@echo off
REM ============================================================
REM Hermes - GPU 检测工具
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "PY=%HERMES_ROOT%\portable-python\python.exe"
set "SCRIPT=%HERMES_ROOT%\hermes\scripts\gpu_detector.py"

if not exist "%SCRIPT%" (
    echo [ERROR] gpu_detector.py not found
    exit /b 1
)

"%PY%" "%SCRIPT%" %*

endlocal
exit /b 0