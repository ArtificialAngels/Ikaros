@echo off
REM See docs/scripts/bin/ikaros-think.md

call "%~dp0..\Ikaros-environment\init.bat" >nul 2>&1
if errorlevel 1 (
    echo [think FATAL] init.bat failed.
    exit /b 1
)

"%IKAROS_PYTHON%" "%~dp0..\Ikaros-memory\v5\think.py" %*
exit /b %errorlevel%
