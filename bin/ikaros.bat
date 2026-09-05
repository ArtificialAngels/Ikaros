@echo off
rem ikaros.bat -- unified Ikaros entry point (2026-09-05 merged)
rem
rem Behavior:
rem   Double-click (no args) -> interactive menu (12 options) for desktop users.
rem   CLI with args (e.g. "ikaros web" or "ikaros dsh status") -> forward to
rem     PowerShell + ikarosctl.py transparently (same as before).
rem
rem Why this file (and not separate):
rem   - Single entry point per profile (no per-action .bat scatter)
rem   - GUI users see menu; CLI users keep transparent forwarding
rem   - Win+R "ikaros" hits the menu (was: hit ikaros.bat -> forwarded to ikaros.ps1 with no args, error)
rem
rem Merged from (deleted 2026-09-05): start-dsh-ikaros.bat, restart-dsh-ikaros.ps1,
rem   dsh-open.bat, dsh-status.bat, dsh-sync.bat, ikaros-launcher.bat
rem
rem ASCII only -- cmd parses bat in ANSI/GBK; UTF-8 comments turn into
rem mojibake that cmd tries to execute (AGENTS.md hard rule).

setlocal EnableExtensions EnableDelayedExpansion

set "IKAROS_ROOT=%~dp0.."
for %%I in ("%IKAROS_ROOT%") do set "IKAROS_ROOT=%%~fI"

set "IKAROS_BIN=%IKAROS_ROOT%\bin"
set "LAUNCHER=%IKAROS_BIN%\ikaros.ps1"

rem Check if we were invoked with arguments.
rem  - Double-click: %CMDCMDLINE% ends with the .bat path; %1..%9 are empty
rem  - CLI: at least one non-empty %1
rem
rem Edge cases handled:
rem  - "/c ikaros.bat web" (cmd /c): %1=web
rem  - "ikaros web" (from Win+R after PATH set): %1=web
rem  - ""  (double-click, run via cmd): %1 empty
if "%~1"=="" goto :menu

rem CLI path -- transparent forwarding
call "%IKAROS_BIN%\ikaros-env.bat" >nul 2>&1
if errorlevel 1 (
    echo [FATAL] ikaros-env.bat failed
    exit /b 1
)
"%IKAROS_BIN%\ikaros.ps1" %*
exit /b %ERRORLEVEL%

:menu
rem Interactive menu -- shown when invoked with no args
cls
echo ============================================================
echo   Ikaros Launcher  (IKAROS_ROOT=%IKAROS_ROOT%)
echo ============================================================
echo   Components
echo     1) web       - start dsh work engine (:3080)
echo     2) tree      - start conversation-tree panel (dynamic port)
echo     3) embed     - start embedding service (:8587)
echo     4) all       - start full stack (embedding + tree + dsh)
echo   dsh shortcuts
echo     5) dsh-open  - launch Chrome --app window for :3080
echo     6) dsh-status - one-shot health summary
echo     7) dsh-sync  - sync cordis.patch.yml to user profile
echo   Diagnostics + control
echo     8) doctor    - read-only environment diagnostics
echo     9) check     - full runtime environment check
echo     10) status   - show component states
echo     11) ps       - show running processes
echo     12) stop     - stop one component (then prompts for id)
echo     0) restart  - restart one component (then prompts for id)
echo     q) exit
echo ============================================================
echo Tip: from CLI use "ikaros web" / "ikaros dsh status" etc.
echo ============================================================
set /p "CHOICE=choose [0-12 or q]: "

if /i "%CHOICE%"=="q" exit /b 0
if "%CHOICE%"=="0" call :run_interactive restart
if "%CHOICE%"=="1" call :run web
if "%CHOICE%"=="2" call :run tree
if "%CHOICE%"=="3" call :run embed
if "%CHOICE%"=="4" call :run all
if "%CHOICE%"=="5" call :run dsh open
if "%CHOICE%"=="6" call :run dsh status
if "%CHOICE%"=="7" call :run dsh sync
if "%CHOICE%"=="8" call :run doctor
if "%CHOICE%"=="9" call :run check
if "%CHOICE%"=="10" call :run status
if "%CHOICE%"=="11" call :run ps
if "%CHOICE%"=="12" call :run_interactive stop

echo.
echo [WARN] unknown choice: %CHOICE%
pause
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
    pause
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
