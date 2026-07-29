@echo off
set "IKAROS_ROOT=%~dp0.."
REM Process-level proxy isolation: keep Ikaros stack off the broken system socks proxy
set "NO_PROXY=*"
set "no_proxy=*"
rem Resolve Ikaros path / self-heal on move, before starting the panel.
call "%IKAROS_ROOT%\bin\resolve-ikaros-path.bat"
if defined RELOC (
  rem Ikaros was found at a different location; re-exec the panel from there.
  call "%RELOC%\bin\ikaros-control.bat"
  goto :eof
)
rem Kill any stale dashboard process still bound to :9100 so a relaunch
rem loads the latest server.py (otherwise the old process keeps serving old code).
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr /r ":9100[ ].*LISTENING"') do taskkill /F /PID %%a >nul 2>&1
ping -n 2 127.0.0.1 >nul
start "" /min "%IKAROS_ROOT%\runtime\portable-python\pythonw.exe" "%IKAROS_ROOT%\core\dashboard\server.py"
start http://127.0.0.1:9100
