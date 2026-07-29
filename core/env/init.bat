@echo off
REM See docs/scripts/core/env/init.md
REM No setlocal: vars pass through to caller.

REM ---- Detect IKAROS_ROOT ----
if not defined IKAROS_ROOT (
    for /f "delims=" %%R in ('"%~dp0scripts\detect-root.exe" 2^>nul') do set "IKAROS_ROOT=%%R"
)
if not defined IKAROS_ROOT (
    set "IKAROS_ENV_DIR=%~dp0"
    for %%I in ("%IKAROS_ENV_DIR%\..") do set "IKAROS_ROOT=%%~fI"
)
if not defined IKAROS_ROOT (
    echo [init.bat FAIL] IKAROS_ROOT not detected & exit /b 1
)

REM ---- Load env ----
call "%IKAROS_ROOT%\core\env\ikaros-env.bat" || (
    echo [init.bat FAIL] ikaros-env.bat failed & exit /b 2
)

echo [init] IKAROS_ROOT=%IKAROS_ROOT%
exit /b 0
