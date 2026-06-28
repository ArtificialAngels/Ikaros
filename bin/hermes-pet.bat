@echo off
REM ============================================================
REM  🪶 Ikaros Desktop Pet — Hermes service launcher
REM
REM  Integrated into hermes-all.bat / hermes-stop.bat.
REM  Starts the pet as a detached process; window appears in
REM  system tray. Close this terminal after the pet shows up.
REM
REM  Autostart: bin\hermes-pet.bat --autostart  (HKCU Run)
REM  Manual:    bin\hermes-pet.bat start
REM  Stop:      bin\hermes-pet.bat stop
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

call "%~dp0..\deps\hermes-env.bat"
if errorlevel 1 (
    echo [ikaros] FATAL: could not resolve HERMES_ROOT.
    exit /b 1
)

set "MODE=%1"
if "%MODE%"=="" set "MODE=start"

if /I "%MODE%"=="start" goto :start
if /I "%MODE%"=="stop"  goto :stop
if /I "%MODE%"=="--autostart" goto :autostart
if /I "%MODE%"=="status" goto :status

echo [ikaros] Unknown mode: %MODE%
echo [ikaros] Usage: bin\hermes-pet.bat [start^|stop^|status^|--autostart]
exit /b 1

:start
echo.
echo ============================================================
echo   🪶 Ikaros Desktop Pet — Starting
echo ============================================================
set "PET_DIR=%HERMES_ROOT%\bin\ikaros-desktop-pet"
set "LAUNCHER=%PET_DIR%\detached.py"
set "LOG=%HERMES_ROOT%\data\logs\ikaros-pet.log"

if not exist "%LAUNCHER%" (
    echo [ikaros] ERROR: %LAUNCHER% not found
    exit /b 1
)

REM Kill any existing pet first
call :stop

REM Launch detached (starts main.py, exits immediately)
REM Use /B so this cmd window isn't tied to the pet's lifecycle
start "IkarosPet" /B "%HERMES_PYTHON%" "%LAUNCHER%"

REM Wait a moment, then check
timeout /t 3 /nobreak >nul

REM Check if pet started (look for python with ikaros-desktop-pet in cmdline)
wmic process where "name='python.exe' and commandline like '%%ikaros-desktop-pet%%'" get processid /format:value 2>nul | find "ProcessId=" >nul
if errorlevel 1 (
    echo [ikaros] WARNING: pet may not have started. Check log: %LOG%
) else (
    echo [ikaros] ✓ Pet started.
)
echo.
exit /b 0

:stop
echo.
echo [ikaros] Stopping Desktop Pet...
REM Find and kill python processes with ikaros-desktop-pet in command line
set "KILLED=0"
for /f "tokens=2 delims==" %%P in ('wmic process where "name='python.exe' and commandline like '%%ikaros-desktop-pet%%'" get processid /format:value 2^>nul') do (
    if not "%%P"=="" (
        echo   killing PID %%P...
        taskkill /F /PID %%P >nul 2>&1
        set "KILLED=1"
    )
)
REM Also kill by window title fallback
taskkill /FI "WINDOWTITLE eq 🪶*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq 💬*" /F >nul 2>&1
if "%KILLED%"=="1" (echo [ikaros] ✓ Stopped.) else (echo [ikaros] Pet was not running.)
echo.
exit /b 0

:autostart
echo.
echo ============================================================
echo   🪶 Ikaros Desktop Pet — HKCU Run Autostart
echo ============================================================
REM Register desktop pet to start with Windows.
REM Uses the same pattern as main.py register_autostart().
REM Writes: HKCU\Software\Microsoft\Windows\CurrentVersion\Run
set "REG_KEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
set "VAL_NAME=IkarosDesktopPet"

REM Check current state
reg query "%REG_KEY%" /v "%VAL_NAME%" >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=2*" %%A in ('reg query "%REG_KEY%" /v "%VAL_NAME%" 2^>nul') do (
        set "OLD_VAL=%%B"
    )
    if not "!OLD_VAL!"=="" (
        echo   Current: !OLD_VAL!
        echo.
        echo   A — Update to this machine's path
        echo   R — Remove (disable autostart)
        echo   S — Skip (keep as-is)
        choice /C ARS /N /M "Choose [A/R/S]: "
        if errorlevel 3 goto :end_autostart
        if errorlevel 2 goto :remove_autostart
    )
)

:set_autostart
REM Build the command: python detached.py
set "CMD=%HERMES_PYTHON% %HERMES_ROOT%\bin\ikaros-desktop-pet\detached.py"
reg add "%REG_KEY%" /v "%VAL_NAME%" /t REG_SZ /d "%CMD%" /f >nul
if errorlevel 1 (
    echo [ikaros] ERROR: Failed to write registry (need admin?)
    echo   Try: bin\hermes-pet.bat start
    exit /b 1
)
echo [ikaros] ✓ Autostart registered.
echo   Command: %CMD%
echo   Next boot: Desktop Pet will start automatically.
echo   To unregister: bin\hermes-pet.bat --autostart (choose R)
goto :end_autostart

:remove_autostart
reg delete "%REG_KEY%" /v "%VAL_NAME%" /f >nul 2>&1
echo [ikaros] ✓ Autostart removed.
goto :end_autostart

:end_autostart
echo.
exit /b 0

:status
echo.
echo === Desktop Pet Status ===
set "RUNNING="
for /f "tokens=2 delims==" %%P in ('wmic process where "name='python.exe' and commandline like '%%ikaros-desktop-pet%%'" get processid /format:value 2^>nul') do (
    if not "%%P"=="" set "RUNNING=%%P"
)
if not "%RUNNING%"=="" (
    echo   PID %RUNNING% ✓ running
) else (
    echo   not running
)
REM Check autostart
reg query "%REG_KEY%" /v "%VAL_NAME%" >nul 2>&1
if not errorlevel 1 (echo   Autostart: registered) else (echo   Autostart: not set)
echo.
exit /b 0