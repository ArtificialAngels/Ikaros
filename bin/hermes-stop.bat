@echo off
REM ============================================================
REM Hermes - Stop all running Hermes processes
REM
REM Kills:
REM   - llama-server*.exe (any variant)
REM   - python -m hermes serve (Hermes FastAPI)
REM   - gopeed-web (the Python download bridge, optional)
REM
REM v2: 2026-06-06 - use wildcard IM match. v1's "llama-server.exe" literal
REM missed the actual binary name "llama-server-cuda-12.4.exe" and the
REM model stayed in VRAM after stop.
REM ============================================================
setlocal

echo Stopping Hermes processes...

REM ---- 1. Kill ALL llama-server variants (frees VRAM) ----
REM   The binary is named llama-server-cuda-12.4.exe (not llama-server.exe).
REM   /T also kills child processes spawned by detached .ps1 launchers.
echo [1/4] Killing llama-server* (any variant)...
REM Count first, then kill (so the "no matches" branch is unambiguous).
powershell -NoProfile -Command ^
    "Get-Process -Name 'llama-server*' -ErrorAction SilentlyContinue | Measure-Object | ForEach-Object { $_.Count }" ^
    > "%TEMP%\hermes-stop-count.txt" 2>nul
set /p LLAMA_COUNT=< "%TEMP%\hermes-stop-count.txt" >nul 2>&1
del "%TEMP%\hermes-stop-count.txt" 2>nul
if "%LLAMA_COUNT%"=="0" (
    echo   (no llama-server* found running)
) else (
    powershell -NoProfile -Command ^
        "Get-Process -Name 'llama-server*' -ErrorAction SilentlyContinue | ForEach-Object { Write-Host ('   PID ' + $_.Id + ' (' + $_.ProcessName + ')'); Stop-Process -Id $_.Id -Force }" ^
        2>nul
)

REM ---- 2. Kill python processes related to Hermes ----
echo [2/3] Killing Hermes python processes...
powershell -NoProfile -Command ^
    "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | Where-Object { $_.CommandLine -match 'hermes' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" ^
    >nul 2>&1
if errorlevel 1 echo   (none found)

REM ---- 3. Kill gopeed-web (Python download bridge) if we own it ----
echo [2/3] Killing gopeed-web (if Hermes-spawned)...
REM gopeed-web is a single-exe headless server; only kill it if we started it
REM (i.e. it's running on 9999 and was spawned by our process tree). For now
REM just attempt taskkill on the binary name - user can re-launch easily.
powershell -NoProfile -Command ^
    "Get-Process -Name 'gopeed-web' -ErrorAction SilentlyContinue | Where-Object { $_.MainModule.FileName -like '*Hermes Agent*' } | Stop-Process -Force" ^
    >nul 2>&1
if errorlevel 1 echo   (none / not owned by Hermes)

REM ---- 4. Kill any leftover "Hermes-*" window (defensive) ----
echo [3/3] Killing leftover Hermes-* windows...
REM taskkill prints child-termination info to stdout (not stderr), so we
REM need >nul 2>&1 to suppress it cleanly.
taskkill /F /FI "WINDOWTITLE eq Hermes*" /T >nul 2>&1

echo.
echo ============================================================
echo   Hermes stopped.
echo.
echo   VRAM should release within 1-2 seconds.
echo   If memory is still high, check nvidia-smi manually.
echo ============================================================
timeout /t 2 /nobreak >nul
endlocal
exit /b 0
