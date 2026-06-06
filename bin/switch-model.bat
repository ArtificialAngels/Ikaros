@echo off
REM ============================================================
REM Hermes - Switch the active LLM model
REM
REM Usage:
REM     bin\switch-model.bat                       (use default Qwen3.6.gguf)
REM     bin\switch-model.bat Qwen2.5-3B-Instruct-Q4_K_M.gguf
REM     bin\switch-model.bat Qwen2.5-7B-Instruct-Q4_K_M.gguf
REM     bin\switch-model.bat Qwen1.5-1.8B-Chat-Q4_K_M.gguf
REM
REM What it does:
REM   1. Stops the running llama-server (frees the GGUF + VRAM).
REM   2. Restarts llama-server with the new model (using smart NGL).
REM   3. Tells you to restart Open WebUI so it picks up the new alias.
REM
REM Each llama-server process loads exactly one GGUF — Open WebUI's model
REM dropdown shows only what llama-server is currently serving. To switch
REM models, the LLM process has to be replaced. This bat does that.
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "MODELS_DIR=%HERMES_ROOT%\data\models"
set "PY=%HERMES_ROOT%\portable-python\python.exe"
set "LLAMA_PORT=8080"

set "NEW_MODEL=%~1"
if "%NEW_MODEL%"=="" set "NEW_MODEL=Qwen3.5-35B-A3B-Q4_K_M.gguf"
set "NEW_MODEL_PATH=%MODELS_DIR%\%NEW_MODEL%"

if not exist "%NEW_MODEL_PATH%" (
    echo [ERROR] Model not found: %NEW_MODEL_PATH%
    echo.
    echo Available models in %MODELS_DIR%:
    for %%F in ("%MODELS_DIR%\*.gguf") do (
        set "SZ=%%~zF"
        echo   %%~nxf  ^(!SZ! bytes^)
    )
    exit /b 1
)

echo ============================================================
echo   Hermes - Switch Model
echo.
echo   New model: %NEW_MODEL%
echo ============================================================
echo.

echo [1/3] Stopping current llama-server...
powershell -NoProfile -Command "Get-Process -Name 'llama-server*' -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep -Seconds 2" >nul 2>&1
echo   stopped.

echo.
echo [2/3] Starting llama-server with new model...
set "LLAMA_MODEL=%NEW_MODEL_PATH%"
start "Hermes-LLM" /MIN cmd /c ""%HERMES_ROOT%\bin\start-llm-smart.bat""

echo.
echo [3/3] Waiting for new model to load...
set /a "WAITED=0"
:wait
timeout /t 3 /nobreak >nul
set /a "WAITED+=3"
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%LLAMA_PORT%/health' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo   ready in %WAITED%s
    goto :done
)
if %WAITED% GEQ 180 (
    echo   [WARN] not ready after 180s; check data\logs\llm-server.err
    goto :done
)
if %WAITED% GEQ 60 if %WAITED% LSS 63 echo   still loading... (large model can take a while)
goto :wait

:done
echo.
echo ============================================================
echo   Switched to: %NEW_MODEL%
echo.
echo   IMPORTANT: Open WebUI caches the model list at startup.
echo   To see the new model in the dropdown, restart it:
echo       bin\hermes-stop.bat
echo       bin\hermes-web.bat
echo   (Or just run bin\hermes-all.bat again — it will use the
echo   new model everywhere.)
echo ============================================================
echo.
endlocal
exit /b 0
