@echo off
setlocal EnableExtensions

REM ============================================================
REM  dsh-sync.bat -- sync cordis.patch.yml to user profile
REM
REM  Thin wrapper around `ikaros dsh sync`.  Pushes the canonical
REM  core/ikaros-dsh/cordis.patch.yml into
REM  ~/.dsh/profiles/web/cordis.patch.yml so that dsh web mode
REM  (which loads the user-level patch) picks up MCP / LSP / persona
REM  changes after editing the canonical file.
REM
REM  ASCII only -- cmd parses bat in ANSI/GBK.
REM ============================================================

set "IKAROS_ROOT=%~dp0.."
for %%i in ("%IKAROS_ROOT%") do set "IKAROS_ROOT=%%~fi"

call "%IKAROS_ROOT%\bin\ikaros.bat" dsh sync
exit /b %ERRORLEVEL%
