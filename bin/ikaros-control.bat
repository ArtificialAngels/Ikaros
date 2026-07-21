@echo off
set "PANEL_PORT=9100"
set "IKAROS_ROOT=%~dp0.."
set "PY=%IKAROS_ROOT%\runtime\portable-python\python.exe"
set "PYW=%IKAROS_ROOT%\runtime\portable-python\pythonw.exe"

rem 1) Find and kill any process already listening on :9100
set "OLDPID="
for /f "tokens=*" %%P in ('"%PY%" -c "import subprocess; out=subprocess.run(['netstat','-ano'],capture_output=True,text=True).stdout; lines=[l for l in out.splitlines() if ':9100' in l and ('LISTEN' in l or 'ESTABLISHED' in l)]; print(lines[0].split()[-1] if lines else '')"') do set "OLDPID=%%P"

if defined OLDPID (
  echo [control] killing old panel pid %OLDPID% on :%PANEL_PORT%
  taskkill /F /PID %OLDPID% >nul 2>&1
  ping -n 2 127.0.0.1 >nul
)

rem 2) Launch the patched dashboard (studio-update + panel.html)
start "" /min "%PYW%" "%IKAROS_ROOT%\bin\ikaros-dashboard-patched.py"
ping -n 3 127.0.0.1 >nul
start http://127.0.0.1:%PANEL_PORT%/
