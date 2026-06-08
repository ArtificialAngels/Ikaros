@echo off
REM ============================================================
REM Hermes - Portable environment bootstrap (v1, 2026-06-08)
REM
REM Idempotent: detects and downloads missing pieces:
REM   1. portable-python/  (Python 3.12.10 embed-amd64, ~10 MB)
REM   2. runtime/llama-server-*.exe  (llama.cpp b9503 with CUDA 12.4,
REM                                   cudart/cublas bundled, ~250 MB)
REM   3. data/models/Qwen2.5-3B-Instruct-Q4_K_M.gguf
REM      (Hugging Face official mirror, ~2 GB)
REM
REM Each piece is checked separately. Already-present pieces are
REM skipped (idempotent re-runs are fast and free). Downloads use
REM aria2c when present (16-thread parallel), else PowerShell
REM BITS. Both go to the OS temp dir, then a final .NET Expand-
REM Archive / Expand-7Zip step drops them under %HERMES_ROOT%.
REM
REM Exit codes:
REM   0 = all pieces present or successfully installed
REM   1 = at least one piece failed (caller may continue, just warns)
REM
REM Usage:
REM   bin\setup-portable.bat           (check + install missing)
REM   bin\setup-portable.bat status    (check only, no install)
REM   bin\setup-portable.bat python    (just the python piece)
REM   bin\setup-portable.bat runtime   (just the runtime piece)
REM   bin\setup-portable.bat model     (just the model piece)
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "LOG=%HERMES_ROOT%\hermes\data\logs\setup-portable.log"
if not exist "%HERMES_ROOT%\hermes\data\logs" mkdir "%HERMES_ROOT%\hermes\data\logs" 2>nul

echo ============================================================  >> "%LOG%"
echo  Hermes Setup-Portable - %DATE% %TIME%  mode=%1            >> "%LOG%"
echo ============================================================  >> "%LOG%"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=auto"

REM ---- Pick downloader ----
set "ARIA=%HERMES_ROOT%\runtime\aria2c.exe"
set "USE_ARIA=0"
if exist "%ARIA%" set "USE_ARIA=1"

REM ---- Default model URL (Hugging Face Qwen2.5-3B-Instruct Q4_K_M) ----
set "DEFAULT_MODEL_URL=https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf"
set "DEFAULT_MODEL_PATH=%HERMES_ROOT%\data\models\Qwen2.5-3B-Instruct-Q4_K_M.gguf"

REM ============================================================
REM 1. portable-python
REM ============================================================
:check_python
set "PY=%HERMES_ROOT%\portable-python\python.exe"
if "%MODE%"=="runtime" goto :check_runtime
if "%MODE%"=="model"   goto :check_model
if "%MODE%"=="status" (
    if not exist "%PY%" (
        echo [setup-portable] status: portable-python MISSING
        set "MISSING=1"
    ) else (
        echo [setup-portable] status: portable-python present
    )
    goto :check_runtime
)

if exist "%PY%" goto :check_runtime
echo.
echo ============================================================
echo   [1/3] portable-python  ^(?^)
echo   Python embed-amd64 is missing. Downloading from python.org...
echo ============================================================
echo.

set "PYTHON_VERSION=3.12.10"
set "PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/python-%PYTHON_VERSION%-embed-amd64.zip"
set "PYTHON_ZIP=%TEMP%\hermes-python-%PYTHON_VERSION%-embed-amd64.zip"
set "PYTHON_DIR=%HERMES_ROOT%\portable-python"

if not exist "%PYTHON_DIR%" mkdir "%PYTHON_DIR%" 2>nul

call :download "%PYTHON_URL%" "%PYTHON_ZIP%"
if errorlevel 1 (
    echo [setup-portable] FAIL: Python download failed.
    set "MISSING=1"
    goto :check_runtime
)

echo   Extracting to %PYTHON_DIR% ...
powershell -NoProfile -Command "Expand-Archive -Path '%PYTHON_ZIP%' -DestinationPath '%PYTHON_DIR%' -Force" >nul 2>&1
if errorlevel 1 (
    echo [setup-portable] FAIL: Python extract failed.
    set "MISSING=1"
    goto :check_runtime
)
del "%PYTHON_ZIP%" 2>nul

if not exist "%PY%" (
    echo [setup-portable] FAIL: python.exe still missing after extract.
    set "MISSING=1"
    goto :check_runtime
)
echo   OK: %PY%
echo.

REM ============================================================
REM 2. runtime/llama-server
REM ============================================================
:check_runtime
set "LLAMA_CUDA=%HERMES_ROOT%\runtime\llama-server-cuda-12.4.exe"
set "LLAMA_CPU=%HERMES_ROOT%\runtime\llama-server.exe"
if "%MODE%"=="python" goto :check_model
if "%MODE%"=="model"  goto :check_model
if "%MODE%"=="status" (
    if not exist "%LLAMA_CUDA%" if not exist "%LLAMA_CPU%" (
        echo [setup-portable] status: runtime/llama-server MISSING
        set "MISSING=1"
    ) else (
        echo [setup-portable] status: runtime/llama-server present
    )
    goto :check_model
)

if exist "%LLAMA_CUDA%" goto :check_model
if exist "%LLAMA_CPU%" goto :check_model
echo.
echo ============================================================
echo   [2/3] runtime/llama-server  ^(?^)
echo   llama.cpp b9503 (with CUDA 12.4) is missing. Downloading
echo   from the official ggml-org release ~250MB...
echo ============================================================
echo.

set "LLAMA_VERSION=b9503"
set "LLAMA_URL=https://github.com/ggml-org/llama.cpp/releases/download/%LLAMA_VERSION%/llama-%LLAMA_VERSION%-bin-win-cuda-12.4-x64.zip"
set "LLAMA_ZIP=%TEMP%\hermes-llama-%LLAMA_VERSION%.zip"
set "RUNTIME_DIR=%HERMES_ROOT%\runtime"

call :download "%LLAMA_URL%" "%LLAMA_ZIP%"
if errorlevel 1 (
    echo [setup-portable] FAIL: llama.cpp download failed.
    set "MISSING=1"
    goto :check_model
)

echo   Extracting to %RUNTIME_DIR% ...
powershell -NoProfile -Command "Expand-Archive -Path '%LLAMA_ZIP%' -DestinationPath '%TEMP%\hermes-llama-extract' -Force" >nul 2>&1
if errorlevel 1 (
    echo [setup-portable] FAIL: llama.cpp extract failed.
    set "MISSING=1"
    goto :check_model
)

REM Move extracted files to runtime/. The official zip puts binaries at root.
for %%F in ("%TEMP%\hermes-llama-extract\*") do (
    move /Y "%%F" "%RUNTIME_DIR%\" >nul 2>&1
)
rmdir /S /Q "%TEMP%\hermes-llama-extract" 2>nul
del "%LLAMA_ZIP%" 2>nul

if not exist "%LLAMA_CUDA%" if not exist "%LLAMA_CPU%" (
    echo [setup-portable] FAIL: llama-server binary still missing after extract.
    set "MISSING=1"
    goto :check_model
)
echo   OK: %LLAMA_CUDA%
echo.

REM ============================================================
REM 3. data/models/Qwen2.5-3B (default model)
REM ============================================================
:check_model
if "%MODE%"=="status" (
    if not exist "%DEFAULT_MODEL_PATH%" (
        echo [setup-portable] status: default model MISSING
        set "MISSING=1"
    ) else (
        echo [setup-portable] status: default model present
    )
    goto :summary
)

if exist "%DEFAULT_MODEL_PATH%" goto :summary
echo.
echo ============================================================
echo   [3/3] data/models/Qwen2.5-3B  ^(?^)
echo   No .gguf found in data\models\. Downloading the default
echo   3B model from Hugging Face ~2GB (this is the biggest
echo   piece; on slow connections it can take 10-30+ minutes).
echo ============================================================
echo.

if not exist "%HERMES_ROOT%\data\models" mkdir "%HERMES_ROOT%\data\models" 2>nul

call :download "%DEFAULT_MODEL_URL%" "%DEFAULT_MODEL_PATH%"
if errorlevel 1 (
    echo [setup-portable] FAIL: default model download failed.
    set "MISSING=1"
    goto :summary
)
echo   OK: %DEFAULT_MODEL_PATH%
echo.

REM ============================================================
REM Summary
REM ============================================================
:summary
echo ============================================================
if defined MISSING (
    echo   Hermes Setup-Portable: COMPLETE WITH WARNINGS
    echo   Some pieces failed. Re-run bin\setup-portable.bat to retry.
) else (
    echo   Hermes Setup-Portable: ALL OK
)
echo ============================================================
if "%MODE%"=="status" (
    if defined MISSING exit /b 1
    exit /b 0
)
if defined MISSING exit /b 1
exit /b 0

REM ============================================================
REM Helper: download URL -> path (uses aria2c if present, else BITS)
REM Returns non-zero on failure.
REM ============================================================
:download
set "DL_URL=%~1"
set "DL_DEST=%~2"
echo   [download] %DL_URL%
echo   [to]       %DL_DEST%
if "%USE_ARIA%"=="1" (
    "%ARIA%" -x16 -s16 --console-log-level=error --summary-interval=0 -d "%TEMP%" -o "%~n2" "%DL_URL%" >nul 2>&1
    if errorlevel 1 exit /b 1
    REM aria2c downloads to %TEMP% with name %~n2; move to DL_DEST
    move /Y "%TEMP%\%~n2" "%DL_DEST%" >nul 2>&1
    if errorlevel 1 exit /b 1
) else (
    powershell -NoProfile -Command "try { Start-BitsTransfer -Source '%DL_URL%' -Destination '%DL_DEST%' -ErrorAction Stop } catch { exit 1 }" >nul 2>&1
    if errorlevel 1 exit /b 1
)
exit /b 0
