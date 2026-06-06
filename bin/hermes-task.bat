@echo off
REM ============================================================
REM Hermes - Run autonomous task from command line
REM
REM Usage:
REM     bin\hermes-task.bat "do something"
REM     bin\hermes-task.bat "do something" --mock
REM     bin\hermes-task.bat "do something" --json
REM
REM Requires: hermes-all.bat running (or at least llama-server + hermes serve)
REM Or: use --mock to test without LLM.
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "PY=%HERMES_ROOT%\portable-python\python.exe"

REM Pass through all args
set "ARGS=%*"
echo [hermes-task] Running: %ARGS%
"%PY%" -m hermes task %ARGS%
exit /b %ERRORLEVEL%
