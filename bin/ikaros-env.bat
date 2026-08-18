@echo off
rem ikaros-env.bat �� Ikaros ��Я���� (��һȨ��Դ, ��ê��)
rem �ɸ���� bat call ���ļ� (start-dsh-ikaros.bat / ������� / memory services)��
rem ê��ԭ�� (ѧ ComfyUI-aki): һ��·����� IKAROS_ROOT �Ƶ�, ��д���̷�;
rem IKAROS_ROOT �� %%~fI �淶�� (���� %~dp0.. ������ \bin\.. ��˫��б��)��
rem ����: 2026-08-11 �� �ع�: 2026-08-18 (�Ƴ� hermes/neko, ���� dsh)
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

rem ---- DeepSeek Harness (dsh) �������� ----
set "IKAROS_DSH=%IKAROS_ROOT%\runtime\dsh"
set "IKAROS_DSH_SOURCE=%IKAROS_ROOT%\runtime\deepseek-harness-master"
set "IKAROS_DSH_PROFILE=%IKAROS_DATA%\dsh\profiles"
set "IKAROS_DSH_WEB_PORT=3080"
set "IKAROS_DSH_OVERLAY=%IKAROS_ROOT%\core\ikaros-dsh\cordis.patch.yml"

rem ---- omp (oh-my-pi) ���� agent ----
set "IKAROS_OMP_AGENT=%IKAROS_DATA%\omp\agent"
set "PI_CODING_AGENT_DIR=%IKAROS_OMP_AGENT%"

rem ---- llama.cpp / ����ģ�� ----
if not defined IKAROS_LLAMA_VERSION (
    set "IKAROS_LLAMA_VERSION=b10000-cuda"
    nvidia-smi 2>nul | findstr /C:"CUDA Version: 12." >nul 2>&1 && set "IKAROS_LLAMA_VERSION=b10000-cuda-12.4"
    nvidia-smi 2>nul | findstr /C:"CUDA UMD Version: 12." >nul 2>&1 && set "IKAROS_LLAMA_VERSION=b10000-cuda-12.4"
)
set "IKAROS_LLAMA_DIR=%IKAROS_RUNTIME%\llama\%IKAROS_LLAMA_VERSION%"
set "IKAROS_LLAMA_SERVER=%IKAROS_LLAMA_DIR%\llama-server.exe"

rem ---- ��������ʱ ----
set "IKAROS_RUST=%IKAROS_RUNTIME%\rust"
set "IKAROS_HERDR=%IKAROS_RUNTIME%\herdr\herdr.exe"
set "IKAROS_MCP=%IKAROS_RUNTIME%\MCPServe"
set "IKAROS_GRAPHIFY=%IKAROS_MCP%\graphify"
set "IKAROS_GRAPHIFY_SERVE=%IKAROS_GRAPHIFY%\graphify\serve.py"
set "THIRDSPACE_VAULT=%IKAROS_DATA%\thirdspace-vault"

rem ---- �˿� ----
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

rem ---- --print ģʽ: ���ȫ�� IKAROS_* ���� (����/������) ----
if /i "%~1"=="--print" (
    set | findstr /B "IKAROS_"
    exit /b 0
)
exit /b 0
