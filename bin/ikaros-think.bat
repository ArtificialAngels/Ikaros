@echo off
REM ============================================================
REM  ikaros-think.bat - V5.1 self-think loop
REM ============================================================
REM  Usage:
REM    ikaros-think           - run one think cycle (for cron)
REM    ikaros-think --watch   - loop every 5 min (daemon)
REM    ikaros-think --interval=N - custom interval (min)
REM
REM  Exit: 0 = thought generated, 1 = none
REM ============================================================

call "%~dp0..\Ikaros-environment\init.bat" >nul 2>&1
if errorlevel 1 (
    echo [think FATAL] init.bat failed.
    exit /b 1
)

"%IKAROS_PYTHON%" "%~dp0..\Ikaros-memory\v5\think.py" %*
exit /b %errorlevel%
