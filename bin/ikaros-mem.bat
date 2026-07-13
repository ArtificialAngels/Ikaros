@echo off
REM ============================================================
REM Ikaros Memory V5.1 CLI wrapper
REM ============================================================
REM Routes python calls to portable-python (has chromadb + aiosqlite
REM + sqlite-vec) instead of the default hermes-agent venv, which
REM does NOT have these packages.
REM
REM Usage: ikaros-mem <command> [args...]
REM   ikaros-mem stats
REM   ikaros-mem search "query"
REM   ikaros-mem store "content" --type fact --weight 0.7
REM   ikaros-mem decay
REM ============================================================

call "%~dp0..\Ikaros-environment\init.bat" >nul 2>&1
if not defined IKAROS_PYTHON (
    echo [FATAL] Ikaros-environment\init.bat did not set IKAROS_PYTHON.
    exit /b 1
)

REM V5.1 default
set "MEM_SCRIPT=%~dp0..\Ikaros-memory\v5\store.py"

"%IKAROS_PYTHON%" "%MEM_SCRIPT%" %*
