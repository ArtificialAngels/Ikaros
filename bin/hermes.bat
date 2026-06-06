@echo off
REM ============================================================
REM Hermes - 启动 CLI 对话（交互模式）
REM 使用 portable-python，无需本机安装 Python
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "PY=%HERMES_ROOT%\portable-python\python.exe"

if not exist "%PY%" (
    echo [ERROR] Python not found: %PY%
    echo Run scripts\install-portable.bat first.
    pause
    exit /b 1
)

cd /d "%HERMES_ROOT%"

REM Set up environment
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "HERMES_DATA_DIR=%HERMES_ROOT%\hermes\data"
set "HERMES_LLM_MOCK=1"
set "HERMES_EMBEDDER=hash"

echo ============================================================
echo   Hermes - CLI Chat Mode
echo   Python: %PY%
echo   Mock LLM: enabled (set HERMES_LLM_MOCK=0 for real LLM)
echo ============================================================
echo.

"%PY%" -m hermes chat

endlocal
