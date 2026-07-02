@echo off
REM ============================================================
REM  Ikaros — One-click Launcher
REM ============================================================
REM  Starts all backend services via the Python supervisor
REM  (orchestrator), then launches the frontends.
REM
REM  Architecture (cloud-first):
REM    Supervisor reads modules/*/module.json, resolves the
REM    dependency graph (Kahn topo-sort), and starts each
REM    module in order with port health-checks.
REM
REM    :7860  bridge  (Rust — memory API, signals, module scan)
REM    :8080  llm_engine (local GGUF — disabled by default,
REM                       cloud models used unless local needed)
REM    Desktop      Hermes Desktop (Electron, primary frontend)
REM    Pet          Ikaros Desktop Pet (system tray, cloud chat)
REM
REM  All backend processes are fully detached; closing this
REM  window does NOT affect them.
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

REM ---- [env] Single source of truth: deps\hermes-env.bat ----
REM      Resolves HERMES_ROOT + 13 derived paths (HERMES_PYTHON,
REM      HERMES_BIN, HERMES_RUNTIME, etc.)
call "%~dp0..\deps\hermes-env.bat"
if errorlevel 1 (
    echo [FATAL] deps\hermes-env.bat failed to resolve HERMES_ROOT.
    echo         See stderr above for details.
    pause
    exit /b 1
)

echo ============================================================
echo   Ikaros — All-in-One Launcher
echo.
echo   Bridge:    http://127.0.0.1:7860  (memory + signals)
echo   LLM:       cloud-first  (local GGUF disabled by default)
echo   Frontend:  Hermes Desktop  (Electron)
echo   Pet:       Ikaros Desktop Pet  (cloud chat)
echo.
echo   Logs:      %HERMES_ROOT%\data\logs\
echo   Stop:      bin\ikaros-sleep.bat
echo ============================================================
echo.

REM ---- Step 0a: CRLF sanity check (warn only) ----
REM      cmd.exe mis-parses LF-only .bat files (paths with spaces
REM      get truncated). Fix: portable-python\python.exe bin\fix-eol.py --all
echo [0a] Checking bat / ps1 line endings...
"%HERMES_PYTHON%" "%HERMES_BIN%\fix-eol.py" --check >nul 2>&1
if errorlevel 1 (
    echo [WARN] Some bat / ps1 files have wrong line endings.
    echo [WARN] Run: portable-python\python.exe bin\fix-eol.py --all
    echo [WARN] Continuing anyway — failures may surface later.
)

REM ---- Step 0b: Environment bootstrap (portable python / runtime) ----
echo [0b] Checking portable environment...
call "%HERMES_ROOT%\bin\setup-portable.bat" auto >nul
if errorlevel 1 (
    echo [WARN] setup-portable had warnings — continuing anyway.
    echo [WARN] Re-run bin\setup-portable.bat later to retry.
)

REM ---- Step 1: Stop any stale instances ----
echo [1]  Stopping old instances ^(if any^)...
call "%HERMES_ROOT%\bin\ikaros-sleep.bat" >nul 2>&1
timeout /t 2 /nobreak >nul

REM ---- Step 2: Verify HERMES_ROOT integrity ----
echo [2]  Verifying HERMES_ROOT...
call "%HERMES_BIN%\hermes-root.bat" verify
if errorlevel 1 (
    echo.
    echo [FATAL] HERMES_ROOT verify FAILED.
    echo         The supervisor was NOT started.
    echo         See the error from `hermes-root.bat verify` above.
    pause
    exit /b 3
)

REM ---- Step 3: Start all services via the supervisor (orchestrator) ----
REM      hermes-supervisor.py scans modules/*/module.json, resolves
echo [3]  Starting services via supervisor ^(orchestrator^)...
REM      the dependency graph via Kahn topo-sort, and starts each
REM      module in order with TCP port health-checks.
call "%HERMES_ROOT%\bin\hermes-supervisor.bat" --start
if errorlevel 1 (
    echo.
    echo [ERROR] Supervisor failed to start one or more services.
    echo [hint]  Check: data\logs\supervisor-state.json
    echo [hint]  Check: data\logs\*.err  (per-module error logs)
    echo [hint]  Run:   bin\hermes-supervisor.bat --status
    pause
    exit /b 1
)

REM ---- Step 4: Launch Hermes Desktop (primary frontend) ----
REM      Wait for bridge HTTP health before launching Desktop,
REM      otherwise Desktop's reconnection loop leaks memory → OOM.
echo.
echo [4]  Waiting for bridge HTTP health...
set "_BRIDGE_OK=0"
for /L %%i in (1,1,30) do (
    if "!_BRIDGE_OK!"=="0" (
        "%HERMES_PYTHON%" -c "import http.client;c=http.client.HTTPConnection('127.0.0.1',7860,timeout=3);c.request('GET','/health');exit(0 if c.getresponse().status==200 else 1)" >nul 2>&1
        if not errorlevel 1 set "_BRIDGE_OK=1"
        if "!_BRIDGE_OK!"=="0" timeout /t 1 /nobreak >nul
    )
)
if "%_BRIDGE_OK%"=="0" (
    echo [desktop] WARN: Bridge :7860 not responding to HTTP after 30s.
    echo [desktop]       Desktop may show connection errors on first chat.
    echo [desktop]       Continuing anyway...
)
echo [4]  Launching Hermes Desktop...
call "%HERMES_BIN%\hermes-desktop.bat"
if errorlevel 1 (
    echo [desktop] WARN: Hermes Desktop failed to start.
    echo [desktop] See:   data\logs\desktop.log
) else (
    echo [desktop] OK
)

REM ---- Step 5: Launch Desktop Pet (non-critical, cloud chat) ----
echo.
echo [5]  Launching Desktop Pet...
start "" /B cmd /c ""%HERMES_BIN%\hermes-pet.bat" start" >nul 2>&1
echo [pet]    OK

REM ---- Done ----
echo.
echo ============================================================
echo   Ikaros is ready!
echo.
echo   Bridge:    http://127.0.0.1:7860/health
echo   Frontend:  Hermes Desktop  (Electron window)
echo   Pet:       Ikaros Desktop Pet  (system tray)
echo   LLM:       cloud  ^(MiniMax-M3 default, auto-flip if local needed^)
echo.
echo   Logs:      %HERMES_ROOT%\data\logs\
echo   Stop:      bin\ikaros-sleep.bat
echo   Status:    bin\hermes-supervisor.bat --status
echo ============================================================
echo.
echo   This window will close; services stay alive.
echo   To stop: bin\ikaros-sleep.bat

endlocal
exit /b 0