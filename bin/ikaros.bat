@echo off
rem ikaros.bat -- unified Ikaros entry point (2026-09-05 merged)
rem
rem Behavior:
rem   Double-click (no args) -> interactive menu (12 options) for desktop users.
rem   CLI with args (e.g. "ikaros web" or "ikaros dsh status") -> forward to
rem     PowerShell + ikarosctl.py transparently (same as before).
rem
rem Design:
rem   - Single entry per action: menu -> pick -> run -> exit. NO menu loops
rem     (cmd `call :label` + `goto :label` inside sub creates call stack
rem     recursion that loops forever on EOF. Avoided by single-shot flow.)
rem   - After an action, bat closes. User re-double-clicks for next action.
rem
rem ASCII only -- cmd parses bat in ANSI/GBK; UTF-8 comments turn into
rem mojibake that cmd tries to execute (AGENTS.md hard rule).

setlocal EnableExtensions EnableDelayedExpansion

set "IKAROS_ROOT=%~dp0.."
for %%I in ("%IKAROS_ROOT%") do set "IKAROS_ROOT=%%~fI"

set "IKAROS_BIN=%IKAROS_ROOT%\bin"
set "LAUNCHER=%IKAROS_BIN%\ikaros.ps1"

rem Check if we were invoked with arguments.
rem  - Double-click: %~1 empty -> menu
rem  - CLI: at least one non-empty %1 -> forward
if not "%~1"=="" (
    call "%IKAROS_BIN%\ikaros-env.bat" >nul 2>&1
    if errorlevel 1 (
        echo [FATAL] ikaros-env.bat failed
        exit /b 1
    )
    :: 2026-09-05 fix: 不要让 cmd 用文件关联打开 .ps1 (很多机器 .ps1 关联 notepad).
    :: 显式调 powershell, 避免 .ps1 被 cmd 当数据文件/资源打开.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" %*
    exit /b %ERRORLEVEL%
)

rem === Double-click entry: show menu (single shot, no inner loop) ===
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
echo     s) stop      - stop a component (then pick from list)
echo     r) restart   - restart a component (then pick from list)
echo     q) exit
echo ============================================================
echo Tip: from CLI use "ikaros web" / "ikaros dsh status" etc.
echo ============================================================
set /p "CHOICE=choose [1-11 / s / r / q]: "

if /i "%CHOICE%"=="q" exit /b 0
if /i "%CHOICE%"=="s" goto :menu_stop
if /i "%CHOICE%"=="r" goto :menu_restart

rem === 1-11: direct dispatch (no inner prompt) ===
if "%CHOICE%"=="1" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" web & exit /b 0
if "%CHOICE%"=="2" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" tree & exit /b 0
if "%CHOICE%"=="3" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" embed & exit /b 0
if "%CHOICE%"=="4" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" all & exit /b 0
if "%CHOICE%"=="5" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" dsh open & exit /b 0
if "%CHOICE%"=="6" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" dsh status & exit /b 0
if "%CHOICE%"=="7" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" dsh sync & exit /b 0
if "%CHOICE%"=="8" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" doctor & exit /b 0
if "%CHOICE%"=="9" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" check & exit /b 0
if "%CHOICE%"=="10" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" status & exit /b 0
if "%CHOICE%"=="11" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" ps & exit /b 0

echo.
echo [WARN] unknown choice: %CHOICE%
pause
exit /b 0

:menu_stop
rem === s: stop, then pick from 3-component list ===
echo.
echo   Pick component to STOP:
echo     1) dsh                 DeepSeek Harness work engine (:3080)
echo     2) conversation-tree  Tree panel (dynamic port)
echo     3) embedding          bge-m3 embed service (:8587)
echo     0) cancel
set /p "IDX=number [1-3 / 0=cancel]: "
if "%IDX%"=="0" exit /b 0
if "%IDX%"=="1" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" stop dsh & exit /b 0
if "%IDX%"=="2" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" stop conversation-tree & exit /b 0
if "%IDX%"=="3" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" stop embedding & exit /b 0
echo [WARN] unknown number: %IDX% (valid: 1=dsh / 2=conversation-tree / 3=embedding / 0=cancel)
pause
exit /b 0

:menu_restart
rem === r: restart, then pick from 3-component list ===
echo.
echo   Pick component to RESTART:
echo     1) dsh                 DeepSeek Harness work engine (:3080)
echo     2) conversation-tree  Tree panel (dynamic port)
echo     3) embedding          bge-m3 embed service (:8587)
echo     0) cancel
set /p "IDX=number [1-3 / 0=cancel]: "
if "%IDX%"=="0" exit /b 0
if "%IDX%"=="1" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" restart dsh & exit /b 0
if "%IDX%"=="2" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" restart conversation-tree & exit /b 0
if "%IDX%"=="3" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" restart embedding & exit /b 0
echo [WARN] unknown number: %IDX% (valid: 1=dsh / 2=conversation-tree / 3=embedding / 0=cancel)
pause
exit /b 0
