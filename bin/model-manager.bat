@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "PY=%HERMES_ROOT%\portable-python\python.exe"
set "SCRIPT=%HERMES_ROOT%\hermes\scripts\model_manager.py"

if not exist "%SCRIPT%" (
    echo [ERROR] model_manager.py not found
    exit /b 1
)

set "CMD=list"
if "%~1" neq "" set "CMD=%~1"

"%PY%" "%SCRIPT%" %CMD% %2 %3 %4 %5

endlocal
exit /b 0