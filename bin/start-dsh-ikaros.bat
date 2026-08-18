@echo off
setlocal EnableExtensions

REM ============================================================
REM  Ikaros 工作引擎 —— DeepSeek Harness (DSH) 底座启动器
REM
REM  启用 core/ikaros-dsh/cordis.patch.yml overlay:
REM    - memory_v5 长期记忆 (MCP stdio, 48 个 v5_* 工具)
REM    - 持久 PTY 终端 (terminal / terminal-bash / tool-terminal)
REM    - LSP 精确导航 (lsp / lsp-stdio / tool-lsp)
REM    - Ikaros 工作引擎 persona (system-prompt 覆盖)
REM
REM  用法:
REM    start-dsh-ikaros.bat                 → 交互式 Web GUI (http://127.0.0.1:3080)
REM    start-dsh-ikaros.bat web [args...]   → 显式 web 模式 (透传额外参数)
REM    start-dsh-ikaros.bat headless <task> → one-shot 工作引擎 (跑一个 task 退出)
REM
REM  与裸 dsh web 的区别: 本脚本带 --patch 显式加载 Ikaros overlay。
REM ============================================================

REM ── 自锚定 IKAROS_ROOT (%~dp0 移动文件夹后仍正确) ──
set "IKAROS_ROOT=%~dp0.."
for %%i in ("%IKAROS_ROOT%") do set "IKAROS_ROOT=%%~fi"

REM ── 注入便携环境 (IKAROS_* / PATH) ──
if exist "%IKAROS_ROOT%\bin\ikaros-env.bat" call "%IKAROS_ROOT%\bin\ikaros-env.bat"

set "PATCH=%IKAROS_ROOT%\core\ikaros-dsh\cordis.patch.yml"
if not exist "%PATCH%" (
  echo [start-dsh-ikaros] ERROR: patch overlay not found: %PATCH%
  exit /b 1
)

REM ── dsh 命令可用性检查 (npm 全局) ──
where dsh >nul 2>nul
if errorlevel 1 (
  echo [start-dsh-ikaros] ERROR: dsh command not found. Install: npm i -g @deepseek-ai/dsh
  exit /b 1
)

if /i "%~1"=="headless" (
  shift
  echo [start-dsh-ikaros] headless mode ^(patch: %PATCH%^)
  dsh --profile headless --patch "%PATCH%" %*
) else if /i "%~1"=="web" (
  shift
  echo [start-dsh-ikaros] web mode ^(patch: %PATCH%^)
  dsh web --patch "%PATCH%" %*
) else (
  echo [start-dsh-ikaros] web mode ^(patch: %PATCH%^)
  dsh web --patch "%PATCH%" %*
)

endlocal
