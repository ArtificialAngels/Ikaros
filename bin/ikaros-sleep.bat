@echo off
REM ============================================================
REM  Ikaros — Graceful Shutdown (alias for hermes-stop.bat)
REM ============================================================
REM  Called by ikaros-start.bat to stop old instances before
REM  starting fresh. Also usable standalone.
REM  For the full shutdown sequence, see hermes-stop.bat.
REM ============================================================
setlocal
chcp 65001 >nul

REM ---- [env] Resolve HERMES_ROOT ----
call "%~dp0..\deps\hermes-env.bat"
if errorlevel 1 (
    echo [FATAL] could not resolve HERMES_ROOT.
    exit /b 1
)

echo [sleep] Stopping all Ikaros processes...

REM ---- Step 1: Stop supervisor-managed services (reverse topo order) ----
call "%HERMES_ROOT%\bin\hermes-supervisor.bat" --stop

REM ---- Step 2: Stop Hermes Desktop (Electron) ----
taskkill /F /IM "Hermes.exe" /T >nul 2>&1

REM ---- Step 3: Stop Desktop Pet ----
call "%HERMES_ROOT%\bin\hermes-pet.bat" stop >nul 2>&1

REM ---- Step 4: Safety sweep — orphaned llama-server ----
taskkill /F /IM "llama-server.exe" /T >nul 2>&1
taskkill /F /IM "llama-server-cuda-12.4.exe" /T >nul 2>&1
taskkill /F /IM "llama-server-vulkan.exe" /T >nul 2>&1

echo [sleep] Done.
endlocal
exit /b 0
