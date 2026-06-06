@echo off
REM ============================================================
REM Hermes Model Launcher - 图形化模型选择器
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0"
set "PY=%HERMES_ROOT%\portable-python\python.exe"
set "SCRIPT=%HERMES_ROOT%\hermes\scripts\model_launcher_gui.py"

if not exist "%SCRIPT%" (
    echo [ERROR] model_launcher_gui.py not found
    pause
    exit /b 1
)

echo 启动 Hermes Model Launcher...
"%PY%" "%SCRIPT%"

endlocal
exit /b 0