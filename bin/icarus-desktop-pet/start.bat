@echo off
REM ============================================================
REM  🪶 Icarus Desktop Pet — Detached Launcher
REM
REM  Starts the pet as a fully independent process.
REM  After window appears, you can close this terminal.
REM  The pet will keep running in the background.
REM
REM  Use stop.bat to quit it.
REM ============================================================

setlocal

set "SCRIPT_DIR=%~dp0"
for %%A in ("%SCRIPT_DIR%..") do set "HERMES_ROOT=%%~fA"
set "PY=%HERMES_ROOT%\portable-python\python.exe"
set "LAUNCHER=%SCRIPT_DIR%detached.py"
set "LOG=%HERMES_ROOT%\data\logs\icarus-pet.log"

if not exist "%PY%" (
    echo [icarus] ERROR: python not found at %PY%
    pause
    exit /b 1
)

if not exist "%LAUNCHER%" (
    echo [icarus] ERROR: detached.py not found at %LAUNCHER%
    pause
    exit /b 1
)

if not exist "%HERMES_ROOT%\data\logs" mkdir "%HERMES_ROOT%\data\logs"

echo.
echo ============================================================
echo   🪶 Icarus Desktop Pet - Detached Launcher
echo ============================================================
echo.
echo   Once the pet window appears, you can close this window.
echo   The pet will continue running in the system tray.
echo   Use stop.bat to quit it.
echo.
echo ============================================================
echo.

REM Run launcher in background (it spawns the real process and exits)
start "" /B "%PY%" "%LAUNCHER%"

REM Wait a moment then check
timeout /t 4 /nobreak >nul

echo [icarus] Pet launched (check task tray for the icon).
echo [icarus] Log: %LOG%
echo [icarus] Use stop.bat to exit.
echo.
exit /b 0
