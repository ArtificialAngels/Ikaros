@echo off
REM ============================================================
REM  Ikaros Desktop Pet - Tauri v2 Edition (2026-07-05)
REM
REM  Replaces old PyQt6 pet with new Tauri v2 + Vue 3 + Live2D.
REM  Uses release build (no Vite dev server needed).
REM
REM  Usage: bin\ikaros-live2d.bat [start|stop|status]
REM ============================================================

call "%~dp0..\Ikaros-environment\init.bat"
if errorlevel 1 (
    echo [ikaros] FATAL: Ikaros-environment init.bat failed.
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
REM -- Singleton check --
echo [ikaros] Checking for existing instance...
tasklist /FI "IMAGENAME eq ikaros-desktop-pet.exe" /FO CSV /NH 2>nul | find /I "ikaros-desktop-pet.exe" >nul
if not errorlevel 1 (
    echo [ikaros] Pet already running.
    exit /b 0
)
echo [ikaros] No existing instance found.
echo.
echo ============================================================
echo   Ikaros Desktop Pet v2 (Tauri) - Starting
echo ============================================================

set "PET_EXE=%IKAROS_ROOT%\Ikaros-Live2D\src-tauri\target\release\ikaros-desktop-pet.exe"

if not exist "%PET_EXE%" (
    echo [ikaros] ERROR: %PET_EXE% not found.
    echo [ikaros] Run: cd %IKAROS_ROOT%\Ikaros-Live2D ^&^& npx tauri build
    exit /b 1
)

REM Kill any stale instances first
call :stop >nul 2>&1

REM Launch Tauri exe (detached, no Vite needed for release build)
echo [ikaros] Launching Tauri desktop pet...
start "" "%PET_EXE%"

timeout /t 2 /nobreak >nul

REM Verify
tasklist /FI "IMAGENAME eq ikaros-desktop-pet.exe" /FO CSV /NH 2>nul | find /I "ikaros-desktop-pet.exe" >nul
if errorlevel 1 (
    echo [ikaros] WARNING: pet exe may not have started.
) else (
    echo [ikaros] OK Pet started (Tauri v2 release).
)
echo.
exit /b 0

:stop
echo.
echo [ikaros] Stopping Tauri Desktop Pet...
tasklist /FI "IMAGENAME eq ikaros-desktop-pet.exe" /FO CSV /NH 2>nul | find /I "ikaros-desktop-pet.exe" >nul
if errorlevel 1 (
    echo [ikaros] Pet was not running.
) else (
    taskkill /F /IM "ikaros-desktop-pet.exe" >nul 2>&1
    echo [ikaros] OK Stopped.
)
echo.
exit /b 0

:status
echo.
echo === Desktop Pet v2 (Tauri) Status ===
tasklist /FI "IMAGENAME eq ikaros-desktop-pet.exe" /FO CSV /NH 2>nul | find /I "ikaros-desktop-pet.exe" >nul
if not errorlevel 1 (
    echo   Running (ikaros-desktop-pet.exe)
) else (
    echo   Not running
)
echo.
exit /b 0
