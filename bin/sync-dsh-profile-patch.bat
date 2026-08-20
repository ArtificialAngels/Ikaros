@echo off
setlocal EnableExtensions

REM ============================================================
REM  sync-dsh-profile-patch.bat
REM
REM  Sync cordis.patch.yml from canonical source to user-level
REM  profile (~/.dsh/profiles/web/cordis.patch.yml).
REM  Why: dsh web mode (without --patch) loads the user-level patch.
REM  If out of sync, web starts with stale MCP / LSP / persona config.
REM
REM  Usage: bin\sync-dsh-profile-patch.bat
REM  Hook: .githooks\pre-commit (or git hook) calls this after cordis.patch.yml changes
REM
REM  ASCII only -- cmd parses bat in ANSI/GBK, UTF-8 comments break it.
REM ============================================================

set "IKAROS_ROOT=%~dp0.."
for %%i in ("%IKAROS_ROOT%") do set "IKAROS_ROOT=%%~fi"

set "SRC=%IKAROS_ROOT%\core\ikaros-dsh\cordis.patch.yml"
set "DST=%USERPROFILE%\.dsh\profiles\web\cordis.patch.yml"

if not exist "%SRC%" (
    echo [FATAL] source patch not found: %SRC%
    exit /b 1
)

if not exist "%DST%" (
    echo [INFO] first run: creating user-level profile at %DST%
    mkdir "%USERPROFILE%\.dsh\profiles\web" 2>nul
)

REM 用 xcopy 复制, /Y 覆盖, /Q 静默
xcopy /Y /Q "%SRC%" "%DST%" >nul
if errorlevel 1 (
    echo [FATAL] failed to copy %SRC% to %DST%
    exit /b 1
)

echo [OK] dsh profile patch synced: %DST%
exit /b 0
