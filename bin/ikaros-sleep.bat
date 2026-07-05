@echo off
REM ============================================================
REM  Ikaros - Graceful Shutdown (no-bridge 2026-07-03)
REM ============================================================
REM  Stops: Memory Watchdog + Desktop Pet + Hermes Desktop + safety sweep.
REM  No supervisor needed (retired).
REM ============================================================
setlocal

REM ---- Load Ikaros environment (via init.bat single entry) ----
call "%~dp0..\Ikaros-environment\init.bat"
if errorlevel 1 (
    echo [FATAL] Ikaros-environment\init.bat failed.
    exit /b 1
)

echo [sleep] Stopping all Ikaros processes...
echo.

REM ---- Step 0: Stop Memory Watchdog (stop memory first, then pet) ----
echo [0] Stopping Memory Watchdog...
"%IKAROS_PYTHON%" "%IKAROS_BIN%\ikaros-memory-watchdog.py" --stop >nul 2>&1
echo       done

REM ---- Step 1: Stop Desktop Pet ----
echo [1] Stopping Desktop Pet...
call "%IKAROS_BIN%\hermes-pet.bat" stop >nul 2>&1
echo       done

REM ---- Step 2: Stop Hermes Desktop (Electron) ----
echo [2] Stopping Hermes Desktop...
taskkill /F /IM "Hermes.exe" /T >nul 2>&1
echo       done

REM ---- Step 3: Safety sweep ----
echo [3] Safety sweep...
taskkill /F /IM "llama-server.exe" /T >nul 2>&1
taskkill /F /IM "llama-server-cuda-12.4.exe" /T >nul 2>&1
echo       done

echo.
echo [sleep] Done.
endlocal
exit /b 0
