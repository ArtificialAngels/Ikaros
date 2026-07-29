@echo off
REM hermes.cmd — Hermes CLI wrapper that auto-sources the Ikaros portable env
REM Usage: hermes [args...]
REM        hermes dashboard
REM        hermes chat

call "%~dp0core\env\ikaros-env.bat" >nul 2>&1

if not defined HERMES_BIN (
    echo [error] HERMES_BIN not set — is core/env/ikaros-env.bat working? 1>&2
    exit /b 1
)

"%HERMES_BIN%" %*
