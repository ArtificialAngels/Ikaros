@echo off
REM ============================================================
REM bin\hermes-root.bat -- Thin bat launcher for hermes-root.py
REM
REM Why this exists: All other .bat / .ps1 files in the project
REM call into here to resolve HERMES_ROOT. This is the ONE place
REM that knows how to invoke the Python resolver with the right
REM portable Python.
REM
REM Subcommands (forwarded to hermes-root.py):
REM   resolve, verify, init, scan, persist, clean
REM
REM Usage (from project root):
REM   call bin\hermes-root.bat init
REM   for /f "usebackq tokens=1,* delims==" %%K in (`call bin\hermes-root.bat init`) do set "%%K=%%L"
REM
REM Why Python: cmd /c's quote parsing breaks on paths with spaces
REM ("E:\Hermes Agent" gets truncated at the space). Python
REM subprocess.Popen with list args goes straight to CreateProcessW,
REM sidestepping all cmd /c / PowerShell -File fragility.
REM ============================================================
setlocal

set "HERE=%~dp0"
set "HERMES_ROOT=%HERE%.."
for %%I in ("%HERMES_ROOT%") do set "HERMES_ROOT=%%~fI"
set "PY=%HERMES_ROOT%\portable-python\python.exe"
set "RESOLVER=%HERMES_ROOT%\bin\hermes-root.py"

if not exist "%PY%" (
    echo [hermes-root] FATAL: portable Python not found at "%PY%"
    echo [hermes-root]   Run bin\setup-portable.bat to install it.
    exit /b 1
)
if not exist "%RESOLVER%" (
    echo [hermes-root] FATAL: resolver not found at "%RESOLVER%"
    exit /b 1
)

REM ---- Forward all args to hermes-root.py ----
"%PY%" "%RESOLVER%" %*
exit /b %ERRORLEVEL%
