@echo off
REM ============================================================
REM Hermes - One-click Launcher.
REM Starts llama-server (:8080) + bridge (:7860) + webui (:8648).
REM All processes detached; webui opens browser via its own
REM health-check hook (no Start-Process here; see AGENTS.md §0.7b).
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

REM ---- Single source of truth: resolve HERMES_ROOT + 13 derived paths.
REM      bin\hermes-root.bat handles drive-letter migration, .hermes-root
REM      cache, and fallback drive scan. We just consume the env block.
call "%~dp0..\deps\hermes-env.bat"
if errorlevel 1 (
    echo [FATAL] deps\hermes-env.bat failed to resolve HERMES_ROOT.
    echo         See stderr above for details.
    pause
    exit /b 1
)

set "LLAMA_PORT=8080"
set "BRIDGE_PORT=7860"
set "WEBUI_PORT=8648"

echo ============================================================
echo   Hermes - All-in-One Launcher
echo.
echo   Llama-server:  http://127.0.0.1:%LLAMA_PORT%
echo   Bridge:        http://127.0.0.1:%BRIDGE_PORT%
echo   WebUI:         http://localhost:%WEBUI_PORT%/
echo.
echo   Logs:          %HERMES_ROOT%\data\logs\
echo   Stop:          bin\hermes-stop.bat
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
call "%HERMES_ROOT%\bin\hermes-stop.bat" >nul 2>&1
timeout /t 2 /nobreak >nul

REM ---- Step 2: Start all services via the pure-Python supervisor ----
REM      Why Python: see bin\hermes-supervisor.bat header for the
REM      `cmd /c "powershell -File ..."` fragility this replaced.
echo [2/2] Starting all services via Python supervisor...
call "%HERMES_ROOT%\bin\hermes-supervisor.bat" --start
if errorlevel 1 (
    echo [ERROR] Supervisor failed to start services.
    echo [hint]  See data\logs\supervisor-state.json and per-module *.err
    pause
    exit /b 1
)

REM ---- Done: open browser ----
echo.
echo ============================================================
echo   Ready!
echo.
echo   WebUI:    http://localhost:%WEBUI_PORT%/
echo   Bridge:   http://127.0.0.1:%BRIDGE_PORT%/health
echo   Llama:    http://127.0.0.1:%LLAMA_PORT%/v1/models
echo.
echo   Logs:     %HERMES_ROOT%\data\logs\
echo   Stop:     bin\hermes-stop.bat
echo   Status:   bin\hermes-status.bat
echo ============================================================
echo.

REM Browser opens automatically when hermes-web-ui's health check passes
REM (see runtime/node23/node_modules/hermes-web-ui/bin/hermes-web-ui.mjs,
REM around line 454 -- it does `execSync('start <url>')` on Windows).
REM Doing it here too would open a duplicate tab, so we deliberately do NOT.

endlocal
exit /b 0