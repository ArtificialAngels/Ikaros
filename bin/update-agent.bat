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
set "ZIP_URL=https://github.com/%UPSTREAM_REPO%/archive/refs/heads/%TARGET%.zip"
set "ZIP_FILE=%TEMP%\hermes-agent-update.zip"
set "EXTRACT_DIR=%TEMP%\hermes-agent-extract"

echo [2/4] Downloading from GitHub (%TARGET%)...
echo   URL: %ZIP_URL%

REM Try aria2c first (faster), then PowerShell BITS
if exist "%HERMES_ROOT%\runtime\aria2c.exe" (
    "%HERMES_ROOT%\runtime\aria2c.exe" -x16 -s16 --console-log-level=error --summary-interval=0 -d "%TEMP%" -o "hermes-agent-update.zip" "%ZIP_URL%" >nul 2>&1
) else (
    powershell -NoProfile -Command "try { Start-BitsTransfer -Source '%ZIP_URL%' -Destination '%ZIP_FILE%' -ErrorAction Stop } catch { exit 1 }" >nul 2>&1
)
if errorlevel 1 (
    echo   [FAIL] Download failed. Check network connectivity.
    goto :cleanup
)
echo   OK: downloaded to %ZIP_FILE%

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
