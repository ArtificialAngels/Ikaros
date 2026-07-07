@echo off
REM ============================================================
REM Ikaros Memory v4 CLI wrapper (V3 removed 2026-07-07)
REM ============================================================
REM Routes python calls to portable-python (has chromadb + aiosqlite
REM + sqlite-vec) instead of the default hermes-agent venv, which
REM does NOT have these packages. Without this, `python v4\store.py`
REM silently fails on VectorIndex() because chromadb is missing.
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

REM V4 default (Phase 4 cutover, V3 removed 2026-07-07)
set "MEM_SCRIPT=%~dp0..\Ikaros-memory\v4\store.py"

"%IKAROS_PYTHON%" "%MEM_SCRIPT%" %*
