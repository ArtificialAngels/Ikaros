@echo off
REM  ika-ws-restart.bat - restart Voice WS (:7870) without full Ikaros restart

call "%~dp0..\Ikaros-environment\init.bat" >nul 2>&1
set "IKAROS_PYTHON=%IKAROS_ROOT%\portable-python\python.exe"

echo [ws-restart] Stopping Voice WS (:7870)...
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":7870" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
timeout /t 1 /nobreak >nul
echo [ws-restart] Voice WS stopped.

echo [ws-restart] Starting Voice WS...
start "VoiceWS" /MIN "%IKAROS_PYTHON%" "%IKAROS_BIN%\ikaros-voice-ws.py" > "%IKAROS_LOGS%\voice-ws.log" 2>&1

REM wait for port
set "WAIT=0"
:wait_ws
"%IKAROS_PYTHON%" -c "import socket;s=socket.socket();s.settimeout(1);r=s.connect_ex(('127.0.0.1',7870));s.close();exit(0 if r==0 else 1)" >nul 2>&1
if not errorlevel 1 goto :ws_ready
timeout /t 2 /nobreak >nul
set /a WAIT+=2
if %WAIT% lss 20 goto :wait_ws
echo [ws-restart] WARNING: Voice WS may not be ready (timeout)
goto :end
:ws_ready
echo [ws-restart] Voice WS ready: ws://127.0.0.1:7870/v1/voice/ws
:end
exit /b 0
