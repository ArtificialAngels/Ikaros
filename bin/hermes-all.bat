@echo off
REM ============================================================
REM Hermes - One-click Launcher
REM v4: llama-server + Hermes API + Open WebUI
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "LLAMA_PORT=8080"
set "HERMES_PORT=7860"
set "WEBUI_PORT=7870"
set "PY=%HERMES_ROOT%\portable-python\python.exe"
set "MODEL=%HERMES_ROOT%\data\models\Qwen3.5-35B-A3B-Q4_K_M.gguf"
set "WEBUI_DATA_DIR=%HERMES_ROOT%\hermes\data\openwebui"
set "WEBUI_KEY_FILE=%WEBUI_DATA_DIR%\.webui_secret_key"

REM ---- Auto-pick llama-server binary: CUDA 12.4 > CUDA 11.8 > Vulkan > CPU ----
set "LLAMA_BIN=%HERMES_ROOT%\runtime\llama-server.exe"
set "GPU_LAYERS=0"
set "GPU_MODE=CPU"

if exist "%HERMES_ROOT%\runtime\llama-server-cuda-12.4.exe" (
    set "LLAMA_BIN=%HERMES_ROOT%\runtime\llama-server-cuda-12.4.exe"
    set "GPU_LAYERS=99"
    set "GPU_MODE=CUDA 12.4"
) else if exist "%HERMES_ROOT%\runtime\llama-server-cuda-11.8.exe" (
    set "LLAMA_BIN=%HERMES_ROOT%\runtime\llama-server-cuda-11.8.exe"
    set "GPU_LAYERS=99"
    set "GPU_MODE=CUDA 11.8"
) else if exist "%HERMES_ROOT%\runtime\llama-server-cuda.exe" (
    set "LLAMA_BIN=%HERMES_ROOT%\runtime\llama-server-cuda.exe"
    set "GPU_LAYERS=99"
    set "GPU_MODE=CUDA"
) else if exist "%HERMES_ROOT%\runtime\llama-server-vulkan.exe" (
    set "LLAMA_BIN=%HERMES_ROOT%\runtime\llama-server-vulkan.exe"
    set "GPU_LAYERS=99"
    set "GPU_MODE=Vulkan"
)
REM Note: GPU_LAYERS is just for display. start-llm-smart.bat does the actual
REM smart NGL calculation based on model size + free VRAM.

if not exist "%WEBUI_DATA_DIR%" mkdir "%WEBUI_DATA_DIR%"
if not exist "%WEBUI_KEY_FILE%" (
    for /f "delims=" %%K in ('powershell -NoProfile -Command "[guid]::NewGuid().ToString().Replace('-','')"') do set "WEBUI_KEY=%%K"
    echo %WEBUI_KEY%> "%WEBUI_KEY_FILE%"
) else (
    set /p WEBUI_KEY=< "%WEBUI_KEY_FILE%"
)

echo ============================================================
echo   Hermes - All-in-One Launcher
echo.
echo   Chat UI (Open WebUI):  http://localhost:%WEBUI_PORT%
echo   Hermes API:           http://localhost:%HERMES_PORT%/status
echo   Launcher UI:          http://localhost:%HERMES_PORT%/launcher
echo   LLM engine:           http://127.0.0.1:%LLAMA_PORT%  (llama-server)
echo   LLM mode:             %GPU_MODE%  (gpu-layers=%GPU_LAYERS%)
echo ============================================================
echo.

REM ---- Step 0: First-run environment check (idempotent) ----
REM Detects GPU, downloads missing runtime (cudart etc) via gopeed-web.
REM If download fails, gracefully falls back to CPU. Re-runs are no-ops.
echo [0/5] Environment check (GPU + cudart)...
call "%HERMES_ROOT%\bin\hermes-firstrun.bat" auto
if errorlevel 1 (
    echo   [WARN] environment check found issues - continuing with current state
)

REM ---- Step 1: 启动 llama-server (smart NGL calculation) ----
echo [1/5] Starting llama-server (smart NGL)...
REM Pass the model via env var (LLAMA_MODEL) instead of %1 — cmd's nested-quote
REM parser splits paths with spaces (e.g. "E:\Hermes Agent\..." gets cut at
REM the space, and the first chunk is treated as a command). Env vars survive
REM start /MIN and don't have this issue.
set "LLAMA_MODEL=%MODEL%"
start "Hermes-LLM" /MIN cmd /c ""%HERMES_ROOT%\bin\start-llm-smart.bat""

REM ---- Step 2: 等待 llama-server ----
echo [2/5] Waiting for llama-server (first run 30-60s)...
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
if %WAITED% EQU 60 echo   still loading... (large model needs time)
if %WAITED% EQU 90 echo   still loading...
goto :wait_llm

:start_hermes
REM ---- Step 3: 启动 Hermes API（提供 /v1/embeddings 给 Open WebUI RAG）----
echo [3/5] Starting Hermes API (embeddings for RAG)...
start "Hermes-API" /MIN "%PY%" -m hermes serve --host 127.0.0.1 --port %HERMES_PORT%
set /a "WAITED=0"
:wait_hermes
timeout /t 2 /nobreak >nul
set /a "WAITED+=2"
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://127.0.0.1:%HERMES_PORT%/healthz' -UseBasicParsing -TimeoutSec 2).StatusCode } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo   Hermes ready in %WAITED%s
    goto :start_webui
)
if %WAITED% GEQ 30 (
    echo   [WARN] Hermes not ready, but LLM is up
    goto :start_webui
)
goto :wait_hermes

:start_webui
REM ---- Step 4: 启动 Open WebUI ----
echo [4/5] Starting Open WebUI (chat UI)...
set "DATA_DIR=%WEBUI_DATA_DIR%"
set "PORT=%WEBUI_PORT%"
set "HOST=127.0.0.1"
set "WEBUI_NAME=Hermes Agent"
set "ENABLE_SIGNUP=true"
set "ENABLE_INITIAL_ADMIN_SIGNUP=true"
REM DEFAULT_MODELS = the alias llama-server exposes. The smart launcher
REM derives the alias from the model filename (Qwen3.6.gguf -> qwen3.6),
REM so we extract it here too. Override with HERMES_MODEL_ALIAS env var.
if "%HERMES_MODEL_ALIAS%"=="" (
    for %%F in ("%MODEL%") do set "HERMES_MODEL_ALIAS=%%~nF"
    set "HERMES_MODEL_ALIAS=!HERMES_MODEL_ALIAS:.=_!"
)
set "DEFAULT_MODELS=%HERMES_MODEL_ALIAS%"
set "DEFAULT_USER_ROLE=user"
REM === Force Open WebUI to ignore any system Ollama - use ONLY our llama-server ===
set "ENABLE_OLLAMA_API=false"
set "OLLAMA_BASE_URL="
set "ENABLE_OPENAI_API=true"
set "OPENAI_API_BASE_URL=http://127.0.0.1:%LLAMA_PORT%/v1"
set "OPENAI_API_KEY=sk-no-key-needed"
set "RAG_EMBEDDING_ENGINE=openai"
set "RAG_EMBEDDING_MODEL=nomic-embed"
set "RAG_OPENAI_API_BASE_URL=http://127.0.0.1:%HERMES_PORT%/v1"
set "RAG_OPENAI_API_KEY=sk-no-key-needed"
set "SCARF_NO_ANALYTICS=true"
set "DO_NOT_TRACK=true"
set "ENABLE_VERSION_UPDATE_CHECK=false"
set "ENABLE_REVERSE_PROXY=false"
set "WEBUI_SECRET_KEY=%WEBUI_KEY%"

start "Hermes-WebUI" /MIN "%PY%" -c "from open_webui import app; import sys; sys.argv=['open-webui','serve','--host','127.0.0.1','--port','%WEBUI_PORT%']; app()"

set /a "WAITED=0"
:wait_webui
timeout /t 2 /nobreak >nul
set /a "WAITED+=2"
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%WEBUI_PORT%/health' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo   Open WebUI ready in %WAITED%s
    goto :bootstrap
)
if %WAITED% GEQ 60 (
    echo   [WARN] Open WebUI not ready after 60s
    goto :open_browser
)
goto :wait_webui

:bootstrap
REM ---- Step 4.5: 自动 bootstrap (signup + add model) ----
echo   Bootstrapping admin + model...
set "WEBUI_URL=http://127.0.0.1:%WEBUI_PORT%"
set "OW_DATA_DIR=%WEBUI_DATA_DIR%"
set "WIPE=0"
set "BOOTSTRAP_LOG=%HERMES_ROOT%\hermes\data\logs\bootstrap.log"
set "ADMIN_EMAIL=admin@hermes.local"
set "ADMIN_PASSWORD=hermes123"
"%PY%" "%HERMES_ROOT%\hermes\scripts\bootstrap_openwebui.py"
echo   Bootstrap done (see %BOOTSTRAP_LOG%)

:open_browser
REM ---- Step 5: 打开浏览器 ----
echo.
echo [5/5] Opening browser...
echo.
echo ============================================================
echo   Ready!
echo.
echo   Chat UI:        http://localhost:%WEBUI_PORT%
echo   Login:          admin@hermes.local  /  hermes123
echo   Hermes API:     http://localhost:%HERMES_PORT%/status
echo.
echo   Model already added. Just pick "%HERMES_MODEL_ALIAS% (Local)" and chat.
echo.
echo   要切换到其它模型:  bin\switch-model.bat "<name>.gguf"
echo.
echo   要重置 Open WebUI（清空用户和聊天记录）:
echo     删掉 %WEBUI_DATA_DIR%\webui.db
echo     重新跑 hermes-all.bat
echo ============================================================
echo.

start "" "http://localhost:%WEBUI_PORT%"

echo.
echo (三个后台窗口会保持运行 - 关掉它们可停止服务)
echo.
endlocal
exit /b 0
