@echo off
REM ============================================================
REM Hermes Doctor - diagnostic tool
REM
REM Inspired by ComfyUI-aki matsu.exe. Scans the project for
REM common issues and prints a report.
REM
REM Exit codes:
REM   0 = all OK
REM   1 = warnings
REM   2 = errors
REM
REM Usage:
REM   bin\hermes-doctor.bat
REM   bin\hermes-doctor.bat --json    (machine-readable output)
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "PY=%HERMES_ROOT%\portable-python\python.exe"
set "LOG=%HERMES_ROOT%\hermes\data\logs\doctor.log"

if not exist "%HERMES_ROOT%\hermes\data\logs" mkdir "%HERMES_ROOT%\hermes\data\logs" 2>nul

if not exist "%PY%" (
    echo [FAIL] Python not found: %PY%
    exit /b 2
)

echo === Hermes Doctor run at %DATE% %TIME% === >> "%LOG%"
"%PY%" -m hermes.doctor
set "RC=%ERRORLEVEL%"
echo exit=%RC% >> "%LOG%"
exit /b %RC%
