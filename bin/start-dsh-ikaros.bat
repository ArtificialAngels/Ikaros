@echo off
setlocal EnableExtensions

REM ============================================================
REM  Ikaros work engine -- DeepSeek Harness (DSH) launcher
REM
REM  Thin wrapper: forwards to `ikaros web` / `ikaros headless`.
REM  Real implementation: core/ikarosctl.py
REM  (2026-08-20: collapsed to a thin wrapper around ikaros launcher, see docs/ikaros-launcher-design.md)
REM  ASCII only -- cmd parses bat in ANSI/GBK, UTF-8 comments break it.
REM ============================================================

set "IKAROS_ROOT=%~dp0.."
for %%i in ("%IKAROS_ROOT%") do set "IKAROS_ROOT=%%~fi"

call "%IKAROS_ROOT%\bin\ikaros-env.bat"
if errorlevel 1 (
    echo [FATAL] ikaros-env.bat failed.
    exit /b 1
)

"%IKAROS_ROOT%\bin\ikaros.bat" %*
exit /b %ERRORLEVEL%
