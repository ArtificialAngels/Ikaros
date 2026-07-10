@echo off
REM ============================================================
REM  Ikaros - Graceful Shutdown (no-bridge 2026-07-03)
REM ============================================================
REM  Stops: Memory Watchdog + Voice WS (:7870) + Desktop Pet + Hermes Desktop + safety sweep.
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

REM ---- Step 0: Flush V5 affect state (save emotional drift to disk) ----
echo [0] Flushing V5 affect state...
"%IKAROS_PYTHON%" -c "import sys; sys.path.insert(0, r'%IKAROS_ROOT%\Ikaros-memory'); from v5.affect import flush; flush()" >nul 2>&1
echo       done

REM ---- Step 0b: Stop Memory Watchdog ----
echo [0] Stopping Memory Watchdog...
"%IKAROS_PYTHON%" "%IKAROS_BIN%\ikaros-memory-watchdog.py" --stop >nul 2>&1
echo       done

REM ---- Step 0b: Stop Voice WS (:7870) ----
echo [0b] Stopping Voice WS (:7870)...
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":7870" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
echo       done

REM ---- Step 1: Stop Desktop Pet (Tauri v2) ----
echo [1] Stopping Desktop Pet (Tauri)...
call "%IKAROS_BIN%\ikaros-live2d.bat" stop >nul 2>&1
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
