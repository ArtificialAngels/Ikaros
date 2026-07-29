@echo off
REM Stop N.E.K.O desktop + backend

echo [neko-stop] Stopping N.E.K.O...

REM Kill by port (most reliable)
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| find ":48911" ^| find "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| find ":48912" ^| find "LISTENING"') do taskkill /F /PID %%a >nul 2>&1

REM Kill Electron desktop
taskkill /F /IM N.E.K.O.exe >nul 2>&1

REM Verify
netstat -aon 2>nul | find ":48911" | find "LISTENING" >nul 2>&1
if errorlevel 1 (echo [neko-stop] :48911 cleared) else (echo [neko-stop] WARN: :48911 still alive)
netstat -aon 2>nul | find ":48912" | find "LISTENING" >nul 2>&1
if errorlevel 1 (echo [neko-stop] :48912 cleared) else (echo [neko-stop] WARN: :48912 still alive)

echo [neko-stop] Done
