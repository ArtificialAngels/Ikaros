@echo off
rem ikaros-env.bat — Ikaros 便携环境 (单一权威源, 自锚定)
rem 由各入口 bat call 本文件 (start-dsh-ikaros.bat / 控制面板 / memory services)。
rem 锚点原则 (学 ComfyUI-aki): 一切路径相对 IKAROS_ROOT 推导, 不写死盘符;
rem IKAROS_ROOT 用 %%~fI 规范化 (消除 %~dp0.. 残留的 \bin\.. 与双反斜杠)。
rem 生成: 2026-08-11 · 重构: 2026-08-18 (移除 hermes/neko, 新增 dsh)
setlocal DisableDelayedExpansion
for %%I in ("%~dp0..") do set "IKAROS_ROOT=%%~fI"

rem ---- Core paths ----
set "IKAROS_BIN=%IKAROS_ROOT%\bin"
set "IKAROS_CONFIG=%IKAROS_ROOT%\config"
set "IKAROS_DATA=%IKAROS_ROOT%\data"
set "IKAROS_RUNTIME=%IKAROS_ROOT%\runtime"
set "IKAROS_PYTHON=%IKAROS_ROOT%\runtime\portable-python\python.exe"
set "IKAROS_NODE=%IKAROS_ROOT%\runtime\node\node.exe"
set "IKAROS_NODE_MODULES=%IKAROS_ROOT%\runtime\node\node_modules"
set "IKAROS_LOGS=%IKAROS_ROOT%\data\logs"
set "IKAROS_MODULES=%IKAROS_ROOT%\modules"

rem ---- Memory V5 ----
set "IKAROS_MEMORY=%IKAROS_ROOT%\core\memory_v5"
set "IKAROS_MEMORY_DATA=%IKAROS_MEMORY%\data"
set "IKAROS_MEMORY_MODELS=%IKAROS_MEMORY%\models"
set "IKAROS_MEMORY_SCRIPT=%IKAROS_MEMORY%\store.py"
set "IKAROS_MODEL_EMBEDDING=%IKAROS_MEMORY_MODELS%\bge-m3-q8_0.gguf"
set "IKAROS_MODEL_LLM=%IKAROS_MEMORY_MODELS%\Phi-4-mini-instruct-Q4_K_M.gguf"

rem ---- DeepSeek Harness (dsh) 工作引擎 ----
set "IKAROS_DSH=%IKAROS_ROOT%\runtime\dsh"
set "IKAROS_DSH_SOURCE=%IKAROS_ROOT%\runtime\deepseek-harness-master"
set "IKAROS_DSH_PROFILE=%IKAROS_DATA%\dsh\profiles"
set "IKAROS_DSH_WEB_PORT=3080"
set "IKAROS_DSH_OVERLAY=%IKAROS_ROOT%\core\ikaros-dsh\cordis.patch.yml"

rem ---- omp (oh-my-pi) 编码 agent ----
set "IKAROS_OMP_AGENT=%IKAROS_DATA%\omp\agent"
set "PI_CODING_AGENT_DIR=%IKAROS_OMP_AGENT%"

rem ---- llama.cpp / 本地模型 ----
if not defined IKAROS_LLAMA_VERSION (
    set "IKAROS_LLAMA_VERSION=b10000-cuda"
    nvidia-smi 2>nul | findstr /C:"CUDA Version: 12." >nul 2>&1 && set "IKAROS_LLAMA_VERSION=b10000-cuda-12.4"
    nvidia-smi 2>nul | findstr /C:"CUDA UMD Version: 12." >nul 2>&1 && set "IKAROS_LLAMA_VERSION=b10000-cuda-12.4"
)
set "IKAROS_LLAMA_DIR=%IKAROS_RUNTIME%\llama\%IKAROS_LLAMA_VERSION%"
set "IKAROS_LLAMA_SERVER=%IKAROS_LLAMA_DIR%\llama-server.exe"

rem ---- 其它运行时 ----
set "IKAROS_RUST=%IKAROS_RUNTIME%\rust"
set "IKAROS_HERDR=%IKAROS_RUNTIME%\herdr\herdr.exe"
set "IKAROS_MCP=%IKAROS_RUNTIME%\MCPServe"
set "IKAROS_GRAPHIFY=%IKAROS_MCP%\graphify"
set "IKAROS_GRAPHIFY_SERVE=%IKAROS_GRAPHIFY%\graphify\serve.py"
set "THIRDSPACE_VAULT=%IKAROS_DATA%\thirdspace-vault"

rem ---- 端口 ----
set "IKAROS_PORT_EMBEDDING=8587"
set "IKAROS_PORT_LLM=8080"
set "IKAROS_PORT_LLAMA=8080"

rem ---- Python / PATH ----
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHONPATH=%IKAROS_ROOT%;%IKAROS_ROOT%\core"
set "PATH=%IKAROS_RUST%\bin;%IKAROS_LLAMA_DIR%;%IKAROS_ROOT%\runtime\herdr;%IKAROS_RUNTIME%;%IKAROS_RUNTIME%\node;%IKAROS_ROOT%\runtime\portable-python\Scripts;%IKAROS_ROOT%\runtime\portable-python;%PATH%"
set "NODE_PATH=%IKAROS_NODE_MODULES%"
set "PYTHONHOME="
exit /b 0
