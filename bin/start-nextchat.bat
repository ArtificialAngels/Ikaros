@echo off
REM ============================================================
REM Hermes - Start ChatGPT-Next-Web
REM Port 7890 — connects to llama-server on :8080/v1
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "NEXTCHAT_DIR=%HERMES_ROOT%\hermes\data\nextchat"
set "NODE=%HERMES_ROOT%\runtime\node\node.exe"
set "NEXTCHAT_PORT=7890"
set "LLAMA_PORT=8080"

cd /d "%NEXTCHAT_DIR%"

echo ============================================================
echo   ChatGPT-Next-Web
echo.
echo   URL:     http://localhost:%NEXTCHAT_PORT%
echo   API:     http://127.0.0.1:%LLAMA_PORT%/v1
echo.
echo   Configure in settings gear:
echo     Custom Endpoint: http://127.0.0.1:%LLAMA_PORT%
echo     API Key: sk-no-key-needed
echo ============================================================
echo.

set "PATH=%HERMES_ROOT%\runtime\node;%PATH%"
set "PORT=%NEXTCHAT_PORT%"
set "HOSTNAME=127.0.0.1"

if exist ".next\standalone\server.js" (
    echo   Starting production server...
    copy /Y ".next\static" ".next\standalone\.next\static\" >nul 2>&1
    start "Hermes-NextChat" /MIN "%NODE%" ".next\standalone\server.js"
) else (
    echo   Starting development server...
    start "Hermes-NextChat" /MIN cmd /c "set PATH=%HERMES_ROOT%\runtime\node;%%PATH%% && set PORT=%NEXTCHAT_PORT% && npx next dev -p %NEXTCHAT_PORT% -H 127.0.0.1"
)

echo.
echo   (First start takes 5-10 seconds. Open browser when ready.)
echo.
start "" "http://localhost:%NEXTCHAT_PORT%"
endlocal
