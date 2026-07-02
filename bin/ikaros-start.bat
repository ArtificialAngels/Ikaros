@echo off
REM ============================================================
REM Hermes - One-click Launcher.
REM Starts llama-server (:8080) + bridge (:7860) via supervisor,
REM then launches Hermes Desktop (official Electron app) as the
REM primary frontend. All processes detached.
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

REM ---- Single source of truth: deps\hermes-env.bat resolves HERMES_ROOT + 13 derived paths. ----
call "%~dp0..\deps\hermes-env.bat"
if errorlevel 1 (
    echo [FATAL] deps\hermes-env.bat failed to resolve HERMES_ROOT.
    echo         See stderr above for details.
    pause
    exit /b 1
)

set "LLAMA_PORT=8080"
set "BRIDGE_PORT=7860"

echo ============================================================
echo   Hermes - All-in-One Launcher
echo.
echo   Llama-server:  http://127.0.0.1:%LLAMA_PORT% (disabled - cloud-only)
echo   Bridge:        http://127.0.0.1:%BRIDGE_PORT%
echo   Frontend:      Hermes Desktop (Electron)
echo.
echo   Logs:          %HERMES_ROOT%\data\logs\
echo   Stop:          bin\ikaros-sleep.bat
echo   Status:        bin\hermes-status.bat
echo ============================================================
echo.

REM ---- Step 0a: CRLF sanity check (warn only, do not block) ----
REM      cmd.exe does not parse LF-only .bat files correctly (paths with
REM      spaces get truncated, scripts fail silently). Catch this BEFORE
REM      anything tries to launch. To fix: run
REM      `portable-python\python.exe bin\fix-eol.py --all`.
echo [0a] Checking bat / ps1 line endings...
"%HERMES_PYTHON%" "%HERMES_BIN%\fix-eol.py" --check >nul 2>&1
if errorlevel 1 (
    echo [WARN] Some bat / ps1 files have wrong line endings!
    echo [WARN] Run: portable-python\python.exe bin\fix-eol.py --all
    echo [WARN] Continuing anyway - failures may surface later.
)

REM ---- Step 0: Environment bootstrap (portable python / runtime / model) ----
echo [0/2] Checking portable environment...
call "%HERMES_ROOT%\bin\setup-portable.bat" auto >nul
if errorlevel 1 (
    echo [warn] setup-portable had warnings - continuing anyway.
    echo [warn] Re-run bin\setup-portable.bat later to retry.
)

REM ---- Step 1: Kill any stale instances ----
echo [1/2] Stopping old instances (if any)...
call "%HERMES_ROOT%\bin\ikaros-sleep.bat" >nul 2>&1
timeout /t 2 /nobreak >nul

REM ---- Step 2: Start all services via the pure-Python supervisor ----
REM      Why Python: see bin\hermes-supervisor.bat header for the
REM      `cmd /c "powershell -File ..."` fragility this replaced.
echo [2/2] Starting all services via Python supervisor...

REM ---- Step 2a: double-verify at user-facing entry (supervisor.bat already
REM      does this; doing it here too gives clearer error attribution) ----
call "%HERMES_BIN%\hermes-root.bat" verify
if errorlevel 1 (
    echo.
    echo [FATAL] HERMES_ROOT verify FAILED at ikaros-start.bat entry.
    echo         The supervisor was NOT started. See the error from
    echo         `bin\hermes-root.bat verify` above for diagnosis.
    pause
    exit /b 3
)

call "%HERMES_ROOT%\bin\hermes-supervisor.bat" --start
if errorlevel 1 (
    echo [ERROR] Supervisor failed to start services.
    echo [hint]  See data\logs\supervisor-state.json and per-module *.err
    pause
    exit /b 1
)

REM ---- Step 3: Hermes Desktop (primary frontend) ----
echo.
echo [desktop] Launching Hermes Desktop...
call "%HERMES_BIN%\hermes-desktop.bat"
if errorlevel 1 (
    echo [desktop] WARNING: Hermes Desktop failed to start.
    echo [desktop] See data\logs\desktop.log for details.
) else (
    echo [desktop] ✓
)

REM ---- Step 4: Desktop Pet (non-critical, not expected to fail) ----
echo.
echo [pet]  Desktop Pet...
start "" /B cmd /c ""%HERMES_BIN%\hermes-pet.bat" start" >nul 2>&1
echo [pet]  ✓ launch initiated

REM ---- Done ----
echo.
echo ============================================================
echo   Ready!
echo.
echo   Frontend: Hermes Desktop (Electron window)
echo   Bridge:   http://127.0.0.1:%BRIDGE_PORT%/health
echo   LLM:      cloud (MiniMax-M3 default, auto-flip if local needed)
echo.
echo   Logs:     %HERMES_ROOT%\data\logs\
echo   Stop:     bin\ikaros-sleep.bat
echo   Status:   bin\hermes-supervisor.bat --status
echo ============================================================
echo.
echo   Window will close now; services stay alive.
echo   To stop everything: bin\ikaros-sleep.bat

endlocal
exit /b 0