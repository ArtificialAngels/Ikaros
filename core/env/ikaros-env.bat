@echo off
REM See docs/scripts/core/env/ikaros-env.md
REM No setlocal: all vars exported to caller.

REM ---- IKAROS_ROOT: auto-detect from script location ----
if defined IKAROS_ROOT goto :root_ok
set "IKAROS_ENV_DIR=%~dp0"
if "%IKAROS_ENV_DIR:~-1%"=="\" set "IKAROS_ENV_DIR=%IKAROS_ENV_DIR:~0,-1%"
for %%I in ("%IKAROS_ENV_DIR%\..") do set "IKAROS_ROOT=%%~fI"
:root_ok
if "%IKAROS_ROOT:~-1%"=="\" set "IKAROS_ROOT=%IKAROS_ROOT:~0,-1%"

REM ---- Core paths ----
set "IKAROS_PYTHON=%IKAROS_ROOT%\runtime\portable-python\python.exe"
set "IKAROS_RUNTIME=%IKAROS_ROOT%\runtime"
set "IKAROS_NODE=%IKAROS_ROOT%\runtime\node\node.exe"
set "IKAROS_DATA=%IKAROS_ROOT%\data"
set "IKAROS_BIN=%IKAROS_ROOT%\bin"
REM ---- ThirdSpace Vault（外部知识库层，thirdspace-bridge skill 用）----
set "THIRDSPACE_VAULT=%IKAROS_ROOT%\data\thirdspace-vault"
set "IKAROS_CONFIG=%IKAROS_ROOT%\config"
set "IKAROS_MODULES=%IKAROS_ROOT%\modules"
set "IKAROS_LOGS=%IKAROS_ROOT%\data\logs"

REM ---- Hermes Agent paths ----
set "IKAROS_HERMES_AGENT=%IKAROS_ROOT%\core/hermes"
set "IKAROS_HERMES_HOME=%IKAROS_ROOT%\data\hermes-agent"

REM Point the Hermes Agent service dependencies at our environment.
set "HERMES_BIN=%IKAROS_HERMES_AGENT%\venv\Scripts\hermes.exe"
set "HERMES_AGENT_CLI_PYTHON=%IKAROS_HERMES_AGENT%\venv\Scripts\python.exe"
set "HERMES_AGENT_BRIDGE_PYTHON=%IKAROS_HERMES_AGENT%\venv\Scripts\python.exe"
set "HERMES_AGENT_NODE=%IKAROS_RUNTIME%\node\node.exe"

REM ---- N.E.K.O Frontend (Electron desktop + FastAPI backend) ----
set "IKAROS_NEKO=%IKAROS_ROOT%\core\neko"
set "IKAROS_NEKO_PYTHON=%IKAROS_NEKO%\.venv\Scripts\python.exe"
set "IKAROS_NEKO_SERVER=app.main_server"
set "IKAROS_NEKO_DESKTOP=%IKAROS_NEKO%\N.E.K.O.exe"
set "IKAROS_NEKO_STATIC=%IKAROS_NEKO%\static"
set "IKAROS_NEKO_TEMPLATES=%IKAROS_NEKO%\templates"
set "IKAROS_NEKO_PORT=48911"

REM ---- Ikaros Memory paths ----
set "IKAROS_MEMORY=%IKAROS_ROOT%\core\memory_v5"
set "IKAROS_MEMORY_DATA=%IKAROS_MEMORY%\data"
set "IKAROS_MEMORY_MODELS=%IKAROS_MEMORY%\models"
set "IKAROS_MEMORY_SCRIPT=%IKAROS_MEMORY%\store.py"

REM ---- N.E.K.O Live2D (取代旧 Ikaros-Live2D) ----
set "IKAROS_LIVE2D=%IKAROS_NEKO%"
set "IKAROS_NODE_MODULES=%IKAROS_RUNTIME%\\node\\node_modules"

REM ---- Portable Rust (standalone, no rustup) ----
set "IKAROS_RUST=%IKAROS_RUNTIME%\rust"

REM ---- llama-server (llama.cpp) ----
if not defined IKAROS_LLAMA_VERSION set "IKAROS_LLAMA_VERSION=b10000-cuda"
set "IKAROS_LLAMA_DIR=%IKAROS_RUNTIME%\llama\%IKAROS_LLAMA_VERSION%"
set "IKAROS_LLAMA_SERVER=%IKAROS_LLAMA_DIR%\llama-server.exe"

REM ---- herdr (agent-aware 终端多路复用器，作为受控引擎接入 Ikaros) ----
set "IKAROS_HERDR=%IKAROS_ROOT%\runtime\herdr\herdr.exe"

REM ---- Model paths ----
set "IKAROS_MODEL_EMBEDDING=%IKAROS_MEMORY_MODELS%\nomic-embed-text-v2-moe.f32.gguf"
REM IKAROS_MODEL_LLM intentionally NOT set here - watchdog picks the default local LLM via resolver

REM ---- Service ports ----
set "IKAROS_PORT_EMBEDDING=8587"
set "IKAROS_PORT_LLM=8080"
set "IKAROS_PORT_BRIDGE=7860"
set "IKAROS_PORT_LIVE2D_WEBVIEW=8648"
set "IKAROS_PORT_LIVE2D_WEBVIEW_INTERNAL=8649"
set "IKAROS_PORT_LLAMA=8080"

REM ---- Python / PATH ----
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHONPATH=%IKAROS_ROOT%;%IKAROS_HERMES_AGENT%"
set "PATH=%IKAROS_RUST%\bin;%IKAROS_LLAMA_DIR%;%IKAROS_ROOT%\runtime\herdr;%IKAROS_RUNTIME%;%IKAROS_RUNTIME%\node;%IKAROS_ROOT%\runtime\portable-python\Scripts;%IKAROS_ROOT%\runtime\portable-python;%PATH%"
set "NODE_PATH=%IKAROS_RUNTIME%\node\node_modules"
set "PYTHONHOME="

REM ---- HERMES_* compat vars (for legacy scripts) ----
set "HERMES_ROOT=%IKAROS_ROOT%"
set "HERMES_HOME=%IKAROS_HERMES_HOME%"
set "HERMES_PYTHON=%IKAROS_PYTHON%"
set "HERMES_RUNTIME=%IKAROS_RUNTIME%"
set "HERMES_AGENT_ROOT=%IKAROS_HERMES_AGENT%"

exit /b 0
