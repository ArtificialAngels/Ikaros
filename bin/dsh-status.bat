@echo off
setlocal EnableExtensions

REM ============================================================
REM  dsh-status.bat -- one-shot dsh web health summary
REM
REM  Thin wrapper around `ikaros dsh status`.  Surfaces:
REM    - 3080 listen / dsh web PID / CT port / client.js URL sync / CT HTTP
REM
REM  ASCII only -- cmd parses bat in ANSI/GBK, UTF-8 comments break it.
REM ============================================================

set "IKAROS_ROOT=%~dp0.."
for %%i in ("%IKAROS_ROOT%") do set "IKAROS_ROOT=%%~fi"

call "%IKAROS_ROOT%\bin\ikaros.bat" dsh status
exit /b %ERRORLEVEL%
