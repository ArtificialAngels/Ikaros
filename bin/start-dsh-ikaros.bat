@echo off
setlocal EnableExtensions

REM ============================================================
REM  Ikaros work engine -- DeepSeek Harness (DSH) launcher
REM
REM  Loads core/ikaros-dsh/cordis.patch.yml overlay:
REM    - memory_v5 long-term memory (MCP stdio, 48 v5_* tools)
REM    - persistent PTY terminal (terminal / terminal-bash / tool-terminal)
REM    - LSP navigation (lsp / lsp-stdio / tool-lsp)
REM    - Ikaros work-engine persona (system-prompt override)
REM
REM  Usage:
REM    start-dsh-ikaros.bat                 -> web GUI (http://127.0.0.1:3080)
REM    start-dsh-ikaros.bat web [args...]   -> explicit web mode
REM    start-dsh-ikaros.bat headless <task> -> one-shot work engine
REM
REM  2026-08-18: use local dsh (runtime/dsh), no npm global shim.
REM  ASCII only -- cmd parses bat in ANSI/GBK, UTF-8 comments break it.
REM ============================================================

REM -- self-anchored IKAROS_ROOT (works after moving the folder) --
set "IKAROS_ROOT=%~dp0.."
for %%i in ("%IKAROS_ROOT%") do set "IKAROS_ROOT=%%~fi"

REM -- inject portable env (IKAROS_* / PATH) --
if exist "%IKAROS_ROOT%\bin\ikaros-env.bat" call "%IKAROS_ROOT%\bin\ikaros-env.bat"

set "PATCH=%IKAROS_ROOT%\core\ikaros-dsh\cordis.patch.yml"
if not exist "%PATCH%" (
  echo [start-dsh-ikaros] ERROR: patch overlay not found: %PATCH%
  exit /b 1
)

REM -- local dsh entry (portable, no npm global dependency) --
set "DSH_NODE=%IKAROS_ROOT%\runtime\node\node.exe"
set "DSH_BIN=%IKAROS_ROOT%\runtime\dsh\node_modules\@deepseek-ai\dsh\lib\bin.js"
if not exist "%DSH_NODE%" (
  echo [start-dsh-ikaros] ERROR: node not found: %DSH_NODE%
  exit /b 1
)
if not exist "%DSH_BIN%" (
  echo [start-dsh-ikaros] ERROR: dsh not found: %DSH_BIN%
  exit /b 1
)

if /i "%~1"=="headless" (
  echo [start-dsh-ikaros] headless mode (patch: %PATCH%)
  "%DSH_NODE%" "%DSH_BIN%" --profile headless --patch "%PATCH%" %~2 %~3 %~4 %~5 %~6 %~7 %~8 %~9
) else if /i "%~1"=="web" (
  REM 2026-08-19: ikaros-env.bat 的 setlocal 会让 IKAROS_DSH_WEB_PORT 不外传,
  REM 这里本地兜底 (默认 3085, 避免与官方 dsh desktop 3080 冲突)。
  if not defined IKAROS_DSH_WEB_PORT set "IKAROS_DSH_WEB_PORT=3085"
  echo [start-dsh-ikaros] web mode (profile auto-load patch, port %IKAROS_DSH_WEB_PORT%)
  "%DSH_NODE%" "%DSH_BIN%" web --port %IKAROS_DSH_WEB_PORT% %~2 %~3 %~4 %~5 %~6 %~7 %~8 %~9
) else (
  if not defined IKAROS_DSH_WEB_PORT set "IKAROS_DSH_WEB_PORT=3085"
  echo [start-dsh-ikaros] web mode (profile auto-load patch, port %IKAROS_DSH_WEB_PORT%)
  "%DSH_NODE%" "%DSH_BIN%" web --port %IKAROS_DSH_WEB_PORT% %~2 %~3 %~4 %~5 %~6 %~7 %~8 %~9
)

endlocal
