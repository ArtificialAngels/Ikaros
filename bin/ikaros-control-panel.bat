@echo off
set "IKAROS_ROOT=%~dp0.."

rem Process-level proxy isolation (plan B): keep Ikaros stack off the broken system socks proxy.
set "NO_PROXY=*"
set "no_proxy=*"

set "PANEL_DIR=%IKAROS_ROOT%/core/control-panel"
set "ELECTRON=%IKAROS_ROOT%/apps/neko/N.E.K.O.exe"
set "PY=%IKAROS_ROOT%/runtime/portable-python/pythonw.exe"
set "SERVER=%IKAROS_ROOT%/core/dashboard/server.py"

rem Kill any stale dashboard still bound to :9100 so a relaunch loads the latest server.py.
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr /r ":9100[ ].*LISTENING"') do taskkill /F /PID %%a >nul 2>&1
ping -n 2 127.0.0.1 >nul

rem Start the headless control-panel backend (server.py does NOT auto-open a browser).
start "" /min "%PY%" "%SERVER%"

rem Launch the standalone Electron panel, reusing N.E.K.O's electron binary (no separate download).
start "" "%ELECTRON%" "%PANEL_DIR%/main.js"
