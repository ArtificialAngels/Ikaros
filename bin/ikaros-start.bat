@echo off
REM ============================================================
REM  Ikaros - Full Stack Launcher
REM  Steps: token -> env -> verify -> sleep -> memory -> voice -> think -> pet -> studio (dashboard/desktop = manual backup)
REM  Pure ASCII. No setlocal. No timeout (use ping -n). No init.bat inside sleep.bat.
REM ============================================================

REM ---- 0a. Dashboard WS Token ----
set HERMES_DASHBOARD_SESSION_TOKEN=ikaros-fixed-token-20260715
echo ikaros-fixed-token-20260715 > "%~dp0..\.dash_token"

REM ---- 0. Load environment ----
call "%~dp0..\Ikaros-environment\init.bat"
if errorlevel 1 (
    echo [FATAL] init.bat failed
    pause
    exit /b 1
)

REM ---- 0b. Quick self-check ----
call "%IKAROS_BIN%\ikaros-verify.bat" --quick
if errorlevel 1 (
    echo [FATAL] verify.bat failed
    pause
    exit /b 1
)

echo ============================================================
echo   Ikaros
echo.
echo   Pet:       Ikaros Desktop Pet v2  (Tauri v2, Live2D)
echo   Studio:    http://127.0.0.1:8649   (auto - default Web UI + Agent)
echo   Dashboard: http://127.0.0.1:9119   (manual: bin\hermes-dashboard.bat)
echo   Voice WS:  ws://127.0.0.1:7870/v1/voice/ws
echo   Memory:    :8587 embedding + :8080 qwen3-8b
echo   Think:     V5.1 metacog cycle
echo   Logs:      %IKAROS_LOGS%\
echo   Stop:      bin\ikaros-sleep.bat
echo ============================================================

REM ---- 1. Stop stale instances ----
echo [1] Stopping old instances...
call "%IKAROS_BIN%\ikaros-sleep.bat"
echo       [1] done

REM ---- 2. Memory watchdog ----
echo.
echo [2] Starting Memory Services...
echo       Embedding :8587 (nomic-embed-text)
if /I "%~1"=="--no-llm" (
    set "IKAROS_SKIP_LLM=1"
    echo       LLM :8080 SKIPPED --no-llm
) else (
    echo       LLM :8080 (qwen3-8b)
)
wscript.exe "%IKAROS_BIN%\launch-hidden.vbs" "cmd /c ""%IKAROS_PYTHON%"" ""%IKAROS_BIN%\ikaros-memory-watchdog.py"" --detach"
set "W=0"
:mem_wait
if exist "%IKAROS_MEMORY_DATA%\endpoints.json" goto :mem_ok
ping -n 3 127.0.0.1 >nul
set /a W+=2
if %W% lss 40 goto :mem_wait
echo       [WARN] Memory timeout
goto :after_mem
:mem_ok
echo       Memory endpoints ready
:after_mem

REM ---- 2b. Voice WS ----
echo [2b] Launching Voice WS (:7870)...
wscript.exe "%IKAROS_BIN%\launch-hidden.vbs" "cmd /c ""%IKAROS_PYTHON%"" ""%IKAROS_BIN%\ikaros-voice-ws.py"" > ""%IKAROS_LOGS%\voice-ws.log"" 2>&1"
set "W=0"
:voice_wait
"%IKAROS_PYTHON%" -c "import socket;s=socket.socket();s.settimeout(1);r=s.connect_ex(('127.0.0.1',7870));s.close();exit(0 if r==0 else 1)" >nul 2>&1
if not errorlevel 1 goto :voice_ok
ping -n 3 127.0.0.1 >nul
set /a W+=2
if %W% lss 20 goto :voice_wait
echo       [WARN] Voice WS timeout
goto :after_voice
:voice_ok
echo       Voice WS ready
:after_voice

REM ---- 2c. Think loop ----
echo [2c] Launching V5.1 self-think loop...
wscript.exe "%IKAROS_BIN%\launch-hidden.vbs" "cmd /c ""%IKAROS_BIN%\ikaros-think.bat"" --watch >nul 2>&1"

REM ---- 2d. Soul sync (V5 -> Hermes SOUL.md) ----
echo [2d] Syncing V5 soul to Hermes...
"%IKAROS_PYTHON%" "%IKAROS_BIN%\ikaros-soul-sync.py" --once >nul 2>&1
if not errorlevel 1 (
    echo       SOUL.md synced
) else (
    echo       [WARN] soul-sync failed (non-fatal)
)

REM ---- 3. Desktop Pet ----
echo [3] Launching Desktop Pet (Tauri)...
set "PET=%IKAROS_ROOT%\Ikaros-Live2D\src-tauri\target\release\ikaros-desktop-pet.exe"
if not exist "%PET%" (
    echo       [WARN] pet exe not found, skipping
    goto :after_pet
)
start "" "%PET%"
echo       Pet started
:after_pet

REM ---- Hermes Dashboard (MANUAL backup, NOT auto-started) ----
REM  Intentionally NOT auto-started: running it alongside Studio's
REM  bridge would spawn a 2nd Hermes Agent writing the same
REM  data/hermes-agent (session split + db contention).
REM  Launch on demand:  bin\hermes-dashboard.bat
REM  (Avoid running both Studio and Dashboard at once.)

REM ---- 4. Hermes Studio (hermes-web-ui, dev mode) ----
REM  Dev mode: npm run dev -> concurrently dev:server (Koa :8647) + dev:client
REM  (Vite :8649). Open http://127.0.0.1:8649. This is the earliest working
REM  variant (rolled back per request). NODE_PATH cleared to avoid managed
REM  runtime pollution.
echo [4] Starting Hermes Studio (:8649)...
set "STUDIO=%IKAROS_STUDIO%"
set "STUDIO_LOGS=%IKAROS_LOGS%\hermes-studio"
if not exist "%STUDIO_LOGS%" mkdir "%STUDIO_LOGS%"
cd /d "%STUDIO%"
if not exist "%STUDIO%\node_modules" (
    echo       [install] node_modules missing - running npm install...
    set "PATH=C:\Program Files\nodejs;%PATH%"
    call npm install
    if errorlevel 1 (
        echo       [WARN] npm install failed - skipping Hermes Studio
        goto :after_studio
    )
) else (
    echo       [skip] node_modules present
)
wscript.exe "%IKAROS_BIN%\launch-hidden.vbs" "cmd /c set ""HERMES_WEB_UI_HOME=%IKAROS_DATA%\hermes-studio"" && set ""NODE_PATH="" && set ""PATH=C:\Program Files\nodejs;%PATH%"" && cd /d ""%STUDIO%"" && npm run dev > ""%STUDIO_LOGS%\hermes-studio.log"" 2>&1"
set "W=0"
:studio_wait
"%IKAROS_PYTHON%" -c "import socket;s=socket.socket();s.settimeout(1);r=s.connect_ex(('127.0.0.1',8649));s.close();exit(0 if r==0 else 1)" >nul 2>&1
if not errorlevel 1 goto :studio_ok
ping -n 3 127.0.0.1 >nul
set /a W+=2
if %W% lss 90 goto :studio_wait
echo       [WARN] Hermes Studio timeout on :8649
goto :after_studio
:studio_ok
echo       Hermes Studio: http://127.0.0.1:8649
:after_studio

REM ---- 5. Desktop (interactive) ----
echo.
set /p "OPEN_DESKTOP=Launch Hermes Desktop? [y/N]: "
if /i "%OPEN_DESKTOP%"=="y" goto :do_desktop
if /i "%OPEN_DESKTOP%"=="yes" goto :do_desktop
goto :skip_desktop
:do_desktop
echo [5] Launching Hermes Desktop...
call "%IKAROS_BIN%\hermes-desktop.bat"
:skip_desktop

REM ---- Done ----
echo ============================================================
echo   Ikaros is ready!
echo.
echo   Pet:       Ikaros Desktop Pet v2  (Tauri v2, Live2D)
echo   Studio:    http://127.0.0.1:8649   (default Web UI + Agent)
echo   Dashboard: http://127.0.0.1:9119   (manual: bin\hermes-dashboard.bat)
echo   Voice WS:  ws://127.0.0.1:7870/v1/voice/ws
echo   Memory:    :8587 + :8080
echo.
echo   Stop:      bin\ikaros-sleep.bat
echo ============================================================
exit /b 0
