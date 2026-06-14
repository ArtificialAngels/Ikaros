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
REM   bin\hermes-supervisor.bat --only webui bridge
REM ============================================================
setlocal
chcp 65001 >nul

REM ---- Single source of truth: bin\hermes-root.bat (drive-letter agnostic) ----
call "%~dp0..\deps\hermes-env.bat"
if errorlevel 1 (
    echo [FATAL] could not resolve HERMES_ROOT.
    exit /b 2
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
