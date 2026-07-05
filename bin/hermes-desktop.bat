@echo off
REM ============================================================
REM Hermes Desktop - Portable Launcher
REM ============================================================
REM Launches the official Hermes Desktop (Electron app) built from
REM hermes-agent/apps/desktop. All paths derived from HERMES_ROOT
REM so the launcher works on any drive letter (USB portable).
REM
REM Env vars set for the Desktop process:
REM   HERMES_HOME              - data directory (config.yaml, .env, sessions)
REM   HERMES_DESKTOP_HERMES_ROOT - hermes-agent source root
REM   HERMES_DESKTOP_PYTHON    - venv Python with all deps installed
REM   PATH                     - node + portable-python + venv Scripts
REM ============================================================
REM -- Why NO setlocal ------------------------------------------------
REM  The Electron Desktop must see HERMES_HOME / HERMES_DESKTOP_HERMES_ROOT
REM  / HERMES_DESKTOP_PYTHON in its *own* process.env.  `setlocal` keeps
REM  variables local to cmd.exe and the `start`-spawned grandchild has
REM  been observed to inherit the *user-level persistent* env block
REM  instead of the setlocal-modified one on Windows 25H2.
REM  Dropping setlocal lets `set` write the current process env, which
REM  `start` reliably inherits.
REM -------------------------------------------------------------------

REM ---- Resolve Ikaros paths (via init.bat single entry) ----
call "%~dp0..\Ikaros-environment\init.bat"
if errorlevel 1 (
    echo [FATAL] Ikaros-environment\init.bat failed to resolve IKAROS_ROOT.
    exit /b 1
)

REM ---- Derive paths from HERMES_ROOT ----
set "HERMES_HOME=%HERMES_ROOT%\data\hermes-agent"
set "HERMES_DESKTOP_HERMES_ROOT=%HERMES_ROOT%\hermes-agent"
set "HERMES_DESKTOP_PYTHON=%HERMES_ROOT%\hermes-agent\venv\Scripts\python.exe"
REM Pin the Electron resolveHermesCwd() fallback to HERMES_ROOT so that
REM relative terminal.cwd values like '.\' in config.yaml resolve against
REM the project root, not the Electron process cwd (which may be on C:\).
REM This env var is read by main.cjs resolveHermesCwd() as 2nd candidate.
set "HERMES_DESKTOP_CWD=%HERMES_ROOT%"
set "DESKTOP_EXE=%HERMES_ROOT%\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe"
REM 2026-07-02: pin Electron userData to HERMES_ROOT so the portable launcher
REM never bleeds state into %APPDATA%\Roaming\Hermes on the host user account.
REM main.cjs reads HERMES_DESKTOP_USER_DATA_DIR and calls app.setPath(userData)
REM before anything writes. Old C:\Users\<host>\AppData\Roaming\Hermes is left
REM in place (cache-only, no business state) - first launch resets window pos.
set "HERMES_DESKTOP_USER_DATA_DIR=%HERMES_HOME%\desktop"

REM ---- Prepend node + venv to PATH ----
set "PATH=%HERMES_ROOT%\runtime\node23;%HERMES_ROOT%\portable-python;%HERMES_ROOT%\hermes-agent\venv\Scripts;%PATH%"

REM ---- Sanity checks ----
if not exist "%DESKTOP_EXE%" (
    echo [FATAL] Hermes Desktop not found: %DESKTOP_EXE%
    echo         Build it first: cd hermes-agent ^&^& hermes desktop --build-only
    exit /b 1
)
if not exist "%HERMES_DESKTOP_PYTHON%" (
    echo [FATAL] venv Python not found: %HERMES_DESKTOP_PYTHON%
    echo         Install deps: hermes-agent\venv\Scripts\python.exe -m pip install -e .
    exit /b 1
)

REM ---- Pre-flight: warn if bridge is down (prevents backend boot-loop) ----
REM 2026-07-01: if bridge :7860 isn't running, the desktop backend crashes on
REM first API call and enters a crash+restart loop.  Warn early so the user can
REM start the supervisor first, but still launch (cloud-only mode still works).
"%HERMES_ROOT%\portable-python\python.exe" -c "import socket;s=socket.socket();s.settimeout(2);r=s.connect_ex(('127.0.0.1',7860));s.close();exit(0 if r==0 else 1)" >nul 2>&1
if errorlevel 1 (
    echo [WARN] Bridge :7860 not responding. Desktop may show errors on first chat.
    echo        Fix: run bin\hermes-supervisor.bat start first, then retry.
    echo        (Cloud-only models still work without the bridge.)
    echo.
)

REM ---- Launch (redirect stdio to prevent EPIPE when cmd window closes) ----
REM 2026-07-01: wrap in cmd /c so Electron's child-process stderr (Node deprecation
REM warnings with backticks, Chromium GPU cache errors) is captured by the intermediate
REM cmd.exe's console - not the parent cmd.exe that's running this .bat.  Without this,
REM the parent cmd sees raw Electron stderr and can render ". was unexpected at this time."
REM when backtick-containing lines hit the console buffer.
if not exist "%HERMES_HOME%\logs" mkdir "%HERMES_HOME%\logs"
REM Launch Electron completely hidden (no CMD window flash)
REM WScript.Shell.Run with windowStyle=0 = hidden, detached
start "" /B wscript.exe "%~dp0launch-hidden.vbs" "cmd /c ""%DESKTOP_EXE%"" > ""%HERMES_HOME%\logs\desktop-stdout.log"" 2>&1"
exit /b 0
