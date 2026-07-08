@echo off
REM ============================================================
REM  Ikaros - Desktop Pet Launcher (no-bridge 2026-07-03)
REM ============================================================
REM  No-bridge: no bridge / supervisor / webui.
REM  2026-07-05 哥哥装了 hermes-web-ui 后又卸了, :8648 已释放给
REM  Ikaros-Live2D Tauri webview.  Hermes Desktop launches as
REM  standalone app.  Desktop Pet stays in system tray.
REM ============================================================
REM -- Why NO setlocal -----------------------------------------------
REM  Windows 25H2: child processes created by start within setlocal
REM  inherit the original unmodified env block, not the setlocal one.
REM  hermes-desktop.bat relies on env vars PATH / HERMES_HOME /
REM  HERMES_DESKTOP_HERMES_ROOT passed to Electron subprocess.
REM  Without setlocal, all set writes to current process env,
REM  and start reliably inherits them.
REM  (wait loop uses goto to re-resolve %WAIT%, no delayedexpansion)
REM -------------------------------------------------------------------

REM ---- Load Ikaros environment (via init.bat single entry) ----
call "%~dp0..\Ikaros-environment\init.bat"
if errorlevel 1 (
    echo [FATAL] Ikaros-environment\init.bat failed.
    pause
    exit /b 1
)

REM ---- Step 0: Quick self-check (suite green for recent changes) ----
call "%IKAROS_BIN%\ikaros-verify.bat" --quick
if errorlevel 1 (
    echo [FATAL] Self-check failed. Fix before continuing.
    pause
    exit /b 1
)
echo.
echo ============================================================
echo   Ikaros - No-Bridge Launcher
echo.
echo   Pet:       Ikaros Desktop Pet v2  (Tauri v2, Live2D, click-through)
echo   Frontend:  Hermes Desktop       (Electron)
echo.
echo   Memory:    Embedding :8587 + LLM :8080 (Hermes Agent unified)
echo   LLM:       cloud (DeepSeek V4 / minimax) + local :8080
echo   Dashboard: http://127.0.0.1:9119  (hermes dashboard)
echo   Voice WS:  ws://127.0.0.1:7870/v1/voice/ws (Tauri Pet speech)
echo   5D Cog:    cogno + soul injected by cloud_chat.py
echo   Memory:    ikaros-memory-watchdog auto-check + restart
echo   Logs:      %IKAROS_LOGS%\
echo   Stop:      bin\ikaros-sleep.bat
echo ============================================================
echo.

REM ---- Step 1: Stop stale instances ----
echo [1] Stopping old instances...
call "%IKAROS_BIN%\ikaros-sleep.bat" >nul 2>&1
timeout /t 2 /nobreak >nul
echo       done

REM ---- Step 2: Start Memory Services (watchdog manages embedding + LLM) ----
echo.
echo [2] Starting Memory Services...
echo       Embedding :8587 (nomic-embed-text)
echo       LLM       :8080 (qwen3-8b, watchdog managed)
echo.
start "MemoryWatchdog" /MIN "%IKAROS_PYTHON%" "%IKAROS_BIN%\ikaros-memory-watchdog.py" --detach >nul 2>&1
REM Wait for endpoints file (max 40s)
set "WAIT=0"
:wait_endpoints
if exist "%IKAROS_MEMORY_DATA%\endpoints.json" goto :endpoints_ready
timeout /t 2 /nobreak >nul
set /a WAIT+=2
if %WAIT% lss 40 goto :wait_endpoints
echo       [WARN] Memory services may not be fully ready (timeout)
goto :after_memory
:endpoints_ready
echo       Memory endpoints ready:
type "%IKAROS_MEMORY_DATA%\endpoints.json" 2>nul
echo.
:after_memory

REM ---- Step 2b: Launch Voice WS (:7870) for Tauri Pet ----
echo.
echo [2b] Launching Voice WS (:7870)...
echo       Tauri Pet speech link (cogno_5d + cloud_chat + edge-tts)
echo.
start "VoiceWS" /MIN "%IKAROS_PYTHON%" "%IKAROS_BIN%\ikaros-voice-ws.py" > "%IKAROS_LOGS%\voice-ws.log" 2>&1
REM Wait for :7870 (max 20s)
set "WAIT=0"
:wait_voice
"%IKAROS_PYTHON%" -c "import socket;s=socket.socket();s.settimeout(1);r=s.connect_ex(('127.0.0.1',7870));s.close();exit(0 if r==0 else 1)" >nul 2>&1
if not errorlevel 1 goto :voice_ready
timeout /t 2 /nobreak >nul
set /a WAIT+=2
if %WAIT% lss 20 goto :wait_voice
echo       [WARN] Voice WS may not be ready (timeout)
goto :after_voice
:voice_ready
echo       Voice WS: ws://127.0.0.1:7870/v1/voice/ws
echo.
:after_voice

REM ---- Step 3: Launch Desktop Pet v2 (Tauri) ----
echo.
echo [3] Launching Desktop Pet v2 (Tauri)...
set "PET_EXE=%IKAROS_ROOT%\Ikaros-Live2D\src-tauri\target\release\ikaros-desktop-pet.exe"
if not exist "%PET_EXE%" (
    echo       [WARN] %PET_EXE% not found. Skipping pet.
    goto :after_pet
)
start "" "%PET_EXE%"
echo       Pet started (Tauri v2 release)
:after_pet

REM ---- Step 4: Launch Hermes Dashboard (web UI :9119) ----
echo.
echo [4] Starting Hermes Dashboard...
REM Use launch-hidden.vbs to start completely windowless (no CMD flash)
wscript.exe "%IKAROS_BIN%\launch-hidden.vbs" "cmd /c ""%IKAROS_HERMES_AGENT%\venv\Scripts\hermes.exe"" dashboard --port 9119 --no-open --skip-build >nul 2>&1"
REM Wait for Dashboard port (max 30s)
set "WAIT=0"
:wait_dashboard
"%IKAROS_PYTHON%" -c "import socket;s=socket.socket();s.settimeout(1);r=s.connect_ex(('127.0.0.1',9119));s.close();exit(0 if r==0 else 1)" >nul 2>&1
if not errorlevel 1 goto :dashboard_ready
timeout /t 2 /nobreak >nul
set /a WAIT+=2
if %WAIT% lss 30 goto :wait_dashboard
echo       [WARN] Dashboard may not be fully ready (timeout)
goto :after_dashboard
:dashboard_ready
echo       Dashboard: http://127.0.0.1:9119
echo.
:after_dashboard

REM ---- Step 5: Launch Hermes Desktop ----
echo.
echo [5] Launching Hermes Desktop...
call "%IKAROS_BIN%\hermes-desktop.bat"
echo.

REM ---- Done ----
echo ============================================================
echo   Ikaros is ready!
echo.
echo   Pet:       Ikaros Desktop Pet v2  (Tauri v2, Live2D)
echo   Frontend:  Hermes Desktop       (Electron)
echo   Dashboard: http://127.0.0.1:9119
echo   Voice WS:  ws://127.0.0.1:7870/v1/voice/ws
echo   Memory:    Embedding :8587 + LLM :8080 (unified)
echo   LLM:       cloud (DeepSeek V4) + local :8080
echo.
echo   Endpoints: %IKAROS_MEMORY_DATA%\endpoints.json
echo   Stop:      bin\ikaros-sleep.bat
echo ============================================================
echo.

exit /b 0
