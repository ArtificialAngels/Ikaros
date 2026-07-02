@echo off
REM ============================================================
REM  Ikaros — Graceful Shutdown
REM ============================================================
REM  Stops all Ikaros processes in reverse dependency order:
REM    1. Watchdog daemon  (via supervisor --stop)
REM    2. Backend services (reverse topo-sort of module.json)
REM    3. Hermes Desktop   (Electron)
REM    4. Desktop Pet      (system tray)
REM    5. Safety sweep     (any orphaned llama-server)
REM ============================================================
setlocal
chcp 65001 >nul

REM ---- [env] Resolve HERMES_ROOT ----
call "%~dp0..\deps\hermes-env.bat"
if errorlevel 1 (
    echo [FATAL] could not resolve HERMES_ROOT.
    exit /b 1
)

echo ============================================================
echo   Ikaros — Shutting down
echo ============================================================
echo.

REM ---- Step 1: Stop supervisor-managed services (reverse topo order) ----
REM      supervisor --stop also kills the watchdog daemon first,
REM      then stops each service via its stop.ps1 in reverse
REM      dependency order (bridge -> llm_engine -> env_bootstrap).
echo [1]  Stopping backend services ^(supervisor^)...
call "%HERMES_ROOT%\bin\hermes-supervisor.bat" --stop

REM ---- Step 2: Stop Hermes Desktop (Electron) ----
echo.
echo [2]  Stopping Hermes Desktop...
taskkill /F /IM "Hermes.exe" /T >nul 2>&1

REM ---- Step 3: Stop Desktop Pet ----
echo [3]  Stopping Desktop Pet...
call "%HERMES_ROOT%\bin\hermes-pet.bat" stop >nul 2>&1

REM ---- Step 4: Safety sweep — orphaned llama-server ----
REM      The supervisor's stop.ps1 should have stopped these already.
REM      This is a belt-and-suspenders fallback for edge cases.
echo [4]  Safety sweep: orphaned llama-server processes...
taskkill /F /IM "llama-server.exe" /T >nul 2>&1
taskkill /F /IM "llama-server-cuda-12.4.exe" /T >nul 2>&1
taskkill /F /IM "llama-server-vulkan.exe" /T >nul 2>&1

REM ---- Done ----
echo.
echo ============================================================
echo   All Ikaros processes stopped.
echo ============================================================
endlocal
exit /b 0
