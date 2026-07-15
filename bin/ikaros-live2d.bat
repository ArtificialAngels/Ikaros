@echo off
REM ============================================================
REM  Ikaros Desktop Pet - Tauri v2 (Live2D)
REM  Usage: bin\ikaros-live2d.bat [start|stop|status]
REM ============================================================

call "%~dp0..\Ikaros-environment\init.bat"
if errorlevel 1 (
    echo [ikaros] FATAL: init.bat failed.
    exit /b 1
)

set "MODE=%~1"
if "%MODE%"=="" set "MODE=start"

if /I "%MODE%"=="start" goto :start
if /I "%MODE%"=="stop"  goto :stop
if /I "%MODE%"=="status" goto :status

echo [ikaros] Unknown mode: %MODE%
echo [ikaros] Usage: bin\ikaros-live2d.bat [start^|stop^|status]
exit /b 1

:start
call :ensure_voice

tasklist /FI "IMAGENAME eq ikaros-desktop-pet.exe" /FO CSV /NH 2>nul | find /I "ikaros-desktop-pet.exe" >nul
if not errorlevel 1 (
    echo [ikaros] Pet already running.
    exit /b 0
)

set "PET_EXE=%IKAROS_ROOT%\Ikaros-Live2D\src-tauri\target\release\ikaros-desktop-pet.exe"
if not exist "%PET_EXE%" (
    echo [ikaros] ERROR: %PET_EXE% not found.
    echo [ikaros] Build: cd Ikaros-Live2D ^&^& npx tauri build
    exit /b 1
)

call :stop >nul 2>&1
echo [ikaros] Launching pet...
start "" "%PET_EXE%"
ping -n 3 127.0.0.1 >nul

tasklist /FI "IMAGENAME eq ikaros-desktop-pet.exe" /FO CSV /NH 2>nul | find /I "ikaros-desktop-pet.exe" >nul
if errorlevel 1 (
    echo [ikaros] WARNING: pet may not have started.
) else (
    echo [ikaros] OK Pet started.
)
exit /b 0

:stop
tasklist /FI "IMAGENAME eq ikaros-desktop-pet.exe" /FO CSV /NH 2>nul | find /I "ikaros-desktop-pet.exe" >nul
if errorlevel 1 (
    echo [ikaros] Pet was not running.
) else (
    taskkill /F /IM "ikaros-desktop-pet.exe" >nul 2>&1
    echo [ikaros] OK Stopped.
)
exit /b 0

:status
tasklist /FI "IMAGENAME eq ikaros-desktop-pet.exe" /FO CSV /NH 2>nul | find /I "ikaros-desktop-pet.exe" >nul
if not errorlevel 1 (
    echo   Running (ikaros-desktop-pet.exe)
) else (
    echo   Not running
)
exit /b 0

:ensure_voice
"%IKAROS_PYTHON%" -c "import socket;s=socket.socket();s.settimeout(1);r=s.connect_ex(('127.0.0.1',7870));s.close();exit(0 if r==0 else 1)" >nul 2>&1
if not errorlevel 1 exit /b 0
echo [ikaros] Starting Voice WS (:7870) for speech...
start "VoiceWS" /MIN "%IKAROS_PYTHON%" "%IKAROS_BIN%\ikaros-voice-ws.py" > "%IKAROS_LOGS%\voice-ws.log" 2>&1
set "WAIT=0"
:wait_voice_live2d
"%IKAROS_PYTHON%" -c "import socket;s=socket.socket();s.settimeout(1);r=s.connect_ex(('127.0.0.1',7870));s.close();exit(0 if r==0 else 1)" >nul 2>&1
if not errorlevel 1 goto :voice_ready_live2d
ping -n 3 127.0.0.1 >nul
set /a WAIT+=2
if %WAIT% lss 20 goto :wait_voice_live2d
echo [ikaros] WARNING: Voice WS not ready (timeout)
goto :after_voice_live2d
:voice_ready_live2d
echo [ikaros] Voice WS ready: ws://127.0.0.1:7870/v1/voice/ws
:after_voice_live2d
exit /b 0
