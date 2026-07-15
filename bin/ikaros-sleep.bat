@echo off
REM ============================================================
REM  Ikaros - Graceful Shutdown
REM  Stops: Watchdog + VoiceWS(:7870) + Pet + HermesDesktop + llama-server
REM
REM  If run standalone (double-click), loads init.bat first.
REM  When called from ikaros-start.bat, env vars already set — skips init.
REM  No setlocal: avoids corrupting parent's cmd stack.
REM ============================================================

if not defined IKAROS_ROOT call "%~dp0..\Ikaros-environment\init.bat"
if errorlevel 1 (
    echo [FATAL] init.bat failed
    pause
    exit /b 1
)

echo [sleep] Stopping Ikaros...

echo [0] Flushing V5 affect state...
"%IKAROS_PYTHON%" -c "import sys; sys.path.insert(0, r'%IKAROS_ROOT%\Ikaros-memory'); from v5.affect import flush; flush()" >nul 2>&1
echo       done

echo [1] Stopping Memory Watchdog...
"%IKAROS_PYTHON%" "%IKAROS_BIN%\ikaros-memory-watchdog.py" --stop >nul 2>&1
echo       done

echo [2] Stopping Voice WS (:7870)...
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":7870" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
echo       done

echo [3] Stopping Desktop Pet...
call "%IKAROS_BIN%\ikaros-live2d.bat" stop >nul 2>&1
echo       done

echo [4] Stopping Hermes Desktop...
taskkill /F /IM "Hermes.exe" /T >nul 2>&1
echo       done

echo [5] Safety sweep (llama-server)...
taskkill /F /IM "llama-server.exe" /T >nul 2>&1
echo       done

echo [6] Stopping Download Accelerator (gopeed :9999)...
taskkill /F /IM "gopeed-web.exe" /T >nul 2>&1
echo       done

echo [sleep] Done.
exit /b 0
