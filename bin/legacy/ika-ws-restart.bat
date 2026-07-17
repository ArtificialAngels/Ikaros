@echo off
REM See docs/scripts/bin/ika-ws-restart.md

call "%~dp0..\Ikaros-environment\init.bat" >nul 2>&1

echo [ws-restart] Stopping Voice WS (:7870)...
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":7870" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
ping -n 2 127.0.0.1 >nul
echo [ws-restart] Voice WS stopped.

echo [ws-restart] Starting Voice WS...
start "VoiceWS" /MIN "%IKAROS_PYTHON%" "%IKAROS_BIN%\ikaros-voice-ws.py" > "%IKAROS_LOGS%\voice-ws.log" 2>&1

REM wait for port
set "W=0"
:wait_ws
"%IKAROS_PYTHON%" -c "import socket;s=socket.socket();s.settimeout(1);r=s.connect_ex(('127.0.0.1',7870));s.close();exit(0 if r==0 else 1)" >nul 2>&1
if not errorlevel 1 goto :ws_ready
ping -n 3 127.0.0.1 >nul
set /a W+=2
if %W% lss 20 goto :wait_ws
echo [ws-restart] WARNING: Voice WS may not be ready
goto :end
:ws_ready
echo [ws-restart] Voice WS ready: ws://127.0.0.1:7870/v1/voice/ws
:end
exit /b 0
