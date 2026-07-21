@echo off
set "IKAROS_ROOT=%~dp0.."
rem Kill any stale dashboard process still bound to :9100 so a relaunch
rem loads the latest server.py (otherwise the old process keeps serving old code).
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr /r ":9100[ ].*LISTENING"') do taskkill /F /PID %%a >nul 2>&1
ping -n 2 127.0.0.1 >nul
start "" /min "%IKAROS_ROOT%\runtime\portable-python\pythonw.exe" "%IKAROS_ROOT%\tools\ikaros-dashboard\server.py"
start http://127.0.0.1:9100
