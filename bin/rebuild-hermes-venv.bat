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

echo Installing hermes-agent (editable)...
rem [web,mcp] 必须一起装: 2026-08-02 踩坑 — 只装 -e . (或 [web]) 会漏掉 mcp SDK
rem (pyproject 里 mcp 是 extra), 导致 dashboard 里 12 个 MCP server 全部
rem "requires the 'mcp' Python SDK" 连不上。web=fastapi/uvicorn, mcp=mcp==1.28.1。
"%UV%" pip install --python "%VENV%\Scripts\python.exe" -e "%HERMES%[web,mcp]"

echo.
echo Done. Verify with: %VENV%\Scripts\hermes.exe --help
