@echo off
REM ============================================================
REM Hermes - First-run / startup environment check
REM
REM Detects GPU, downloads missing GPU runtime (cudart/cublas) via
REM gopeed-web, and reports back. Idempotent - re-runs are no-ops.
REM
REM Exit codes:
REM   0 = OK (GPU ready OR pure CPU)
REM   1 = WARN (had to fall back, see log)
REM   2 = ERROR (Python / hermes not found)
REM
REM Usage:
REM   bin\hermes-firstrun.bat                (auto: status + install if needed)
REM   bin\hermes-firstrun.bat status
REM   bin\hermes-firstrun.bat check
REM   bin\hermes-firstrun.bat install
REM
REM v2: 2026-06-06, replaces setup-runtime.bat for the boot-time check.
REM setup-runtime.bat still works for one-time first installation.
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "PY=%HERMES_ROOT%\portable-python\python.exe"
set "LOG=%HERMES_ROOT%\hermes\data\logs\firstrun.log"

REM ---- Ensure log dir ----
if not exist "%HERMES_ROOT%\hermes\data\logs" mkdir "%HERMES_ROOT%\hermes\data\logs" 2>nul

echo ============================================================  >> "%LOG%"
echo  Hermes Firstrun - %DATE% %TIME%  mode=%1  >> "%LOG%"
echo ============================================================  >> "%LOG%"

REM ---- Pick subcommand ----
set "MODE=%1"
if "%MODE%"=="" set "MODE=auto"

if not exist "%PY%" (
    echo [FAIL] Python not found: %PY%
    exit /b 2
)

if "%MODE%"=="status" goto :status
if "%MODE%"=="check"  goto :check
if "%MODE%"=="install" goto :install

REM ---- "auto" mode: check + auto-install missing CUDA runtime ----
:check
echo [firstrun] checking environment...
"%PY%" -m hermes.firstrun check
set "RC=%ERRORLEVEL%"
echo [firstrun] check exit=%RC%
if "%MODE%"=="check" exit /b %RC%
if %RC%==0 goto :done
if %RC%==1 (
    echo [firstrun] CUDA runtime DLLs missing (cudart64_12.dll, cublas64_12.dll)
    echo [firstrun] Auto-downloading ~391MB from GitHub (one-time, ~3-5 min)...
    echo [firstrun] To skip, press Ctrl+C now. GPU will run on CPU.
    timeout /t 3 /nobreak >nul
    goto :install
)
if %RC%==2 (
    echo [firstrun] check failed (err=2)
    exit /b 2
)
exit /b %RC%

:install
echo [firstrun] installing missing components (this may take 10-30 minutes)...
"%PY%" -m hermes.firstrun install
set "RC=%ERRORLEVEL%"
echo [firstrun] install exit=%RC%
if "%MODE%"=="install" exit /b %RC%
goto :done

:status
"%PY%" -m hermes.firstrun status
set "RC=%ERRORLEVEL%"
exit /b %RC%

:done
echo [firstrun] done.
exit /b 0
