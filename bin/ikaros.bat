@echo off
rem ikaros.bat -- unified Ikaros entry point (2026-09-05)
rem
rem Behavior:
rem   Double-click (no args) -> interactive menu (6 options) for desktop users.
rem   CLI with args (e.g. "ikaros web" or "ikaros dsh status") -> forward to
rem     PowerShell + ikarosctl.py transparently.
rem
rem Architecture (2026-09-05 confirmed):
rem   - dsh is the SOLE process. tree / v5 / settings auto-load as dsh plugins.
rem   - core/conversation-tree/server.py is spawned by ikaros-conversation-tree
rem     Node plugin watchdog (:48920) -- no menu entry needed.
rem   - llama-server (bge-m3 :8587) is started by ikaros-memory-settings RPC
rem     (settings panel) -- no menu entry needed.
rem   - Therefore menu = dsh open/stop/restart + sync + status. That's it.
rem
rem Design:
rem   - Single entry per action: menu -> pick -> run -> exit. NO menu loops.
rem   - After an action, bat closes. User re-double-clicks for next action.
rem
rem ASCII only -- cmd parses bat in ANSI/GBK; UTF-8 comments turn into
rem mojibake that cmd tries to execute (AGENTS.md hard rule).

setlocal EnableExtensions EnableDelayedExpansion

set "IKAROS_ROOT=%~dp0.."
for %%I in ("%IKAROS_ROOT%") do set "IKAROS_ROOT=%%~fI"

set "IKAROS_BIN=%IKAROS_ROOT%\bin"

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
echo   dsh is the sole process. Plugins (ct / v5 / settings)
echo   auto-load via cordis.patch.yml. Use dsh main panel to
echo   interact with them. This menu only manages dsh itself.
echo.
echo     1) open     - start dsh and launch Chrome --app (:3080)
echo     2) stop     - stop dsh (and its plugins)
echo     3) restart  - stop + start dsh (no Chrome; use 'open' after)
echo     4) sync     - sync cordis.patch.yml to user profile
echo     5) status   - one-shot status (dsh + plugins + ports)
echo     q) exit
echo ============================================================
echo Tip: from CLI use "ikaros web" / "ikaros dsh status" etc.
echo ============================================================
set /p "CHOICE=choose [1-5 / q]: "

if /i "%CHOICE%"=="q" exit /b 0

rem === 1-5: direct dispatch (no inner prompt) ===
if "%CHOICE%"=="1" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" dsh open & exit /b 0
if "%CHOICE%"=="2" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" dsh stop & exit /b 0
if "%CHOICE%"=="3" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" dsh restart & exit /b 0
if "%CHOICE%"=="4" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" dsh sync & exit /b 0
if "%CHOICE%"=="5" powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_BIN%\ikaros.ps1" dsh status & exit /b 0

echo.
echo [WARN] unknown choice: %CHOICE% (valid: 1-5 or q)
pause
exit /b 0