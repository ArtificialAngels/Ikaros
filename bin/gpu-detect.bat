@echo off
REM ============================================================
REM Hermes - GPU detection helper
REM
REM Phase 10: forwards to modules.env_bootstrap.gpu_detect which
REM supersedes the removed hermes/scripts/gpu_detector.py.
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

REM ---- Single source of truth: deps\hermes-env.bat ----
call "%~dp0..\deps\hermes-env.bat"
if errorlevel 1 (
    echo [FATAL] could not resolve HERMES_ROOT.
    exit /b 2
)

if not exist "%HERMES_PYTHON%" (
    echo [ERROR] python.exe not found at %HERMES_PYTHON%
    exit /b 2
)

"%HERMES_PYTHON%" -m modules.env_bootstrap.gpu_detect %*

endlocal
exit /b %ERRORLEVEL%