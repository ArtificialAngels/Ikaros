@echo off
REM ============================================================
REM  DEPRECATED — Use bin\ikaros-start.bat instead.
REM  This file is kept as a redirect for backward compatibility.
REM ============================================================
echo [deprecated] hermes-all.bat is deprecated. Use ikaros-start.bat.
echo             Redirecting...
echo.
call "%~dp0ikaros-start.bat"
exit /b %ERRORLEVEL%