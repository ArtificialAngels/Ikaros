@echo off
set "IKAROS_ROOT=%~dp0.."

rem Process-level proxy isolation (plan B): keep Ikaros stack off the broken system socks proxy.
set "NO_PROXY=*"
set "no_proxy=*"

rem Path self-heal: if Ikaros was moved, re-exec this launcher from the real root.
call "%IKAROS_ROOT%\bin\resolve-ikaros-path.bat"
if defined RELOC (
  call "%RELOC%\bin\ikaros-control-panel.bat"
  goto :eof
)

set "PY=%IKAROS_ROOT%/runtime/portable-python/pythonw.exe"
set "SERVER=%IKAROS_ROOT%/core/dashboard/server.py"

rem Kill any stale dashboard still bound to :9100 so a relaunch loads the latest server.py.
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr /r ":9100[ ].*LISTENING"') do taskkill /F /PID %%a >nul 2>&1
ping -n 2 127.0.0.1 >nul

rem Start ONLY the control-panel backend on :9100.
rem Deliberately do NOT launch N.E.K.O.exe (the Electron shell) — it reuses Neko's
rem electron binary, so its window looks identical to Neko and confuses "panel-only".
rem Also never pass --autostart: this launcher must never pull the full stack.
start "" /min "%PY%" "%SERVER%"

rem Open the panel UI in the default browser once :9100 is up (server.py boots ~12s).
ping -n 14 127.0.0.1 >nul
start "" "http://127.0.0.1:9100"
