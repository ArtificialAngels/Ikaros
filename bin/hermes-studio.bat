@echo off
REM ============================================================
REM Ikaros - Hermes Studio (hermes-web-ui) Launcher
REM Frontend dashboard for Hermes Agent (Vue 3 + Koa monorepo).
REM Source: hermes-studio/ (copied from exProject/hermes-studio-main).
REM Requires Node >= 23 (uses system Node 26.3.0).
REM Pure ASCII. No setlocal. No timeout (use ping -n).
REM ============================================================

call "%~dp0..\Ikaros-environment\init.bat"
if errorlevel 1 (
    echo [FATAL] Ikaros-environment\init.bat failed.
    pause
    exit /b 1
)

set "STUDIO=%IKAROS_STUDIO%"
set "STUDIO_LOGS=%IKAROS_LOGS%\hermes-studio"
if not exist "%STUDIO_LOGS%" mkdir "%STUDIO_LOGS%"

REM Node >= 23 required. Force system Node 26.3.0 ahead of runtime node23.
set "PATH=C:\Program Files\nodejs;%PATH%"
REM Drop project NODE_PATH so the web UI resolves only its own node_modules.
set "NODE_PATH="

REM Keep Web UI runtime state inside our data/ tree (already gitignored).
set "HERMES_WEB_UI_HOME=%IKAROS_DATA%\hermes-studio"

cd /d "%STUDIO%"

REM First launch: install deps (heavy, needs network). Skipped if present.
if not exist "%STUDIO%\node_modules" (
    echo [install] node_modules missing - running npm install (several minutes)...
    call npm install
    if errorlevel 1 (
        echo [FATAL] npm install failed.
        pause
        exit /b 1
    )
) else (
    echo [skip] node_modules present, skipping npm install.
)

REM Dev mode: Koa server :8647 + Vite client :8649 (proxies API to :8647).
REM Production alternative (after build):
REM   npm run build
REM   node bin/hermes-web-ui.mjs
echo Starting Hermes Studio (dev) on http://127.0.0.1:8649 ...

wscript.exe "%~dp0launch-hidden.vbs" "cmd /c npm run dev > ""%STUDIO_LOGS%\hermes-studio.log"" 2>&1"

REM Wait for client port 8649
set "W=0"
:wait_studio
"%IKAROS_PYTHON%" -c "import socket;s=socket.socket();s.settimeout(1);r=s.connect_ex(('127.0.0.1',8649));s.close();exit(0 if r==0 else 1)" >nul 2>&1
if not errorlevel 1 goto :studio_ok
ping -n 3 127.0.0.1 >nul
set /a W+=2
if %W% lss 90 goto :wait_studio
echo [WARN] Hermes Studio did not respond on :8649 within %W%s (may still be starting).
goto :after_studio

:studio_ok
echo Hermes Studio ready: http://127.0.0.1:8649

:after_studio
start "" "http://127.0.0.1:8649"
echo Done.
exit /b 0
