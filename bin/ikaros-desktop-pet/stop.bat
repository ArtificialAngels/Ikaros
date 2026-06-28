@echo off
REM ============================================================
REM  🪶 Ikaros Desktop Pet — Stopper
REM ============================================================
REM
REM  Kills all running ikaros-desktop-pet python processes.
REM
REM ============================================================

echo ============================================================
echo   🪶 Stopping Ikaros Desktop Pet...
echo ============================================================

REM Use wmic to find processes with "ikaros-desktop-pet" in command line
set "KILLED=0"

for /f "tokens=*" %%P in ('wmic process where "name='python.exe' and commandline like '%%ikaros-desktop-pet%%'" get processid 2^>nul ^| findstr /R "^[0-9]"') do (
    echo   killing PID %%P...
    taskkill /F /PID %%P >nul 2>&1
    if !ERRORLEVEL! EQU 0 set "KILLED=1"
)

REM Also try via window title fallback
for /f "tokens=*" %%P in ('tasklist /FI "WINDOWTITLE eq 🪶" /FO CSV 2^>nul ^| findstr /I "python.exe"') do (
    echo   killing window-titled process...
    for /f "tokens=2 delims=," %%Q in ("%%P") do (
        taskkill /F /PID %%Q >nul 2>&1
        set "KILLED=1"
    )
)

if "%KILLED%"=="1" (
    echo.
    echo   ✓ Stopped.
) else (
    echo.
    echo   Pet is not running.
)

echo.
pause
