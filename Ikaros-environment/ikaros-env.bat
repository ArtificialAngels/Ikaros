@echo off
REM ============================================================
REM Ikaros Environment - Unified Path Configuration
REM ============================================================
REM  Centralized path config for all Ikaros components.
REM  When called by other scripts, sets IKAROS_* env vars.
REM
REM  Usage:
REM    call "%IKAROS_ROOT%\Ikaros-environment\ikaros-env.bat"
REM
REM  Auto-detect: If IKAROS_ROOT is not set, derive from script location.
REM ============================================================
REM NOTE: No setlocal - all vars are exported to the caller.
REM       Consistent with deps/hermes-env.bat design.

REM ---- Step 1: Detect IKAROS_ROOT ----
REM If already set, use it; otherwise derive from script location.
REM Script at E:\Ikaros\Ikaros-environment\ikaros-env.bat
REM So IKAROS_ROOT = parent of script directory.
if defined IKAROS_ROOT goto :root_ok

REM Derive from script location: %~dp0 = E:\Ikaros\Ikaros-environment\
REM Strip trailing backslash, then go up one level
set "IKAROS_ENV_DIR=%~dp0"
if "%IKAROS_ENV_DIR:~-1%"=="\" set "IKAROS_ENV_DIR=%IKAROS_ENV_DIR:~0,-1%"
for %%I in ("%IKAROS_ENV_DIR%\..") do set "IKAROS_ROOT=%%~fI"

:root_ok
REM Strip trailing backslash
if "%IKAROS_ROOT:~-1%"=="\" set "IKAROS_ROOT=%IKAROS_ROOT:~0,-1%"

REM ---- Step 2: Core paths ----
set "IKAROS_PYTHON=%IKAROS_ROOT%\portable-python\python.exe"
set "IKAROS_RUNTIME=%IKAROS_ROOT%\runtime"
set "IKAROS_NODE=%IKAROS_ROOT%\runtime\node23\node.exe"
set "IKAROS_DATA=%IKAROS_ROOT%\data"
set "IKAROS_BIN=%IKAROS_ROOT%\bin"
set "IKAROS_CONFIG=%IKAROS_ROOT%\config"
set "IKAROS_MODULES=%IKAROS_ROOT%\modules"
set "IKAROS_DEPS=%IKAROS_ROOT%\deps"
set "IKAROS_LOGS=%IKAROS_ROOT%\data\logs"
set "IKAROS_DATA_MODELS=%IKAROS_ROOT%\data\models"

REM ---- Step 3: Hermes component paths ----
set "IKAROS_HERMES_AGENT=%IKAROS_ROOT%\hermes-agent"
set "IKAROS_HERMES_HOME=%IKAROS_ROOT%\data\hermes-agent"
set "IKAROS_BRIDGE=%IKAROS_ROOT%\bridge"
set "IKAROS_HERMES=%IKAROS_ROOT%\hermes"

REM ---- Step 4: Ikaros-specific module paths ----
set "IKAROS_MEMORY=%IKAROS_ROOT%\Ikaros-memory"
set "IKAROS_MEMORY_DATA=%IKAROS_MEMORY%\data"
set "IKAROS_MEMORY_MODELS=%IKAROS_MEMORY%\models"
set "IKAROS_MEMORY_SERVICES=%IKAROS_MEMORY%\services"
set "IKAROS_MEMORY_SCRIPT=%IKAROS_MEMORY%\v4\store.py"

REM ---- Step 4b: Ikaros-Live2D desktop pet paths ----
set "IKAROS_LIVE2D=%IKAROS_ROOT%\Ikaros-Live2D"
set "IKAROS_NODE_MODULES=%IKAROS_RUNTIME%\node23\node_modules"

REM ---- TTS: 默认走 edge-tts (zh-CN-XiaoxiaoNeural, 中文甜美女声) ----
REM 如需切回本地 VITS, 取消下面注释:
REM set "IKAROS_TTS_BACKEND=local"
REM set "IKAROS_TTS_MODEL_DIR=%IKAROS_DATA_MODELS%\sherpa-onnx-vits-zh-hf-keqing\vits-zh-hf-keqing"
REM set "IKAROS_TTS_SPEAKER=0"

REM ---- Step 4b-2: Ensure pet node_modules link (portable) ----
REM  Pet MUST reuse runtime\node23\node_modules (no duplicate copy).
REM  Ikaros-Live2D\node_modules is a JUNCTION to the shared node_modules.
REM  Why junction (not a relative symlink): Node's realpathSync does an
REM  EPERM stat on directory symlinks in this sandbox, breaking vite build.
REM  Junctions have no such restriction. Portability is handled HERE: this
REM  block recreates the junction from %IKAROS_NODE_MODULES% (derived from
REM  %IKAROS_ROOT%, i.e. the CURRENT drive), so if the whole project tree
REM  is moved to another drive letter the link is rebuilt with the right
REM  path on next launch. If the link is already valid (vue reachable) we
REM  skip, to avoid needless churn.
set "IKAROS_LIVE2D_NM=%IKAROS_LIVE2D%\node_modules"
if exist "%IKAROS_LIVE2D_NM%\vue" goto :nm_ok
if exist "%IKAROS_LIVE2D_NM%" rmdir "%IKAROS_LIVE2D_NM%" >nul 2>&1
mklink /J "%IKAROS_LIVE2D_NM%" "%IKAROS_NODE_MODULES%" >nul 2>&1
if errorlevel 1 (
    echo [warn] pet node_modules link could not be created; build may fail
) else (
    echo [env] pet node_modules -^> %IKAROS_NODE_MODULES% (junction)
)
:nm_ok

REM ---- Step 4c: Portable Rust toolchain (standalone rustc + cargo) ----
REM No rustup needed - just bin/ on PATH. Truly portable, zero registry deps.
set "IKAROS_RUST=%IKAROS_RUNTIME%\rust"

REM ---- Step 5: llama-server paths ----
REM b9867 is the llama.cpp build version, located under runtime\llama\
if not defined IKAROS_LLAMA_VERSION set "IKAROS_LLAMA_VERSION=b9867"
set "IKAROS_LLAMA_DIR=%IKAROS_RUNTIME%\llama\%IKAROS_LLAMA_VERSION%"
set "IKAROS_LLAMA_SERVER=%IKAROS_LLAMA_DIR%\llama-server.exe"
set "IKAROS_LLAMA_CLI=%IKAROS_LLAMA_DIR%\llama-cli.exe"

REM ---- Step 6: Model paths ----
set "IKAROS_MODEL_EMBEDDING=%IKAROS_MEMORY_MODELS%\nomic-embed-text.gguf"
set "IKAROS_MODEL_LLM=%IKAROS_MEMORY_MODELS%\qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"

REM ---- Step 7: Service ports ----
set "IKAROS_PORT_EMBEDDING=8587"
set "IKAROS_PORT_LLAMA=8080"
REM NOTE: LLM unified on :8080 (Hermes Agent llama-server), no separate :8589
set "IKAROS_PORT_BRIDGE=7860"
REM 2026-07-05: hermes-web-ui 卸了 (哥哥).  :8648 让给 Ikaros-Live2D Tauri webview.
set "IKAROS_PORT_LIVE2D_WEBVIEW=8648"
set "IKAROS_PORT_LIVE2D_WEBVIEW_INTERNAL=8649"

REM ---- Step 8: Python env vars ----
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHONPATH=%IKAROS_ROOT%;%IKAROS_HERMES_AGENT%"

REM ---- Step 9: PATH enhancement ----
REM Project portable versions take priority over system versions
set "PATH=%IKAROS_RUST%\bin;%IKAROS_LLAMA_DIR%;%IKAROS_RUNTIME%;%IKAROS_RUNTIME%\node23;%IKAROS_RUNTIME%\aria2;%IKAROS_RUNTIME%\gopeed;%IKAROS_RUNTIME%\rpc-server;%IKAROS_ROOT%\portable-python\Scripts;%IKAROS_ROOT%\portable-python;%PATH%"

REM ---- Step 10: Interference prevention ----
REM Clear system env vars that could interfere
set "NODE_PATH=%IKAROS_RUNTIME%\node23\node_modules"
set "NPM_CONFIG_PREFIX="
set "PYTHONHOME="

REM Set HERMES compat vars (for legacy scripts)
set "HERMES_ROOT=%IKAROS_ROOT%"
set "HERMES_BIN=%IKAROS_BIN%"
set "HERMES_PYTHON=%IKAROS_PYTHON%"
set "HERMES_DATA=%IKAROS_DATA%"
set "HERMES_HOME=%IKAROS_HERMES_HOME%"
set "HERMES_RUNTIME=%IKAROS_RUNTIME%"
set "HERMES_DEPS=%IKAROS_DEPS%"
set "HERMES_MODULES=%IKAROS_MODULES%"
set "HERMES_LOGS=%IKAROS_LOGS%"
set "HERMES_CONFIG=%IKAROS_CONFIG%\hermes.yaml"
set "HERMES_MODELS=%IKAROS_MEMORY_MODELS%"
set "HERMES_AGENT_ROOT=%IKAROS_HERMES_AGENT%"

exit /b 0
