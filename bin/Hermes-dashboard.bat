@echo off
REM ============================================================
REM Ikaros - Hermes Dashboard Launcher
REM ============================================================
REM  Starts the web dashboard (if not already running) and opens
REM  the browser at http://127.0.0.1:9119/.
REM ============================================================
REM  Known bugfix (2026-07-13): HERMES_SERVE_HEADLESS can leak
REM  from a previous Desktop spawn and cause mount_spa() to skip
REM  the SPA entirely at import time.  Explicitly unset it here.
REM ============================================================

REM ---- Load Ikaros environment ----
call "%~dp0..\Ikaros-environment\init.bat"
if errorlevel 1 (
    echo [FATAL] Ikaros-environment\init.bat failed.
    pause
    exit /b 1
)

REM ---- Purge leaking env vars (see bugfix note above) ----
set "HERMES_SERVE_HEADLESS="
set "HERMES_WEB_DIST="

REM ---- Quick check: is the dashboard already running? ----
"%IKAROS_PYTHON%" -c "import socket;s=socket.socket();s.settimeout(1);r=s.connect_ex(('127.0.0.1',9119));s.close();exit(0 if r==0 else 1)" >nul 2>&1
if not errorlevel 1 (
    echo Dashboard already running on :9119. Opening browser...
    start "" "http://127.0.0.1:9119"
    exit /b 0
)

REM ---- Start dashboard ----
echo Starting Hermes Dashboard on :9119...
echo   %IKAROS_HERMES_AGENT%\venv\Scripts\hermes.exe dashboard --port 9119 --no-open --skip-build

start "HermesDashboard" /MIN "%IKAROS_HERMES_AGENT%\venv\Scripts\hermes.exe" dashboard --port 9119 --no-open --skip-build >nul 2>&1

REM ---- Wait for ready ----
set "WAIT=0"
:wait_dashboard
"%IKAROS_PYTHON%" -c "import socket;s=socket.socket();s.settimeout(1);r=s.connect_ex(('127.0.0.1',9119));s.close();exit(0 if r==0 else 1)" >nul 2>&1
if not errorlevel 1 goto :dashboard_ready
ping -n 3 127.0.0.1 >nul
set /a WAIT+=2
if %WAIT% lss 30 goto :wait_dashboard
echo [WARN] Dashboard did not respond within %WAIT%s (may still be starting).
goto :after_dashboard

:dashboard_ready
echo Dashboard is ready: http://127.0.0.1:9119

:after_dashboard
REM ---- Open browser ----
start "" "http://127.0.0.1:9119"
echo Done.
exit /b 0
