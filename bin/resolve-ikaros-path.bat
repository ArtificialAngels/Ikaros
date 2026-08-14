@echo off
rem resolve-ikaros-path.bat
rem Runs path_resolve.py (path acquisition + self-heal) on 9100 panel launch.
rem Sets RELOC if Ikaros was found at a different location, so the caller
rem (ikaros-control-panel.bat) can re-exec the panel from the correct root.
set "IKAROS_ROOT=%~dp0.."
set "PY=%IKAROS_ROOT%\runtime\portable-python\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%IKAROS_ROOT%\bin\path_resolve.py"
set "RELOC="
if exist "%TEMP%\ikaros_relocate_root.txt" (
  set /p RELOC=<"%TEMP%\ikaros_relocate_root.txt"
  del /f /q "%TEMP%\ikaros_relocate_root.txt" >nul 2>&1
)
