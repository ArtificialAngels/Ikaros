@echo off
REM ============================================================
REM Hermes - Stop all background processes (v2, uses supervisor)
REM ============================================================
setlocal
chcp 65001 >nul

REM ---- Single source of truth for HERMES_ROOT ----
call "%~dp0..\deps\hermes-env.bat"
if errorlevel 1 (
    echo [FATAL] could not resolve HERMES_ROOT.
    exit /b 1
)

echo Stopping Hermes processes via Python supervisor...

REM ---- Pure-Python supervisor: stop all services in reverse topo order ----
call "%HERMES_ROOT%\bin\hermes-supervisor.bat" --stop

REM ---- Fallback: kill by image name (catches stragglers) ----
taskkill /F /IM "llama-server.exe" /T >nul 2>&1
taskkill /F /IM "llama-server-cuda-12.4.exe" /T >nul 2>&1
taskkill /F /IM "llama-server-cuda-11.8.exe" /T >nul 2>&1
taskkill /F /IM "llama-server-vulkan.exe" /T >nul 2>&1
taskkill /F /IM "gopeed-web.exe" /T >nul 2>&1

powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | Where-Object { $_.CommandLine -match 'bridge\\.server' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" 2>nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name = 'node.exe'\" | Where-Object { $_.CommandLine -match 'hermes-web-ui' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" 2>nul

echo Done.
endlocal
exit /b 0
