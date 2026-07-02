@echo off
REM ============================================================
REM Hermes - Pure Python supervisor launcher.
REM
REM Python instead of cmd /c "powershell -File ..." because the cmd
REM bridge dies on paths with spaces; see AGENTS.md §0.4 gotcha
REM ("PowerShell -File must use a quoted absolute path") and
REM the module-level note in bin\hermes-supervisor.py docstring.
REM
REM Usage:
REM   bin\hermes-supervisor.bat           # start all services
REM   bin\hermes-supervisor.bat --status  # port health check
REM   bin\hermes-supervisor.bat --stop    # stop all services
REM   bin\hermes-supervisor.bat --dry-run # show start order
REM   bin\hermes-supervisor.bat --only bridge llm_engine
REM ============================================================
setlocal
chcp 65001 >nul

REM ---- Single source of truth: bin\hermes-root.bat (drive-letter agnostic) ----
call "%~dp0..\deps\hermes-env.bat"
if errorlevel 1 (
    echo [FATAL] could not resolve HERMES_ROOT.
    exit /b 2
)

REM ---- Fail-fast HERMES_ROOT verify (NEW 2026-06-16) ----
REM Prevents services from launching with a broken path after USB drive-letter
REM swap (e.g. E: -> F:). hermes-root.bat verify exit codes: 0=ok, 1=marker missing, 2=unresolved.
call "%HERMES_BIN%\hermes-root.bat" verify
set "VERIFY_RC=%ERRORLEVEL%"
if not "%VERIFY_RC%"=="0" (
    echo.
    echo [FATAL] HERMES_ROOT verify FAILED ^(rc=%VERIFY_RC%^)
    echo         Resolved root: %HERMES_ROOT%
    echo         Critical marker check failed:
    echo           - portable-python\python.exe NOT found at:
    echo             "%HERMES_ROOT%\portable-python\python.exe"
    echo.
    echo         Likely cause: USB drive letter changed ^(e.g. E: -^> F:^),
    echo         but a stale env var or .hermes-root cache still points to
    echo         the old location.
    echo.
    echo         Fix ^(pick one^):
    echo           1. Re-insert the USB stick and re-run ikaros-start.bat.
    echo           2. Force re-resolve by deleting .hermes-root at the
    echo              project's root, then re-run ikaros-start.bat.
    echo           3. Manually: bin\hermes-root.bat init ^&^& bin\ikaros-start.bat
    echo.
    exit /b 3
)

set "SUPERVISOR=%HERMES_BIN%\hermes-supervisor.py"

if not exist "%HERMES_PYTHON%" (
    echo [FATAL] portable-python not found: %HERMES_PYTHON%
    echo         Re-run bin\setup-portable.bat to install.
    exit /b 2
)
if not exist "%SUPERVISOR%" (
    echo [FATAL] supervisor not found: %SUPERVISOR%
    exit /b 2
)

REM Hand off to Python directly. No cmd / c layer, so quotes / spaces
REM in paths cannot break parsing.
"%HERMES_PYTHON%" "%SUPERVISOR%" %*
exit /b %ERRORLEVEL%
