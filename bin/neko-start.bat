@echo off
REM N.E.K.O desktop start — Python backend (:48911) + Electron shell
REM Loads Ikaros env, detects NEKO paths, starts backend then desktop.

if not defined IKAROS_NEKO (
    if exist "%~dp0..\core\env\init.bat" (
        call "%~dp0..\core\env\init.bat"
    )
)

if defined IKAROS_NEKO set "ROOT=%IKAROS_NEKO%"
if not defined ROOT set "ROOT=%~dp0..\core\neko"

REM Process-level proxy isolation: bypass Windows system socks proxy
REM (socks://127.0.0.1:8086 is invalid for httpx and causes errors).
REM Only affects Neko launched by this batch; system proxy untouched, Steam Neko unaffected.
set "NO_PROXY=*"
set "no_proxy=*"

if not exist "%ROOT%" (
    echo [neko] ERROR: core/neko not found at %ROOT%
    pause
    exit /b 1
)

if defined IKAROS_NEKO_PYTHON (set "PY=%IKAROS_NEKO_PYTHON%") else (set "PY=%ROOT%\.venv\Scripts\python.exe")
if defined IKAROS_NEKO_SERVER  (set "SRV=%IKAROS_NEKO_SERVER%")  else (set "SRV=%ROOT%\app\main_server.py")
if defined IKAROS_NEKO_PORT    (set "PORT=%IKAROS_NEKO_PORT%")    else (set "PORT=48911")

echo [neko] Backend: %ROOT%
echo [neko] Port: %PORT%

REM Check if backend is already running
netstat -aon 2>nul | findstr /r ":%PORT%[ ].*LISTENING" >nul
if %errorlevel% equ 0 (
    echo [neko] Backend already running on :%PORT%
    goto :launch_desktop
)

echo [neko] Starting Python backend...
start "N.E.K.O Backend" /MIN "%PY%" "%SRV%"
echo [neko] Starting Agent Server (:48915)...
start "N.E.K.O Agent" /MIN "%PY%" "%ROOT%\app\agent_server.py"
echo [neko] Waiting for backend...
ping -n 8 127.0.0.1 >nul

:launch_desktop
set "EXE=%ROOT%\N.E.K.O.exe"
if exist "%EXE%" (
    echo [neko] Launching desktop...
    start "" "%EXE%"
) else (
    echo [neko] Desktop shell not found (N.E.K.O.exe missing)
    echo [neko] Open http://127.0.0.1:%PORT% in browser instead
)
