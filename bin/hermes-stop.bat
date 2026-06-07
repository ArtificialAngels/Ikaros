@echo off
REM ============================================================
REM Hermes - Stop all running Hermes processes
REM ============================================================
setlocal
chcp 65001 >nul
echo Stopping Hermes processes...

REM ---- 1. Graceful WebUI stop (first, so bridge can flush DB) ----
echo [1/8] Stopping WebUI gracefully...
if exist "%~dp0webui-new.bat" (
    call "%~dp0webui-new.bat" stop >nul 2>&1
    echo   WebUI stopped.
) else (
    echo   webui-new.bat not found, skipping.
)

REM ---- 2. Kill llama-server (GPU process) ----
echo [2/8] Killing llama-server...
powershell -NoProfile -Command ^
    "Get-Process -Name 'llama-server*' -ErrorAction SilentlyContinue | ForEach-Object { Write-Host ('   PID ' + $_.Id + ' (' + $_.ProcessName + ')'); $_ } | Stop-Process -Force -ErrorAction SilentlyContinue"
timeout /t 2 /nobreak >nul

REM ---- 3. Kill Hermes python processes ----
echo [3/8] Killing Hermes python...
powershell -NoProfile -Command ^
    "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | Where-Object { $_.CommandLine -match 'hermes' } | ForEach-Object { Write-Host ('   PID ' + $_.ProcessId + ' (hermes)'); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

REM ---- 4. Kill WebUI node process (fallback if graceful stop failed) ----
echo [4/8] Killing WebUI node (fallback)...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name = 'node.exe'\" | Where-Object { $_.CommandLine -match 'hermes-web-ui' } | ForEach-Object { Write-Host ('   PID ' + $_.ProcessId + ' (webui)'); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

REM ---- 5. Kill Console + Trace + Model Run powershell windows ----
echo [5/8] Killing Console ^& Trace ^& Model Run windows...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name = 'powershell.exe'\" | Where-Object { $_.CommandLine -match 'hermes-console' -or $_.CommandLine -match 'hermes-trace' -or $_.CommandLine -match 'hermes-model-run' } | ForEach-Object { Write-Host ('   PID ' + $_.ProcessId + ' (console/trace/model-run)'); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

REM ---- 6. Kill Hermes shell + Terminal windows (by title) ----
echo [6/8] Killing shell ^& Terminal windows...
REM cmd.exe wrappers (Hermes-LLM, Hermes-API, Hermes-Console, Hermes-Trace, Hermes Model Running, etc.)
powershell -NoProfile -Command ^
    "Get-Process -Name 'cmd' -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like 'Hermes-*' -or $_.MainWindowTitle -eq 'Hermes Model Running' } | ForEach-Object { Write-Host ('   PID ' + $_.Id + ' (shell: ' + $_.MainWindowTitle + ')'); Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }" 2>nul
REM Windows Terminal (Win11)
powershell -NoProfile -Command ^
    "Get-Process -Name 'WindowsTerminal','wt' -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -match 'Hermes' } | ForEach-Object { Write-Host ('   PID ' + $_.Id + ' (Terminal: ' + $_.MainWindowTitle + ')'); Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }" 2>nul

REM ---- 7. Kill gopeed-web ----
echo [7/8] Killing gopeed-web...
taskkill /F /IM "gopeed-web.exe" /T >nul 2>&1

REM ---- 8. Close browser tabs showing Hermes (by title match on browser process) ----
echo [8/8] Closing Hermes browser tabs...
powershell -NoProfile -Command ^
    "$browsers = @('msedge','chrome','firefox','brave','opera'); foreach ($b in $browsers) { Get-Process -Name $b -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -match 'Hermes' } | ForEach-Object { Write-Host ('   PID ' + $_.Id + ' (' + $b + ': ' + $_.MainWindowTitle + ')'); Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } }" 2>nul

echo.
echo ============================================================
echo   Hermes stopped.
echo ============================================================
timeout /t 1 /nobreak >nul
endlocal
exit /b 0
