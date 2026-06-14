@echo off
REM ============================================================
REM Hermes - First-run / startup environment check.
REM
REM Detects GPU, downloads missing GPU runtime (cudart/cublas) via
REM the pip-installed nvidia-* packages, and reports back. Idempotent -
REM re-runs are no-ops. Delegates to modules\env_bootstrap\gpu_detect.
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
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

REM ---- Single source of truth: deps\hermes-env.bat ----
call "%~dp0..\deps\hermes-env.bat"
if errorlevel 1 (
    echo [FATAL] could not resolve HERMES_ROOT.
    exit /b 2
)
set "LOG=%HERMES_LOGS%\firstrun.log"

REM ---- Ensure log dir ----
if not exist "%HERMES_LOGS%" mkdir "%HERMES_LOGS%" 2>nul

echo ============================================================  >> "%LOG%"
echo  Hermes Firstrun - %DATE% %TIME%  mode=%1  >> "%LOG%"
echo ============================================================  >> "%LOG%"

REM ---- Pick subcommand ----
set "MODE=%1"
if "%MODE%"=="" set "MODE=auto"

if not exist "%HERMES_PYTHON%" (
    echo [FAIL] Python not found: %HERMES_PYTHON%
    exit /b 2
)

if "%MODE%"=="status" goto :status
if "%MODE%"=="check"  goto :check
if "%MODE%"=="install" goto :install

REM ---- "auto" mode: check + auto-install missing CUDA runtime ----
:check
echo [firstrun] checking environment...
"%HERMES_PYTHON%" -m modules.env_bootstrap.gpu_detect check
set "RC=%ERRORLEVEL%"
echo [firstrun] check exit=%RC%
if "%MODE%"=="check" exit /b %RC%
if %RC%==0 goto :done
if %RC%==1 (
    echo [firstrun] CUDA runtime DLLs missing (cudart / cublas for driver-detected version)
    echo [firstrun] Auto-downloading ~100MB via pip (one-time, ~1-3 min)...
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
"%HERMES_PYTHON%" -m modules.env_bootstrap.gpu_detect install
set "RC=%ERRORLEVEL%"
echo [firstrun] install exit=%RC%
if "%MODE%"=="install" exit /b %RC%
goto :done

:status
"%HERMES_PYTHON%" -m modules.env_bootstrap.gpu_detect status
set "RC=%ERRORLEVEL%"
exit /b %RC%

:done
echo [firstrun] done.
exit /b 0
