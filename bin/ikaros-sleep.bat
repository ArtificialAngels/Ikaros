@echo off
REM Hermes - Stop all background processes
setlocal
chcp 65001 >nul
call "%~dp0..\deps\hermes-env.bat"
if errorlevel 1 (
    echo [FATAL] could not resolve HERMES_ROOT.
    exit /b 1
)
echo Stopping Hermes processes...
call "%HERMES_ROOT%\bin\hermes-supervisor.bat" --stop

REM ---- Stop Hermes Desktop (Electron) ----
echo [desktop] Stopping Hermes Desktop...
taskkill /F /IM "Hermes.exe" /T >nul 2>&1

REM ---- Stop Desktop Pet ----
echo [pet]  Stopping Desktop Pet...
call "%HERMES_ROOT%\bin\hermes-pet.bat" stop >nul 2>&1

taskkill /F /IM "llama-server.exe" /T >nul 2>&1
taskkill /F /IM "llama-server-cuda-12.4.exe" /T >nul 2>&1
taskkill /F /IM "llama-server-vulkan.exe" /T >nul 2>&1
echo Done.
endlocal
exit /b 0
