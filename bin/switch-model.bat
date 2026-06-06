@echo off
REM ============================================================
REM Hermes - Hot-Switch the active LLM model
REM
REM Usage:
REM     bin\switch-model.bat                       (list available models)
REM     bin\switch-model.bat Qwen2.5-3B-Instruct-Q4_K_M.gguf
REM     bin\switch-model.bat Qwen2.5-7B-Instruct-Q4_K_M.gguf
REM     bin\switch-model.bat Qwen1.5-1.8B-Chat-Q4_K_M.gguf
REM     bin\switch-model.bat Qwen3.5-35B-A3B-Q4_K_M.gguf
REM
REM What it does:
REM   1. Stops the running llama-server (frees the GGUF + VRAM).
REM   2. Restarts llama-server with the new model (using smart NGL).
REM   3. Updates Open WebUI model list via Admin API (no restart needed).
REM   4. Refresh browser page to see the new model.
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "MODELS_DIR=%HERMES_ROOT%\data\models"
set "PY=%HERMES_ROOT%\portable-python\python.exe"
set "LLAMA_PORT=8080"
set "WEBUI_PORT=7870"

set "NEW_MODEL=%~1"

REM ---- If no argument, list available models ----
if "%NEW_MODEL%"=="" (
    echo ============================================================
    echo   Hermes - Available Models
    echo ============================================================
    echo.
    if not exist "%MODELS_DIR%" (
        echo   Models directory not found: %MODELS_DIR%
        exit /b 1
    )
    set /a "IDX=0"
    for %%F in ("%MODELS_DIR%\*.gguf") do (
        set /a "IDX+=1"
        for /f "tokens=*" %%S in ('powershell -NoProfile -Command "$s = (Get-Item -LiteralPath '%%F').Length; [math]::Round($s/1GB,2)"') do set "SZ_GB=%%S"
        echo   [!IDX!] %%~nxf
        echo        Size: !SZ_GB! GB
    )
    echo.
    echo   Usage: bin\switch-model.bat ^<model-filename^>
    echo   Example: bin\switch-model.bat Qwen2.5-3B-Instruct-Q4_K_M.gguf
    echo ============================================================
    exit /b 0
)

REM ---- Validate model exists ----
set "NEW_MODEL_PATH=%MODELS_DIR%\%NEW_MODEL%"
if not exist "%NEW_MODEL_PATH%" (
    echo [ERROR] Model not found: %NEW_MODEL_PATH%
    echo Run without arguments to see available models.
    exit /b 1
)

REM ---- Get readable model name ----
for %%F in ("%NEW_MODEL%") do set "MODEL_DISPLAY=%%~nF"
set "MODEL_DISPLAY=!MODEL_DISPLAY:.=_!"
for /f "tokens=*" %%S in ('powershell -NoProfile -Command "$s = (Get-Item -LiteralPath '%NEW_MODEL_PATH%').Length; [math]::Round($s/1GB,2)"') do set "SIZE_GB=%%S"

echo ============================================================
echo   Hermes - Hot-Switch Model
echo.
echo   New model: %MODEL_DISPLAY% ^(!SIZE_GB! GB^)
echo ============================================================
echo.

REM ---- Step 1: Stop current llama-server ----
echo [1/4] Stopping current llama-server...
powershell -NoProfile -Command "Get-Process -Name 'llama-server*' -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep -Seconds 3" >nul 2>&1
echo   stopped.

REM ---- Step 2: Start new llama-server with smart NGL ----
echo.
echo [2/4] Starting new model with GPU acceleration...
set "LLAMA_MODEL=%NEW_MODEL_PATH%"
start "Hermes-LLM" /MIN cmd /c ""%HERMES_ROOT%\bin\start-llm-smart.bat""

REM ---- Step 3: Wait for llama-server to be ready ----
echo.
echo [3/4] Waiting for model to load...
set /a "WAITED=0"
:wait
timeout /t 3 /nobreak >nul
set /a "WAITED+=3"
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%LLAMA_PORT%/health' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo   model ready in %WAITED%s
    goto :update_webui
)
if %WAITED% GEQ 180 (
    echo   [WARN] not ready after 180s; check data\logs\llm-server.err
    goto :update_webui
)
if %WAITED% EQU 30 echo   still loading...
if %WAITED% EQU 60 echo   still loading... (large model can take a while)
if %WAITED% EQU 120 echo   still loading...
goto :wait

REM ---- Step 4: Update Open WebUI model list via Admin API ----
:update_webui
echo.
echo [4/4] Updating Open WebUI model list...

REM Use bootstrap script to re-register model
set "WEBUI_URL=http://127.0.0.1:%WEBUI_PORT%"
set "OW_DATA_DIR=%HERMES_ROOT%\hermes\data\openwebui"
set "WIPE=0"
set "BOOTSTRAP_LOG=%HERMES_ROOT%\hermes\data\logs\bootstrap.log"
set "ADMIN_EMAIL=admin@hermes.local"
set "ADMIN_PASSWORD=hermes123"

REM Derive model alias from filename (same as start-llm-smart.bat)
set "HERMES_MODEL_ALIAS=!MODEL_DISPLAY!"

REM Try to update WebUI (if it's running)
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://127.0.0.1:%WEBUI_PORT%/health' -UseBasicParsing -TimeoutSec 3).StatusCode } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo   [INFO] Open WebUI not running - model will appear on next start
    goto :done
)

"%PY%" "%HERMES_ROOT%\hermes\scripts\bootstrap_openwebui.py" 2>&1
echo   WebUI model list updated.

:done
echo.
echo ============================================================
echo   Switched to: %MODEL_DISPLAY% ^(!SIZE_GB! GB^)
echo.
echo   The new model is now available in Open WebUI.
echo   Refresh your browser page to see it in the dropdown.
echo.
echo   To verify GPU acceleration:
echo       bin\gpu-detect.bat models
echo ============================================================
echo.
endlocal
exit /b 0
