@echo off
REM ============================================================
REM Ikaros Environment - Unified Path Configuration
REM
REM  Sets all IKAROS_* + HERMES_* env vars for portable use.
REM  Called by init.bat — do NOT call directly.
REM  No setlocal: all vars exported to caller.
REM ============================================================

REM ---- IKAROS_ROOT: auto-detect from script location ----
if defined IKAROS_ROOT goto :root_ok
set "IKAROS_ENV_DIR=%~dp0"
if "%IKAROS_ENV_DIR:~-1%"=="\" set "IKAROS_ENV_DIR=%IKAROS_ENV_DIR:~0,-1%"
for %%I in ("%IKAROS_ENV_DIR%\..") do set "IKAROS_ROOT=%%~fI"
:root_ok
if "%IKAROS_ROOT:~-1%"=="\" set "IKAROS_ROOT=%IKAROS_ROOT:~0,-1%"

REM ---- Core paths ----
set "IKAROS_PYTHON=%IKAROS_ROOT%\portable-python\python.exe"
set "IKAROS_RUNTIME=%IKAROS_ROOT%\runtime"
set "IKAROS_NODE=%IKAROS_ROOT%\runtime\node23\node.exe"
set "IKAROS_DATA=%IKAROS_ROOT%\data"
set "IKAROS_BIN=%IKAROS_ROOT%\bin"
set "IKAROS_CONFIG=%IKAROS_ROOT%\config"
set "IKAROS_MODULES=%IKAROS_ROOT%\modules"
set "IKAROS_LOGS=%IKAROS_ROOT%\data\logs"

REM ---- Hermes Agent paths ----
set "IKAROS_HERMES_AGENT=%IKAROS_ROOT%\hermes-agent"
set "IKAROS_HERMES_HOME=%IKAROS_ROOT%\data\hermes-agent"

REM ---- Hermes Studio (hermes-web-ui) frontend ----
set "IKAROS_STUDIO=%IKAROS_ROOT%\hermes-studio"
set "IKAROS_STUDIO_LOGS=%IKAROS_LOGS%\hermes-studio"
set "IKAROS_STUDIO_DATA=%IKAROS_DATA%\hermes-studio"
REM Point the web UI's Hermes Agent service dependencies at our environment.
set "HERMES_BIN=%IKAROS_HERMES_AGENT%\venv\Scripts\hermes.exe"
set "HERMES_AGENT_CLI_PYTHON=%IKAROS_HERMES_AGENT%\venv\Scripts\python.exe"
set "HERMES_AGENT_BRIDGE_PYTHON=%IKAROS_HERMES_AGENT%\venv\Scripts\python.exe"
set "HERMES_AGENT_NODE=C:\Program Files\nodejs\node.exe"

REM ---- Ikaros Memory paths ----
set "IKAROS_MEMORY=%IKAROS_ROOT%\Ikaros-memory"
set "IKAROS_MEMORY_DATA=%IKAROS_MEMORY%\data"
set "IKAROS_MEMORY_MODELS=%IKAROS_MEMORY%\models"
set "IKAROS_MEMORY_SCRIPT=%IKAROS_MEMORY%\v4\store.py"

REM ---- Ikaros Live2D (Tauri pet) ----
set "IKAROS_LIVE2D=%IKAROS_ROOT%\Ikaros-Live2D"
set "IKAROS_NODE_MODULES=%IKAROS_RUNTIME%\node23\node_modules"

REM ---- Pet node_modules junction (portable) ----
set "IKAROS_LIVE2D_NM=%IKAROS_LIVE2D%\node_modules"
if exist "%IKAROS_LIVE2D_NM%\vue" goto :nm_ok
if exist "%IKAROS_LIVE2D_NM%" rmdir "%IKAROS_LIVE2D_NM%" >nul 2>&1
mklink /J "%IKAROS_LIVE2D_NM%" "%IKAROS_NODE_MODULES%" >nul 2>&1
if errorlevel 1 (
    echo [warn] pet node_modules link failed
)
:nm_ok

REM ---- Portable Rust (standalone, no rustup) ----
set "IKAROS_RUST=%IKAROS_RUNTIME%\rust"

REM ---- llama-server (llama.cpp) ----
if not defined IKAROS_LLAMA_VERSION set "IKAROS_LLAMA_VERSION=b10000-cuda"
set "IKAROS_LLAMA_DIR=%IKAROS_RUNTIME%\llama\%IKAROS_LLAMA_VERSION%"
set "IKAROS_LLAMA_SERVER=%IKAROS_LLAMA_DIR%\llama-server.exe"

REM ---- Model paths ----
set "IKAROS_MODEL_EMBEDDING=%IKAROS_MEMORY_MODELS%\nomic-embed-text-v2-moe.f32.gguf"
REM IKAROS_MODEL_LLM intentionally NOT set here — let watchdog default to Qwen3-1.7B

REM ---- Service ports ----
set "IKAROS_PORT_EMBEDDING=8587"
set "IKAROS_PORT_LLAMA=8080"

REM ---- Python / PATH ----
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHONPATH=%IKAROS_ROOT%;%IKAROS_HERMES_AGENT%"
set "PATH=%IKAROS_RUST%\bin;%IKAROS_LLAMA_DIR%;%IKAROS_RUNTIME%;%IKAROS_RUNTIME%\node23;%IKAROS_ROOT%\portable-python\Scripts;%IKAROS_ROOT%\portable-python;%PATH%"
set "NODE_PATH=%IKAROS_RUNTIME%\node23\node_modules"
set "PYTHONHOME="

REM ---- HERMES_* compat vars (for legacy scripts) ----
set "HERMES_ROOT=%IKAROS_ROOT%"
set "HERMES_HOME=%IKAROS_HERMES_HOME%"
set "HERMES_PYTHON=%IKAROS_PYTHON%"
set "HERMES_RUNTIME=%IKAROS_RUNTIME%"
set "HERMES_AGENT_ROOT=%IKAROS_HERMES_AGENT%"

exit /b 0
