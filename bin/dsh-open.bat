@echo off
setlocal EnableExtensions

REM ============================================================
REM  dsh-open.bat -- launch Chrome --app=3080 window for dsh
REM
REM  Thin wrapper around `ikaros dsh open`.  Reuses existing window
REM  if one is already open; otherwise creates a new 1400x900
REM  Chrome --app instance pointed at the configured web port.
REM
REM  ASCII only -- cmd parses bat in ANSI/GBK.
REM ============================================================

set "IKAROS_ROOT=%~dp0.."
for %%i in ("%IKAROS_ROOT%") do set "IKAROS_ROOT=%%~fi"

call "%IKAROS_ROOT%\bin\ikaros.bat" dsh open
exit /b %ERRORLEVEL%
