@echo off
rem ============================================================
rem Hermes Upstream Sync wrapper — thin shim around the Python CLI.
rem Lets you run `bin\hermes-upstream-sync status` from a regular
rem cmd.exe without remembering the .py extension.
rem ============================================================

setlocal
set HERMES_BIN=%~dp0

rem Pick Python: prefer portable if present, else PATH
if exist "%HERMES_BIN%\..\portable-python\python.exe" (
    set "PY=%HERMES_BIN%\..\portable-python\python.exe"
) else (
    set "PY=python"
)

"%PY%" "%HERMES_BIN%\hermes-upstream-sync.py" %*
endlocal
