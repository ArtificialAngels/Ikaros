@echo off
REM ============================================================
REM  🧠 Neuro Tray - Standalone system tray indicator
REM
REM  Shows Neuro status in notification area. Right-click for
REM  menu. Double-click to trigger PATIENCE (let her speak).
REM ============================================================

setlocal

set "SCRIPT_DIR=%~dp0"
for %%A in ("%SCRIPT_DIR%..") do set "HERMES_ROOT=%%~fA"
set "PY=%HERMES_ROOT%\portable-python\python.exe"
set "MAIN=%SCRIPT_DIR%neuro_tray.py"

if not exist "%PY%" (
    echo [neuro-tray] ERROR: python not found at %PY%
    pause
    exit /b 1
)

echo ============================================================
echo   🧠 Neuro Tray starting...
echo   Right-click tray icon for menu.
echo   Double-click to let Ikaros speak.
echo ============================================================

"%PY%" "%MAIN%"