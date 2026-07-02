@echo off
REM ============================================================
REM Hermes Native Port Discovery
REM ------------------------------------------------------------
REM Purpose:
REM   Hermes native dashboard (`hermes_cli.main serve --port 0`)
REM   uses dynamic port allocation, so the URL changes every
REM   time the process restarts. This script finds the live
REM   port(s) by walking the process tree.
REM
REM Usage:
REM   bin\discover-hermes-ports.bat              (full table + URL)
REM   bin\discover-hermes-ports.bat --url-only   (just the main URL)
REM   bin\discover-hermes-ports.bat --token      (just the session token)
REM   bin\discover-hermes-ports.bat --json       (JSON output)
REM
REM Exit codes:
REM   0  dashboard running, URLs printed
REM   1  no dashboard process found
REM   2  dashboard running but no listening port responded
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "MODE=full"
if /I "%1"=="--url-only" set "MODE=url"
if /I "%1"=="--token"   set "MODE=token"
if /I "%1"=="--json"    set "MODE=json"

REM ---- Step 1+2: enumerate listening ports via PS1 helper ----
echo [1/3] Enumerating listening ports for hermes_cli processes...

set "PS_OUT="
set "LAST_PORT="
for /f "usebackq delims=" %%L in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0discover-hermes-ports.ps1"`) do (
  for /f "tokens=1" %%P in ("%%L") do set "LAST_PORT=%%P"
  set "PS_OUT=!PS_OUT!%%L"
  echo.
)

if "!LAST_PORT!"=="" (
  echo [ERROR] No hermes_cli.main serve process found.
  echo         Is Hermes Agent running? Try:  start Hermes Desktop
  exit /b 1
)

echo [OK] Found:
echo.
echo !PS_OUT!
echo.

REM ---- Step 3: fetch token via dedicated PS1 helper (no shell quoting) ----
echo [2/3] Fetching session token from http://127.0.0.1:!LAST_PORT!/ ...

set "TOKEN="
for /f "usebackq delims=" %%T in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0discover-hermes-token.ps1" -Port !LAST_PORT!`) do (
  set "TOKEN=%%T"
)

if "!TOKEN!"=="" (
  echo [WARN] Port !LAST_PORT! is listening but did not return a session token.
  echo        Dashboard may still be starting up. Try again in 5s.
  exit /b 2
)

set "MAIN_URL=http://127.0.0.1:!LAST_PORT!/"

REM ---- Step 4: output by mode ----
if /I "!MODE!"=="url" (
  echo !MAIN_URL!
  exit /b 0
)
if /I "!MODE!"=="token" (
  echo !TOKEN!
  exit /b 0
)
if /I "!MODE!"=="json" (
  echo {"url":"!MAIN_URL!","token":"!TOKEN!"}
  exit /b 0
)

echo [3/3] Done.
echo.
echo ============================================================
echo   DASHBOARD FOUND
echo ============================================================
echo   URL:    !MAIN_URL!
echo   Token:  !TOKEN!
echo.
echo   Quick test:
echo     curl -H "Authorization: Bearer !TOKEN!" !MAIN_URL!api/memory
echo ============================================================
exit /b 0
