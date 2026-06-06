@echo off
REM ============================================================
REM Hermes - Start Hermes API (FastAPI) with built-in Chat Pro
REM Serves the chat UI at /chat, API at /api/*, launcher at /launcher.
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "PY=%HERMES_ROOT%\portable-python\python.exe"

if not exist "%PY%" (
    echo [ERROR] Python not found: %PY%
    pause
    exit /b 1
)

cd /d "%HERMES_ROOT%"

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "HERMES_DATA_DIR=%HERMES_ROOT%\hermes\data"
set "HERMES_LLM_MOCK=1"
set "HERMES_EMBEDDER=hash"

set "PORT=%~1"
if "%PORT%"=="" set "PORT=7860"

echo ============================================================
echo   Hermes - API + Chat Pro
echo   URL: http://localhost:%PORT%
echo ============================================================
echo.

REM Start the Python web server in a new window
start "Hermes-Web" /MIN cmd /c ""%PY%" -m hermes serve --host 0.0.0.0 --port %PORT% 2>&1"

REM Wait for the web server to be ready (up to 60s)
echo Waiting for Hermes API to be ready...
set /a "WAITED=0"
:wait_web
timeout /t 2 /nobreak >nul
set /a "WAITED+=2"
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/healthz' -UseBasicParsing -TimeoutSec 2).StatusCode } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo   Hermes API ready in %WAITED%s
    echo   Try: curl http://localhost:%PORT%/api/status
    goto :open_browser
)
if %WAITED% GEQ 60 (
    echo   [WARN] Hermes API not responding after 60s
    pause
    exit /b 1
)
goto :wait_web

:open_browser
echo Opening http://localhost:%PORT%/status ...
start "" "http://localhost:%PORT%/status"

endlocal
