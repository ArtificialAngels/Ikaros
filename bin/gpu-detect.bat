@echo off
REM ============================================================
REM Hermes - GPU detection helper
REM (forwards to modules.env_bootstrap.gpu_detect; see AGENTS.md §0.4 modules).
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