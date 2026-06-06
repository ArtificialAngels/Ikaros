@echo off
REM ============================================================
REM Hermes - Hot-Switch the active LLM model (v2)
REM
REM Usage:
REM     bin\switch-model.bat                       (list available models)
REM     bin\switch-model.bat Qwen2.5-3B-Instruct-Q4_K_M.gguf
REM     bin\switch-model.bat Qwen1.5-1.8B-Chat-Q4_K_M.gguf
REM
REM What it does:
REM   1. Stops llama-server (frees GGUF + VRAM).
REM   2. Restarts llama-server with new model (smart NGL).
REM   3. Updates Hermes API + Open WebUI model list.
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "MODELS_DIR=%HERMES_ROOT%\data\models"
set "PY=%HERMES_ROOT%\portable-python\python.exe"
set "LLAMA_PORT=8080"
set "WEBUI_PORT=7870"
set "HERMES_PORT=7860"

set "NEW_MODEL=%~1"

REM ---- List models if no argument ----
if "%NEW_MODEL%"=="" (
    echo ============================================================
    echo   Hermes - Available Models
    echo ============================================================
    echo.
    set /a "IDX=0" & set /a "TOTAL=0"
    for %%F in ("%MODELS_DIR%\*.gguf") do set /a "TOTAL+=1"
    for %%F in ("%MODELS_DIR%\*.gguf") do (
        set /a "IDX+=1"
        for /f "tokens=*" %%S in ('powershell -NoProfile -Command "$s=(Get-Item '%%F').Length;[math]::Round($s/1GB,2)"') do set "SZ=%%S"
        echo   [!IDX!/!TOTAL!] %%~nxf  ^(^!SZ^! GB^)
    )
    echo.
    echo   Usage:  bin\switch-model.bat ^<model-filename^>
    echo ============================================================
    exit /b 0
)

REM ---- Validate ----
set "NEW_MODEL_PATH=%MODELS_DIR%\%NEW_MODEL%"
if not exist "%NEW_MODEL_PATH%" (
    echo [ERROR] Model not found: %NEW_MODEL_PATH%
    echo Run without arguments to see available models.
    exit /b 1
)

for %%F in ("%NEW_MODEL%") do set "MODEL_DISPLAY=%%~nF"
for /f "tokens=*" %%S in ('powershell -NoProfile -Command "$s=(Get-Item '%NEW_MODEL_PATH%').Length;[math]::Round($s/1GB,2)"') do set "SIZE_GB=%%S"

echo ============================================================
echo   Hermes - Hot-Switch Model
echo.
echo   Target: %MODEL_DISPLAY% ^(!SIZE_GB! GB^)
echo ============================================================
echo.

REM ---- Step 1: Stop all LLM processes ----
echo [1/3] Stopping llama-server...
powershell -NoProfile -Command "Get-Process -Name 'llama-server*' -ErrorAction SilentlyContinue | Stop-Process -Force" >nul 2>&1
timeout /t 2 /nobreak >nul
echo   stopped.

REM ---- Step 2: Start new model with smart NGL ----
echo.
echo [2/3] Starting new model with GPU acceleration...
set "LLAMA_MODEL=%NEW_MODEL_PATH%"
start "Hermes-LLM" /MIN cmd /c ""%HERMES_ROOT%\bin\start-llm-smart.bat""

REM ---- Step 3: Wait for it to be ready ----
echo.
echo [3/3] Waiting for model to load...
set /a "WAITED=0"
:wait
timeout /t 3 /nobreak >nul
set /a "WAITED+=3"
powershell -NoProfile -Command "try {$r=Invoke-WebRequest -Uri 'http://127.0.0.1:%LLAMA_PORT%/health' -UseBasicParsing -TimeoutSec 2; if($r.StatusCode -eq 200){exit 0}else{exit 1}}catch{exit 1}" >nul 2>&1
if not errorlevel 1 (
    echo   ready in %WAITED%s
    goto :update_webui
)
if %WAITED% GEQ 180 (
    echo   [WARN] not ready after 180s
    goto :done
)
if %WAITED% EQU 30 echo   still loading...
if %WAITED% EQU 60 echo   still loading...
if %WAITED% EQU 120 echo   still loading...
goto :wait

REM ---- Update WebUI model list ----
:update_webui
echo.
echo   Updating Open WebUI model list...
set "WEBUI_URL=http://127.0.0.1:%WEBUI_PORT%"
set "OW_DATA_DIR=%HERMES_ROOT%\hermes\data\openwebui"
set "WIPE=0"
set "BOOTSTRAP_LOG=%HERMES_ROOT%\hermes\data\logs\bootstrap.log"
set "ADMIN_EMAIL=admin@hermes.local"
set "ADMIN_PASSWORD=hermes123"
set "HERMES_MODEL_ALIAS=%MODEL_DISPLAY:.=_%"
set "HERMES_MODEL_ALIAS=!HERMES_MODEL_ALIAS:-=!"

powershell -NoProfile -Command "try {(Invoke-WebRequest -Uri 'http://127.0.0.1:%WEBUI_PORT%/health' -UseBasicParsing -TimeoutSec 3).StatusCode}catch{exit 1}" >nul 2>&1
if not errorlevel 1 (
    "%PY%" "%HERMES_ROOT%\hermes\scripts\bootstrap_openwebui.py" >nul 2>&1
    echo   WebUI model list updated.
) else (
    echo   (Open WebUI not running - model will appear on next start)
)

:done
echo.
echo ============================================================
echo   Switched to: %MODEL_DISPLAY% ^(!SIZE_GB! GB^)
echo.
echo   Chat UI:  http://localhost:%WEBUI_PORT%  (Open WebUI)
echo             http://localhost:%HERMES_PORT%/chat  (built-in)
echo.
echo   Model is now live. Refresh your browser to use it.
echo ============================================================
echo.
endlocal
exit /b 0
