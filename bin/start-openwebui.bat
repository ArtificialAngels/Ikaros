@echo off
REM ============================================================
REM Open WebUI - Chat UI for local llama-server
REM v2: portable, talks to local llama-server
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "PY=%HERMES_ROOT%\portable-python\python.exe"
set "LLAMA_PORT=8080"
set "WEBUI_PORT=7870"
set "DATA_DIR=%HERMES_ROOT%\hermes\data\openwebui"
set "KEY_FILE=%DATA_DIR%\.webui_secret_key"

REM Create data dir
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"

REM Persist or create WEBUI_SECRET_KEY (Open WebUI requires it)
if not exist "%KEY_FILE%" (
    REM Generate a long random key using PowerShell
    for /f "delims=" %%K in ('powershell -NoProfile -Command "[System.Web.Security.Membership]::GeneratePassword(48, 0)"') do set "WEBUI_SECRET_KEY=%%K"
    echo %WEBUI_SECRET_KEY%> "%KEY_FILE%"
) else (
    set /p WEBUI_SECRET_KEY=< "%KEY_FILE%"
)

echo ============================================================
echo   Open WebUI - Hermes Edition
echo.
echo   Chat UI:    http://localhost:%WEBUI_PORT%
echo   LLM API:    http://127.0.0.1:%LLAMA_PORT%  (llama-server)
echo   Data dir:   %DATA_DIR%
echo ============================================================
echo.

REM Open WebUI env vars
set "DATA_DIR=%DATA_DIR%"
set "PORT=%WEBUI_PORT%"
set "HOST=127.0.0.1"
set "WEBUI_AUTH=false"
set "WEBUI_NAME=Hermes Agent"
set "ENABLE_SIGNUP=true"
REM DEFAULT_MODELS should match the alias served by llama-server. The smart
REM launcher aliases the model from its filename (e.g. Qwen3.6.gguf -> qwen3.6).
REM Override with the HERMES_MODEL_ALIAS env var if you're running a custom one.
if "%HERMES_MODEL_ALIAS%"=="" set "HERMES_MODEL_ALIAS=qwen2.5-7b-instruct"
set "DEFAULT_MODELS=%HERMES_MODEL_ALIAS%"
set "DEFAULT_USER_ROLE=user"

REM === Force Open WebUI to ignore any system Ollama - use ONLY our llama-server ===
set "ENABLE_OLLAMA_API=false"
set "OLLAMA_BASE_URL="
set "ENABLE_OPENAI_API=true"
set "OPENAI_API_BASE_URL=http://127.0.0.1:%LLAMA_PORT%/v1"
set "OPENAI_API_KEY=sk-no-key-needed"

REM RAG embedding: use our hermes FastAPI (hash vectors, no model needed)
set "RAG_EMBEDDING_ENGINE=openai"
set "RAG_EMBEDDING_MODEL=nomic-embed"
set "RAG_OPENAI_API_BASE_URL=http://127.0.0.1:7860/v1"
set "RAG_OPENAI_API_KEY=sk-no-key-needed"

REM Disable telemetry and updates for offline use
set "SCARF_NO_ANALYTICS=true"
set "DO_NOT_TRACK=true"
set "ENABLE_VERSION_UPDATE_CHECK=false"
set "ENABLE_REVERSE_PROXY=false"

REM Start in foreground using typer app (avoids __main__ issue)
"%PY%" -c "from open_webui import app; import sys; sys.argv=['open-webui','serve','--host','127.0.0.1','--port','%WEBUI_PORT%']; app()"

endlocal
