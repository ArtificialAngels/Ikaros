@echo off
REM See docs/scripts/bin/hermes-desktop.md
REM No setlocal: Electron must see these env vars.

call "%~dp0..\Ikaros-environment\init.bat"
if errorlevel 1 (
    echo [FATAL] init.bat failed.
    exit /b 1
)

set "HERMES_HOME=%HERMES_ROOT%\data\hermes-agent"
set "HERMES_DESKTOP_HERMES_ROOT=%HERMES_ROOT%\hermes-agent"
set "HERMES_DESKTOP_PYTHON=%HERMES_ROOT%\hermes-agent\venv\Scripts\python.exe"
set "HERMES_DESKTOP_CWD=%HERMES_ROOT%"
set "HERMES_DESKTOP_USER_DATA_DIR=%HERMES_HOME%\desktop"
set "DESKTOP_EXE=%HERMES_ROOT%\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe"
set "PATH=%HERMES_ROOT%\runtime\node;%HERMES_ROOT%\runtime\portable-python;%HERMES_ROOT%\hermes-agent\venv\Scripts;%PATH%"

if not exist "%DESKTOP_EXE%" (
    echo [FATAL] Hermes Desktop not found: %DESKTOP_EXE%
    echo         Build: cd hermes-agent ^&^& hermes desktop --build-only
    exit /b 1
)
if not exist "%HERMES_DESKTOP_PYTHON%" (
    echo [FATAL] venv Python not found: %HERMES_DESKTOP_PYTHON%
    exit /b 1
)

if not exist "%HERMES_HOME%\logs" mkdir "%HERMES_HOME%\logs"
start "" /B wscript.exe "%~dp0launch-hidden.vbs" "cmd /c ""%DESKTOP_EXE%"" > ""%HERMES_HOME%\logs\desktop-stdout.log"" 2>&1"
exit /b 0
