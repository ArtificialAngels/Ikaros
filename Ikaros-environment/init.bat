@echo off
REM ============================================================
REM init.bat - Ikaros Environment Single Entry Point (CMD)
REM ============================================================
REM  Single entry: any bat script can call this to get IKAROS_*
REM  env vars + PATH/PYTHONPATH/sys.path equivalents.
REM
REM  Usage (from anywhere):
REM    call "E:\Ikaros\Ikaros-environment\init.bat"
REM  It will:
REM    1) Detect IKAROS_ROOT (5 priority levels, see scripts/detect-root.ps1)
REM    2) call ikaros-env.bat (core 11 steps, see PATH-LAYER.md)
REM    3) Echo self-check (tell caller "init OK", or throw error)
REM ============================================================
REM NOTE: no setlocal, follows ikaros-env.bat convention (vars pass through to caller)

REM ---- Step 0: guard - prevent duplicate init ----
if defined IKAROS_INIT_DONE goto :init_done

REM ---- Step 1: detect IKAROS_ROOT ----
REM Use native Rust binary (fast, no PowerShell encoding issues)
if not defined IKAROS_ROOT (
    for /f "delims=" %%R in ('"%~dp0scripts\detect-root.exe" 2^>nul') do set "IKAROS_ROOT=%%R"
)
if not defined IKAROS_ROOT (
    REM Fallback: derive from script location parent dir
    REM GOTCHA: no "\" comparison here - cmd.exe treats \" as escape-quote
    REM inside () blocks, causing syntax error. The for loop handles
    REM trailing backslash fine (\\.. resolves correctly).
    set "IKAROS_ENV_DIR=%~dp0"
    for %%I in ("%IKAROS_ENV_DIR%\..") do set "IKAROS_ROOT=%%~fI"
)
if not defined IKAROS_ROOT (
    echo [init.bat FAIL] IKAROS_ROOT not detected & exit /b 1
)

REM ---- Step 2: call ikaros-env.bat (core 11 steps PATH bootstrap) ----
call "%IKAROS_ROOT%\Ikaros-environment\ikaros-env.bat" || (
    echo [init.bat FAIL] ikaros-env.bat failed & exit /b 2
)

REM ---- Step 3: self-check marker ----
set "IKAROS_INIT_DONE=1"
echo [Ikaros init.bat OK] IKAROS_ROOT=%IKAROS_ROOT%
echo [Ikaros init.bat OK] python=%IKAROS_PYTHON%
echo [Ikaros init.bat OK] node=%IKAROS_NODE%

:init_done
exit /b 0
