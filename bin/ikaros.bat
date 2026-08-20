@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ikaros launcher (cmd) - ASCII only (GBK safe)

REM IKAROS_ROOT self-anchored
set "IKAROS_ROOT=%~dp0.."
for %%i in ("%IKAROS_ROOT%") do set "IKAROS_ROOT=%%~fi"

REM call PowerShell for cross-shell argument forwarding
powershell -NoProfile -ExecutionPolicy Bypass -File "%IKAROS_ROOT%\bin\ikaros.ps1" %*
set "IKAROS_EXIT=%ERRORLEVEL%"
endlocal & exit /b %IKAROS_EXIT%
