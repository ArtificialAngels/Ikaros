@echo off
rem ikaros-launcher.bat -- GUI double-click entry point (Windows desktop)
rem
rem Thin wrapper (design section 1.2 candidate B): menu-driven GUI entry,
rem forwards every choice to bin/ikaros.bat (unified CLI), which in turn
rem dispatches to core/ikarosctl.py.
rem
rem Why a separate file: bin/ikaros.bat is a thin cmd -> PowerShell shim.
rem When its child command exits, cmd closes the window immediately, so
rem double-clicking the launcher shows nothing. This wrapper adds pause
rem and a menu loop so desktop users can see output and pick the next step.
rem
rem Self-anchored: IKAROS_ROOT = %~dp0.. (same pattern as bin/ikaros-env.bat).
rem
rem ASCII only -- cmd parses bat in ANSI/GBK; UTF-8 comments turn into
rem mojibake that cmd tries to execute (design section 3.3 hard rule).
rem
rem Last updated: 2026-08-22 (line3 launcher polish)

setlocal EnableExtensions EnableDelayedExpansion

set "IKAROS_ROOT=%~dp0.."
for %%I in ("%IKAROS_ROOT%") do set "IKAROS_ROOT=%%~fI"

set "IKAROS_BIN=%IKAROS_ROOT%\bin"
set "LAUNCHER=%IKAROS_BIN%\ikaros.bat"

if not exist "%LAUNCHER%" (
    echo [FATAL] launcher not found: %LAUNCHER%
    pause
    exit /b 1
)

:menu
cls
echo ============================================================
echo   Ikaros Launcher  (IKAROS_ROOT=%IKAROS_ROOT%)
echo ============================================================
echo   1) web       - start dsh work engine (:3080)
echo   2) tree      - start conversation-tree panel (:48920)
echo   3) embed     - start embedding service (:8587)
echo   4) all       - start full stack (embedding + tree + dsh)
echo   5) doctor    - read-only environment diagnostics
echo   6) check     - full runtime environment check (all components)
echo   7) update    - reserved (suspended during base swap)
echo   8) status    - show component states + health snapshots
echo   9) ps        - show running processes
echo   10) logs      - tail a component log (then prompts for id)
echo   11) stop      - stop one component (then prompts for id)
echo   0) exit
echo ============================================================
set /p "CHOICE=choose [0-11]: "

if "%CHOICE%"=="0" exit /b 0
if "%CHOICE%"=="1" call :run web
if "%CHOICE%"=="2" call :run tree
if "%CHOICE%"=="3" call :run embed
if "%CHOICE%"=="4" call :run all
if "%CHOICE%"=="5" call :run doctor
if "%CHOICE%"=="6" call :run check
if "%CHOICE%"=="7" call :run update
if "%CHOICE%"=="8" call :run status
if "%CHOICE%"=="9" call :run ps
if "%CHOICE%"=="10" call :run_interactive logs
if "%CHOICE%"=="11" call :run_interactive stop

echo.
echo [WARN] unknown choice: %CHOICE%
goto menu

:run
call "%LAUNCHER%" %~1
set "EXITCODE=%ERRORLEVEL%"
echo.
echo ------------------------------------------------------------
if "%EXITCODE%"=="0" (
    echo [OK] command completed
) else (
    echo [FAIL] command exited with code %EXITCODE% -- see above for details
)
pause
goto menu

:run_interactive
set /p "ID=component id: "
if "%ID%"=="" (
    echo [WARN] component id required
    goto menu
)
call "%LAUNCHER%" %~1 %ID%
set "EXITCODE=%ERRORLEVEL%"
echo.
echo ------------------------------------------------------------
if "%EXITCODE%"=="0" (
    echo [OK] command completed
) else (
    echo [FAIL] command exited with code %EXITCODE% -- see above for details
)
pause
goto menu
