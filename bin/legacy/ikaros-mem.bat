@echo off
REM See docs/scripts/bin/ikaros-mem.md

call "%~dp0..\Ikaros-environment\init.bat" >nul 2>&1
if not defined IKAROS_PYTHON (
    echo [FATAL] Ikaros-environment\init.bat did not set IKAROS_PYTHON.
    exit /b 1
)

REM V5.1 default
set "MEM_SCRIPT=%~dp0..\Ikaros-memory\v5\store.py"

"%IKAROS_PYTHON%" "%MEM_SCRIPT%" %*
