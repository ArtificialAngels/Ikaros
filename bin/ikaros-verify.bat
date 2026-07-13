@echo off
REM ============================================================
REM  ikaros-verify.bat -- Suite GREEN entry point for Ikaros V5.1
REM ============================================================
REM  Usage:
REM    ikaros-verify              -- run ALL V5.1 tests (full suite)
REM    ikaros-verify --quick      -- run only goal_contract tests
REM    set IKAROS_SKIP_VERIFY=1   -- silence (exit 0 immediately)
REM
REM  Exit: 0 = all pass, 1 = any fail
REM  Wire into ikaros-start.bat as [Step 0] with --quick flag.
REM ============================================================
REM -- no setlocal (vars must pass to caller for start chain) --

if defined IKAROS_SKIP_VERIFY (
    echo [verify] IKAROS_SKIP_VERIFY=1, skipping.
    exit /b 0
)

REM ---- Self-bootstrap if called standalone ----
if not defined IKAROS_INIT_DONE (
    call "%~dp0..\Ikaros-environment\init.bat"
    if errorlevel 1 (
        echo [verify FATAL] init.bat failed.
        pause
        exit /b 1
    )
)

set "_QUICK="
if /i "%~1"=="--quick" set "_QUICK=1"
if /i "%~1"=="-q"     set "_QUICK=1"

if defined _QUICK (
    set "_TARGET=%IKAROS_ROOT%\Ikaros-memory\v5\tests\test_goal_contract.py"
    echo [verify] Quick mode -- test_goal_contract only
) else (
    set "_TARGET=%IKAROS_ROOT%\Ikaros-memory\v5\tests"
    echo [verify] Full mode -- all V5.1 tests
)
echo.

"%IKAROS_PYTHON%" -m pytest "%_TARGET%" -v --tb=short --no-header
set "_RC=%errorlevel%"

echo.
if %_RC%==0 (
    echo [verify] PASS -- all tests passed.
) else (
    echo [verify] FAIL -- see above for details.
)

exit /b %_RC%