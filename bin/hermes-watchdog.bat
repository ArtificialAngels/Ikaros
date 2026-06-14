@echo off
REM ============================================================
REM Hermes Watchdog launcher.
REM
REM Usually auto-launched (detached) by hermes-supervisor --start.
REM This .bat is for manual debugging:
REM   bin\hermes-watchdog.bat
REM ============================================================
setlocal
chcp 65001 >nul

REM ---- Single source of truth for HERMES_ROOT ----
call "%~dp0..\deps\hermes-env.bat"
if errorlevel 1 (
    echo [FATAL] could not resolve HERMES_ROOT.
    exit /b 1
)

set "WATCHDOG=%HERMES_BIN%\hermes-watchdog.py"

if not exist "%HERMES_PYTHON%" (
    echo [FATAL] portable-python not found: %HERMES_PYTHON%
    exit /b 2
)
if not exist "%WATCHDOG%" (
    echo [FATAL] watchdog not found: %WATCHDOG%
    exit /b 2
)

"%HERMES_PYTHON%" "%WATCHDOG%"
exit /b %ERRORLEVEL%
