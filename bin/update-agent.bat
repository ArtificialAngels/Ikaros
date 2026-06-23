@echo off
REM ============================================================
REM Hermes - Update hermes-agent from upstream (NousResearch)
REM
REM Downloads the latest main branch from GitHub and replaces
REM the hermes-agent/ directory. Runtime data (data/hermes-agent/)
REM is NOT affected — only source code is updated.
REM
REM Usage:
REM   bin\update-agent.bat           (update to latest main)
REM   bin\update-agent.bat v0.17.0   (update to specific tag)
REM   bin\update-agent.bat status    (show current version)
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "AGENT_DIR=%HERMES_ROOT%\hermes-agent"
set "PY=%HERMES_ROOT%\portable-python\python.exe"
set "UPSTREAM_REPO=NousResearch/hermes-agent"
set "UPSTREAM_BRANCH=main"

REM ---- Auto-detect system proxy if HTTPS_PROXY is not already set ----
REM Windows stores proxy in the registry (IE/Edge settings); CLI tools like
REM aria2c / Python urllib do NOT read it automatically.  We read
REM it here and export HTTPS_PROXY + HTTP_PROXY so both download tiers
REM go through the same proxy the browser uses.
for /f "tokens=*" %%P in ('powershell -NoProfile -ExecutionPolicy Bypass -File "%HERMES_ROOT%\bin\_detect_proxy.ps1" 2^>nul') do (
    if not defined HTTPS_PROXY (
        echo   [proxy] Detected system proxy: %%P
        set "HTTPS_PROXY=%%P"
        set "HTTP_PROXY=%%P"
    )
)

REM ---- Read current version from pyproject.toml ----
set "CURRENT_VERSION=unknown"
if exist "%AGENT_DIR%\pyproject.toml" (
    for /f "tokens=2 delims== " %%V in ('findstr /R "^version" "%AGENT_DIR%\pyproject.toml"') do (
        set "CURRENT_VERSION=%%~V"
    )
)

if "%~1"=="status" (
    echo hermes-agent update status
    echo   Current version:  %CURRENT_VERSION%
    echo   Source:           %AGENT_DIR%
    echo   Upstream:         https://github.com/%UPSTREAM_REPO%
    echo   Branch:           %UPSTREAM_BRANCH%
    echo.
    echo Checking for updates...
    "%PY%" -c "import urllib.request,json; r=urllib.request.urlopen('https://api.github.com/repos/%UPSTREAM_REPO%/commits/%UPSTREAM_BRANCH%',timeout=10); d=json.loads(r.read()); print('  Latest commit:  ' + d['sha'][:10]); print('  Date:           ' + d['commit']['committer']['date']); print('  Message:        ' + d['commit']['message'].split(chr(10))[0][:80])" 2>nul
    if errorlevel 1 echo   [WARN] Could not reach GitHub API
    goto :eof
)

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=%UPSTREAM_BRANCH%"

echo ============================================================
echo   hermes-agent upstream update
echo.
echo   Current version:  %CURRENT_VERSION%
echo   Target:           %TARGET%
echo   Upstream:         https://github.com/%UPSTREAM_REPO%
echo ============================================================
echo.

REM ---- Step 1: Backup current hermes-agent/ ----
set "BACKUP_DIR=%HERMES_ROOT%\data\_backup\hermes-agent_%DATE:~10,4%%DATE:~4,2%%DATE:~7,2%"
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%" 2>nul
echo [1/4] Backing up current hermes-agent/ ...
xcopy /E /I /Q /Y "%AGENT_DIR%" "%BACKUP_DIR%" >nul 2>&1
if errorlevel 1 (
    echo   [WARN] Backup may be incomplete, continuing anyway...
) else (
    echo   OK: backup at %BACKUP_DIR%
)

REM ---- Step 2: Download from GitHub ----
REM Two-tier fallback:
REM   1. aria2c  — multi-threaded with proxy support; 300 s timeout.
REM   2. BITS    — Windows Background Transfer; slowest but most resilient.
REM
REM Both respect HTTPS_PROXY (auto-detected from Windows system proxy above).
set "ZIP_URL=https://github.com/%UPSTREAM_REPO%/archive/refs/heads/%TARGET%.zip"
set "ZIP_FILE=%TEMP%\hermes-agent-update.zip"
set "EXTRACT_DIR=%TEMP%\hermes-agent-extract"

echo [2/4] Downloading from GitHub (%TARGET%)...
echo   URL: %ZIP_URL%
echo.

if exist "%ZIP_FILE%" del "%ZIP_FILE%" 2>nul

REM --- Method 1: aria2c with 300-second ceiling ---
set "DL_OK=0"
if exist "%HERMES_ROOT%\runtime\aria2c.exe" (
    echo   [aria2c] Starting ^(timeout: 300s^)...
    del "%ZIP_FILE%.aria2" 2>nul
    set "ARIA_PROXY="
    if defined HTTPS_PROXY set "ARIA_PROXY=--all-proxy=!HTTPS_PROXY!"
    start /b "" "%HERMES_ROOT%\runtime\aria2c.exe" -x16 -s16 --connect-timeout=30 --lowest-speed-limit=512 !ARIA_PROXY! --console-log-level=error --summary-interval=10 -d "%TEMP%" -o "hermes-agent-update.zip" "%ZIP_URL%"
    set "ARIA_PID=!ERRORLEVEL!"
    REM start /b always returns 0; capture real PID via tasklist
    for /f "tokens=2" %%P in ('tasklist /FI "IMAGENAME eq aria2c.exe" /FO LIST 2^>nul ^| findstr /R "PID:"') do set "ARIA_PID=%%P"
    set "ARIA_START=0"
    for /f "tokens=4 delims=]" %%T in ('^<nul set /p^=x ^| find /v "" ^| findstr .') do REM noop
    REM Use a simple seconds counter via nested loops
    for /L %%s in ^(1,1,300^) do ^(
        tasklist /FI "PID eq !ARIA_PID!" 2^>nul | findstr /I "aria2c" ^>nul 2^>^&1
        if errorlevel 1 ^(
            REM process exited — check file
            if exist "%ZIP_FILE%" if not exist "%ZIP_FILE%.aria2" ^(
                set "DL_OK=1"
                goto :aria_done
            ^)
            REM exited but file incomplete
            goto :aria_done
        ^)
        timeout /t 1 /nobreak ^>nul 2^>^&1
    ^)
    REM 300 s elapsed — kill aria2c
    echo   [aria2c] Timeout after 300s, aborting.
    taskkill /PID !ARIA_PID! /F ^>nul 2^>^&1
    del "%ZIP_FILE%" 2>nul
    del "%ZIP_FILE%.aria2" 2>nul
) else ^(
    echo   [aria2c] Not found, skipping.
^)
:aria_done
if "!DL_OK!"=="1" ^(
    echo   [aria2c] OK
    goto :download_ok
^)
echo   [aria2c] Failed or timed out.
echo.

REM --- Method 2: PowerShell BITS (last resort) ---
echo   [BITS] Falling back to Windows BITS transfer...
powershell -NoProfile -Command "try { Start-BitsTransfer -Source '%ZIP_URL%' -Destination '%ZIP_FILE%' -ErrorAction Stop } catch { Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 ^(
    echo.
    echo   [FAIL] All download methods failed.
    echo          aria2c  : timed out or errored
    echo          BITS    : transfer failed
    echo          Check network / proxy settings.
    goto :cleanup
^)
echo   [BITS] OK

:download_ok
echo   Downloaded: %ZIP_FILE%

REM ---- Step 3: Extract and replace ----
echo [3/4] Extracting and replacing hermes-agent/ ...
if exist "%EXTRACT_DIR%" rmdir /S /Q "%EXTRACT_DIR%" 2>nul

powershell -NoProfile -Command "Expand-Archive -Path '%ZIP_FILE%' -DestinationPath '%EXTRACT_DIR%' -Force" >nul 2>&1
if errorlevel 1 (
    echo   [FAIL] Extract failed.
    goto :cleanup
)

REM Find the extracted directory (hermes-agent-main/ or hermes-agent-<tag>/)
set "EXTRACTED="
for /d %%D in ("%EXTRACT_DIR%\hermes-agent-*") do set "EXTRACTED=%%D"
if not defined EXTRACTED (
    echo   [FAIL] Could not find extracted hermes-agent directory.
    goto :cleanup
)
echo   Found: %EXTRACTED%

REM Remove old hermes-agent/ (but preserve data/)
echo   Removing old hermes-agent/ ...
rmdir /S /Q "%AGENT_DIR%" 2>nul

REM Move new one in place
move /Y "%EXTRACTED%" "%AGENT_DIR%" >nul 2>&1
if errorlevel 1 (
    echo   [FAIL] Could not move new source into place.
    echo   Restoring from backup...
    xcopy /E /I /Q /Y "%BACKUP_DIR%" "%AGENT_DIR%" >nul 2>&1
    goto :cleanup
)

REM ---- Step 4: Verify ----
echo [4/4] Verifying update...
if not exist "%AGENT_DIR%\pyproject.toml" (
    echo   [FAIL] pyproject.toml missing after update!
    echo   Restoring from backup...
    rmdir /S /Q "%AGENT_DIR%" 2>nul
    xcopy /E /I /Q /Y "%BACKUP_DIR%" "%AGENT_DIR%" >nul 2>&1
    goto :cleanup
)

set "NEW_VERSION=unknown"
for /f "tokens=2 delims== " %%V in ('findstr /R "^version" "%AGENT_DIR%\pyproject.toml"') do set "NEW_VERSION=%%~V"

echo.
echo ============================================================
echo   Update complete!
echo.
echo   Previous:  %CURRENT_VERSION%
echo   Current:   %NEW_VERSION%
echo   Backup:    %BACKUP_DIR%
echo.
echo   NOTE: You must restart Hermes for changes to take effect:
echo         bin\hermes-stop.bat  ^&^&  bin\hermes-all.bat
echo ============================================================

:cleanup
if exist "%ZIP_FILE%" del "%ZIP_FILE%" 2>nul
if exist "%EXTRACT_DIR%" rmdir /S /Q "%EXTRACT_DIR%" 2>nul
endlocal
exit /b 0
