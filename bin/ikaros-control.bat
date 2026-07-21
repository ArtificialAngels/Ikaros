@echo off
set "IKAROS_ROOT=%~dp0.."
start "" /min "%IKAROS_ROOT%\runtime\portable-python\pythonw.exe" "%IKAROS_ROOT%\tools\ikaros-dashboard\server.py"
start http://127.0.0.1:9100
