@echo off
REM ============================================================
REM Hermes - One-click Launcher v10
REM llama-server + Hermes API (:7860) + Hermes WebUI (:8648)
REM Browser opens to new WebUI at :8648
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "LLAMA_PORT=8080"
set "HERMES_PORT=7860"
set "WEBUI_PORT=8648"
set "PY=%HERMES_ROOT%\portable-python\python.exe"

REM ---- Kill previous instances ----
echo [pre] Stopping old instances...
taskkill /F /IM "llama-server.exe" /T >nul 2>&1
taskkill /F /IM "llama-server-cuda-12.4.exe" /T >nul 2>&1
taskkill /F /IM "llama-server-cuda-11.8.exe" /T >nul 2>&1
taskkill /F /IM "llama-server-vulkan.exe" /T >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | Where-Object { $_.CommandLine -match 'hermes' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
taskkill /F /IM "gopeed-web.exe" /T >nul 2>&1
timeout /t 2 /nobreak >nul

REM ---- Smart default model selection ----
if not "%HERMES_MODEL%"=="" set "MODEL=%HERMES_MODEL%"
if not "%~1"=="" set "MODEL=%~1"
if "%MODEL%"=="" (
    set "VRAM_CHECK=0"
    where nvidia-smi >nul 2>&1
    if not errorlevel 1 (
        for /f "usebackq tokens=*" %%V in (`powershell -NoProfile -Command "$f = (& nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>$null) -split '\n' | Select-Object -First 1; [int]$f"`) do (
            set "VRAM_CHECK=%%V"
        )
    )
    if !VRAM_CHECK! GEQ 8000 (
        set "MODEL=%HERMES_ROOT%\data\models\Qwen2.5-7B-Instruct-Q4_K_M.gguf"
        echo [auto] VRAM=!VRAM_CHECK!MB - 7B GPU
    ) else if !VRAM_CHECK! GEQ 3000 (
        set "MODEL=%HERMES_ROOT%\data\models\Qwen2.5-3B-Instruct-Q4_K_M.gguf"
        echo [auto] VRAM=!VRAM_CHECK!MB - 3B GPU
    ) else (
        set "MODEL=%HERMES_ROOT%\data\models\Qwen2.5-3B-Instruct-Q4_K_M.gguf"
        echo [auto] VRAM=!VRAM_CHECK!MB - 3B (min)
    )
)
if not exist "%MODEL%" (
    echo [WARN] model not found: %MODEL%
    for %%F in ("%HERMES_ROOT%\data\models\*.gguf") do (
        set "MODEL=%%F"
        echo [auto] fallback: %%~nxf
        goto :model_found
    )
    echo [ERROR] No .gguf model in data\models\
    pause
    exit /b 1
)
:model_found

REM ---- Auto-pick llama-server binary ----
set "GPU_MODE=CPU"
if exist "%HERMES_ROOT%\runtime\llama-server-cuda-12.4.exe" set "GPU_MODE=CUDA 12.4"
if exist "%HERMES_ROOT%\runtime\llama-server-cuda-11.8.exe" set "GPU_MODE=CUDA 11.8"
if exist "%HERMES_ROOT%\runtime\llama-server-vulkan.exe" set "GPU_MODE=Vulkan"

echo ============================================================
echo   Hermes - All-in-One Launcher
echo.
echo   New WebUI: http://localhost:%WEBUI_PORT%/
echo   API:       http://localhost:%HERMES_PORT%/api/status
echo   LLM:       http://127.0.0.1:%LLAMA_PORT%  (llama-server)
echo   Console:    bin\hermes-console.bat
echo   Trace:      bin\hermes-trace.bat
echo   Model Run:  bin\hermes-model-run.bat
echo   GPU:        %GPU_MODE%
echo ============================================================
echo.

REM ---- Step 0: Environment check ----
echo [0/7] Environment check...
call "%HERMES_ROOT%\bin\hermes-firstrun.bat" auto 2>nul

REM ---- Step 1: Start llama-server ----
echo [1/7] Starting llama-server (smart NGL)...
set "LLAMA_MODEL=%MODEL%"
start "Hermes-LLM" /MIN cmd /c ""%HERMES_ROOT%\bin\start-llm-smart.bat""

REM ---- Wait for llama-server ----
echo [2/7] Waiting for llama-server...
set /a "WAITED=0"
:wait_llm
timeout /t 3 /nobreak >nul
set /a "WAITED+=3"
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%LLAMA_PORT%/health' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo   llama-server ready in %WAITED%s
    goto :start_hermes
)
if %WAITED% GEQ 120 goto :start_hermes
if %WAITED% EQU 30 echo   still loading...
if %WAITED% EQU 60 echo   still loading...
goto :wait_llm

REM ---- Step 2: Start Hermes API ----
:start_hermes
echo [3/7] Starting Hermes API...
start "Hermes-API" /MIN "%PY%" -m hermes serve --host 127.0.0.1 --port %HERMES_PORT%
set /a "WAITED=0"
:wait_hermes
timeout /t 2 /nobreak >nul
set /a "WAITED+=2"
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://127.0.0.1:%HERMES_PORT%/healthz' -UseBasicParsing -TimeoutSec 2).StatusCode } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo   Hermes API ready in %WAITED%s
    goto :start_webui
)
if %WAITED% GEQ 60 goto :start_webui
if %WAITED% EQU 30 echo   still loading...
goto :wait_hermes

REM ---- Step 3: Start new Hermes WebUI (:8648) ----
:start_webui
echo [4/7] Starting new Hermes WebUI at :%WEBUI_PORT%...
set "HERMES_WEB_UI_NO_BROWSER=1"
call "%HERMES_ROOT%\bin\webui-new.bat" start
set /a "WAITED=0"
:wait_webui
timeout /t 2 /nobreak >nul
set /a "WAITED+=2"
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://127.0.0.1:%WEBUI_PORT%/health' -UseBasicParsing -TimeoutSec 2).StatusCode } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo   WebUI ready in %WAITED%s
    goto :start_console
)
if %WAITED% GEQ 30 goto :start_console
goto :wait_webui

REM ---- Step 4: Start Hermes Console (persistent model management) ----
:start_console
echo [5/7] Starting Hermes Console...
start "Hermes-Console" "%HERMES_ROOT%\bin\hermes-console.bat"

REM ---- Step 5: Start Hermes Trace (real-time webui/bridge/agent log viewer) ----
echo [6/7] Starting Hermes Trace...
start "Hermes-Trace" "%HERMES_ROOT%\bin\hermes-trace.bat"

REM ---- Step 6: Start Hermes Model Running (real-time LLM backend log viewer) ----
echo [7/7] Starting Hermes Model Running...
start "Hermes Model Running" "%HERMES_ROOT%\bin\hermes-model-run.bat"

:done
echo.
echo ============================================================
echo   Ready!
echo.
echo   WebUI:  http://localhost:%WEBUI_PORT%/
echo   API:    http://localhost:%HERMES_PORT%/api/status
echo.
echo   Stop:   bin\hermes-stop.bat
echo ============================================================
echo.

REM ---- Open browser to new WebUI ----
echo [*] Opening http://localhost:%WEBUI_PORT%/...
powershell -NoProfile -Command "[System.Diagnostics.Process]::Start('http://localhost:%WEBUI_PORT%/')" >nul 2>&1
if errorlevel 1 explorer "http://localhost:%WEBUI_PORT%/" 2>nul

endlocal
exit /b 0
