@echo off
REM ============================================================
REM  ikaros-think.bat — V5 空闲思考循环 (Inner Monologue)
REM ============================================================
REM  Usage:
REM    ikaros-think           — run one think cycle (for cron)
REM    ikaros-think --watch   — loop every 45 min (daemon)
REM    ikaros-think --interval=30  — custom interval (min)
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