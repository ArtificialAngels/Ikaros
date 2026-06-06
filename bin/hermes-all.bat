@echo off
REM ============================================================
REM Hermes - One-click Launcher
REM v5: llama-server + Hermes API (Chat Pro built-in)
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "LLAMA_PORT=8080"
set "HERMES_PORT=7860"
set "PY=%HERMES_ROOT%\portable-python\python.exe"

REM ---- Smart default model selection (auto-detect VRAM) ----
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
        echo [auto] VRAM=!VRAM_CHECK!MB - using Qwen2.5 7B (GPU accelerated)
    ) else if !VRAM_CHECK! GEQ 3000 (
        set "MODEL=%HERMES_ROOT%\data\models\Qwen2.5-3B-Instruct-Q4_K_M.gguf"
        echo [auto] VRAM=!VRAM_CHECK!MB - using Qwen2.5 3B (GPU accelerated)
    ) else (
        set "MODEL=%HERMES_ROOT%\data\models\Qwen1.5-1.8B-Chat-Q4_K_M.gguf"
        echo [auto] VRAM=!VRAM_CHECK!MB - using Qwen1.5 1.8B (GPU accelerated)
    )
)
if not exist "%MODEL%" (
    echo [WARN] configured model not found: %MODEL%
    for %%F in ("%HERMES_ROOT%\data\models\*.gguf") do (
        set "MODEL=%%F"
        echo [auto] falling back to: %%~nxf
        goto :model_found
    )
    echo [ERROR] No .gguf model found in data\models\
    pause
    exit /b 1
)
:model_found

REM ---- Auto-pick llama-server binary ----
set "LLAMA_BIN=%HERMES_ROOT%\runtime\llama-server.exe"
set "GPU_MODE=CPU"
if exist "%HERMES_ROOT%\runtime\llama-server-cuda-12.4.exe" (
    set "LLAMA_BIN=%HERMES_ROOT%\runtime\llama-server-cuda-12.4.exe"
    set "GPU_MODE=CUDA 12.4"
) else if exist "%HERMES_ROOT%\runtime\llama-server-cuda-11.8.exe" (
    set "LLAMA_BIN=%HERMES_ROOT%\runtime\llama-server-cuda-11.8.exe"
    set "GPU_MODE=CUDA 11.8"
) else if exist "%HERMES_ROOT%\runtime\llama-server-vulkan.exe" (
    set "LLAMA_BIN=%HERMES_ROOT%\runtime\llama-server-vulkan.exe"
    set "GPU_MODE=Vulkan"
)

echo ============================================================
echo   Hermes - All-in-One Launcher
echo.
echo   Chat UI:    http://localhost:%HERMES_PORT%/chat
echo   Launcher:   http://localhost:%HERMES_PORT%/launcher
echo   API:        http://localhost:%HERMES_PORT%/status
echo   LLM:        http://127.0.0.1:%LLAMA_PORT%  ^(llama-server^)
echo   GPU mode:   %GPU_MODE%
echo ============================================================
echo.

REM ---- Step 0: Environment check ----
echo [0/3] Environment check...
call "%HERMES_ROOT%\bin\hermes-firstrun.bat" auto
if errorlevel 1 (
    echo   [WARN] environment check found issues - continuing
)

REM ---- Step 1: Start llama-server ----
echo [1/3] Starting llama-server (smart NGL)...
set "LLAMA_MODEL=%MODEL%"
start "Hermes-LLM" /MIN cmd /c ""%HERMES_ROOT%\bin\start-llm-smart.bat""

REM ---- Step 2: Wait for llama-server ----
echo [2/3] Waiting for llama-server...
set /a "WAITED=0"
:wait_llm
timeout /t 3 /nobreak >nul
set /a "WAITED+=3"
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%LLAMA_PORT%/health' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo   llama-server ready in %WAITED%s
    goto :start_hermes
)
if %WAITED% GEQ 120 (
    echo   [WARN] llama-server not ready after 120s
    goto :start_hermes
)
if %WAITED% EQU 15 echo   still loading...
if %WAITED% EQU 30 echo   still loading...
if %WAITED% EQU 60 echo   still loading...
if %WAITED% EQU 90 echo   still loading...
goto :wait_llm

REM ---- Step 3: Start Hermes API ----
:start_hermes
echo [3/3] Starting Hermes API + Chat Pro...
start "Hermes-API" /MIN "%PY%" -m hermes serve --host 127.0.0.1 --port %HERMES_PORT%
set /a "WAITED=0"
:wait_hermes
timeout /t 2 /nobreak >nul
set /a "WAITED+=2"
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://127.0.0.1:%HERMES_PORT%/healthz' -UseBasicParsing -TimeoutSec 2).StatusCode } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo   Hermes ready in %WAITED%s
    goto :done
)
if %WAITED% GEQ 30 (
    echo   [WARN] Hermes not ready, but LLM is up
    goto :done
)
goto :wait_hermes

:done
echo.
echo ============================================================
echo   Ready!
echo.
echo   Chat Pro:  http://localhost:%HERMES_PORT%/chat
echo   Launcher:  http://localhost:%HERMES_PORT%/launcher
echo.
echo   Switch model:  bin\switch-model.bat ^<name^>.gguf
echo   Stop all:      bin\hermes-stop.bat
echo ============================================================
echo.
start "" "http://localhost:%HERMES_PORT%/chat"
echo.
echo (后台窗口保持运行 — 关掉它们可停止服务)
echo.
endlocal
exit /b 0
