@echo off
REM ============================================================
REM Hermes - Start ChatGPT-Next-Web
REM Port 7890 — auto-configured for local llama-server only
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "NEXTCHAT_DIR=%HERMES_ROOT%\hermes\data\nextchat"
set "NODE=%HERMES_ROOT%\runtime\node\node.exe"
set "PY=%HERMES_ROOT%\portable-python\python.exe"
set "NEXTCHAT_PORT=7890"
set "LLAMA_PORT=8080"

cd /d "%NEXTCHAT_DIR%"

REM ---- Auto-configure: hide cloud models, show only local llama-server models ----
echo Configuring NextChat for local models...
"%PY%" "%HERMES_ROOT%\hermes\scripts\setup_nextchat_config.py" 2>nul

echo ============================================================
echo   ChatGPT-Next-Web
echo.
echo   URL:     http://localhost:%NEXTCHAT_PORT%
echo   API:     http://127.0.0.1:%LLAMA_PORT%/v1
echo.
echo   Only local llama-server models will appear.
echo   Switch models via Settings gear or bin\switch-model.bat
echo ============================================================
echo.

set "PATH=%HERMES_ROOT%\runtime\node;%PATH%"
set "PORT=%NEXTCHAT_PORT%"
set "HOSTNAME=127.0.0.1"

REM Copy static files into standalone dir
if exist ".next\static" (
    xcopy /E /I /Y ".next\static" ".next\standalone\.next\static\" >nul 2>&1
)
if exist "public" (
    xcopy /E /I /Y "public" ".next\standalone\public\" >nul 2>&1
)

if exist ".next\standalone\server.js" (
    echo   Starting production server...
    start "Hermes-NextChat" /MIN "%NODE%" ".next\standalone\server.js"
) else (
    echo   Starting development server...
    start "Hermes-NextChat" /MIN cmd /c "set PATH=%HERMES_ROOT%\runtime\node;%%PATH%% && set PORT=%NEXTCHAT_PORT% && npx next dev -p %NEXTCHAT_PORT% -H 127.0.0.1"
)

echo.
echo   Ready in ~5s. Only local models shown in dropdown.
start "" "http://localhost:%NEXTCHAT_PORT%"
endlocal
