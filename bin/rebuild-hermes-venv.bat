@echo off
rem rebuild-hermes-venv.bat
rem Recreates the hermes venv from scratch. Drive-letter independent (uses %~dp0).
rem When to use: after the project is moved, the drive letter changes, or
rem hermes.exe / "import hermes_cli" breaks (canonicalize / ModuleNotFound).
rem IMPORTANT: close the Hermes dashboard and cd out of the venv dir first,
rem or the old venv cannot be removed (its files are locked by a running process).

set "ROOT=%~dp0.."
set "HERMES=%ROOT%\core\hermes"
set "VENV=%HERMES%\venv"
set "UV=%ROOT%\runtime\portable-python\Scripts\uv.exe"
set "PY=%ROOT%\runtime\portable-python\python.exe"

if exist "%VENV%" (
  echo Removing old venv at %VENV%
  rmdir /s /q "%VENV%"
)

echo Creating fresh venv...
"%UV%" venv --clear --python "%PY%" --system-site-packages "%VENV%"
if errorlevel 1 (
  echo ERROR: uv venv failed rc=%errorlevel%
  exit /b 1
)

echo Installing hermes-agent (editable)...
rem [web,mcp] 必须一起装: 2026-08-02 踩坑 — 只装 -e . (或 [web]) 会漏掉 mcp SDK
rem (pyproject 里 mcp 是 extra), 导致 dashboard 里 12 个 MCP server 全部
rem "requires the 'mcp' Python SDK" 连不上。web=fastapi/uvicorn, mcp=mcp==1.28.1。
"%UV%" pip install --python "%VENV%\Scripts\python.exe" -e "%HERMES%[web,mcp]"
if errorlevel 1 (
  echo ERROR: pip install failed rc=%errorlevel% — venv 未完成, 不要继续
  exit /b 1
)

echo Installing aiohttp (8642 gateway API server adapter)...
rem 2026-08-02 踩坑: aiohttp 在 [messaging]/[matrix] 等 extra, [web,mcp] 不含 —
rem 缺失时 gateway 报 "API Server: aiohttp not installed" / "No adapter available
rem for api_server", :8642 永远起不来(9119 正常但对话树/得兼的 gateway 链路挂)。
"%UV%" pip install --python "%VENV%\Scripts\python.exe" "aiohttp==3.14.1"
if errorlevel 1 (
  echo ERROR: aiohttp install failed rc=%errorlevel%
  exit /b 1
)

echo Installing python-dotenv (mcp 1.28.1 -^> pydantic-settings -^> dotenv)...
rem 2026-08-04 踩坑: uv 装 [web,mcp] 后 venv 缺 dotenv 包目录(仅剩 dist-info),
rem mcp import 报 "No module named 'dotenv'" — 固定版本重装保证包目录完整。
"%UV%" pip install --python "%VENV%\Scripts\python.exe" --reinstall "python-dotenv==1.1.1"
if errorlevel 1 (
  echo ERROR: python-dotenv install failed rc=%errorlevel%
  exit /b 1
)

echo.
echo Done. Verify with: %VENV%\Scripts\hermes.exe --help
