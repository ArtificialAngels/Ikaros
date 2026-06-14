@echo off
REM ============================================================
REM Hermes - Portable environment bootstrap (v2, 2026-06-14)
REM
REM Idempotent: detects and downloads missing pieces:
REM   1. portable-python/  (Python 3.12.10 embed-amd64, ~10 MB)
REM   2. runtime/llama-server-*.exe  (llama.cpp b9503 with CUDA 12.4,
REM                                   cudart/cublas bundled, ~250 MB)
REM   2b. runtime/cuda/<v>/  (extra CUDA 11.8 / 13.0, on-demand)
REM   3. runtime/node23/    (Node.js 23.11.1 win-x64, ~30 MB)
REM                                   Required by the webui module (modules/webui/).
REM                                   The npm global install (hermes-web-ui) is NOT
REM                                   auto-downloaded -- it lives in a sibling
REM                                   private repo. If runtime/node23/node_modules/
REM                                   hermes-web-ui is absent, webui will fall back
REM                                   to the dev source under .\hermes-web-ui\
REM                                   (which the user must clone separately).
REM   4. data/models/*.gguf  (checked for presence, not auto-downloaded)
REM
REM Each piece is checked separately. Already-present pieces are
REM skipped (idempotent re-runs are fast and free). Downloads use
REM aria2c when present (16-thread parallel), else PowerShell
REM BITS. Both go to the OS temp dir, then a final .NET Expand-
REM Archive step drops them under %HERMES_ROOT%.
REM
REM Exit codes:
REM   0 = all pieces present or successfully installed
REM   1 = at least one piece failed (caller may continue, just warns)
REM
REM Usage:
REM   bin\setup-portable.bat           (check + install missing)
REM   bin\setup-portable.bat status    (check only, no install)
REM   bin\setup-portable.bat python    (just the python piece)
REM   bin\setup-portable.bat runtime   (just the llama.cpp pieces)
REM   bin\setup-portable.bat node      (just the Node.js piece)
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

REM No default model download — users place their own .gguf files in data/models/.
REM The hermes-models.py CLI or the WebUI model manager can download models.
set "HAS_MODEL=0"
if exist "%HERMES_ROOT%\data\models\*.gguf" set "HAS_MODEL=1"

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
echo   [1/4] portable-python  ^(?^)
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
set "LLAMA_CUDA=%HERMES_ROOT%\runtime\cuda\12.4\llama-server-cuda-12.4.exe"
set "LLAMA_CPU=%HERMES_ROOT%\runtime\llama-server.exe"
if "%MODE%"=="python" goto :check_model
if "%MODE%"=="model"  goto :check_model
if "%MODE%"=="node"   goto :check_node
if "%MODE%"=="status" (
    if exist "%LLAMA_CUDA%" (
        echo [setup-portable] status: runtime/llama-server present
    ) else (
        if exist "%LLAMA_CPU%" (
            echo [setup-portable] status: runtime/llama-server present
        ) else (
            echo [setup-portable] status: runtime/llama-server MISSING
            set "MISSING=1"
        )
    )
    goto :check_node
)

if exist "%LLAMA_CUDA%" goto :check_model
if exist "%LLAMA_CPU%" goto :check_model
echo.
echo ============================================================
echo   [2/4] runtime/llama-server  ^(?^)
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

REM Move extracted files to runtime/. Phase 8 multi-version layout:
REM   runtime/                     -- CPU-only DLLs (ggml-*.dll, llama.dll, llama-server.exe)
REM   runtime/cuda/12.4/           -- CUDA 12.4 binaries + DLLs
REM Files matching the CUDA-version pattern go into the cuda/<ver> subdir.
if not exist "%RUNTIME_DIR%\cuda\12.4" mkdir "%RUNTIME_DIR%\cuda\12.4" >nul 2>&1
for %%F in ("%TEMP%\hermes-llama-extract\*") do (
    set "FNAME=%%~nxF"
    echo "%FNAME%" | findstr /I /R "cublas cudart cublasLt ggml-cuda llama-server-cuda" >nul 2>&1
    if not errorlevel 1 (
        move /Y "%%F" "%RUNTIME_DIR%\cuda\12.4\" >nul 2>&1
    ) else (
        move /Y "%%F" "%RUNTIME_DIR%\" >nul 2>&1
    )
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
REM 2b. Optional CUDA 11.8 / 13.0 runtimes (Phase 8)
REM Only download when the active driver requires it; otherwise
REM skip to save disk space (~250 MB per version).
REM ============================================================
:check_cuda_extra
set "CUDA_EXTRA_NEEDED="
for /f "usebackq tokens=*" %%D in (`"%HERMES_ROOT%\portable-python\python.exe" -m modules.env_bootstrap.gpu_detect recommend 2^>nul`) do (
    set "CUDA_REC=%%D"
)
if /i "%CUDA_REC%"=="12.4" goto :check_node
if /i "%CUDA_REC%"=="cpu"   goto :check_node

echo.
echo ============================================================
echo   [2b] runtime/cuda/%CUDA_REC%/  ^(?^)
echo   Detected driver recommends CUDA %CUDA_REC% but only
echo   CUDA 12.4 is bundled. Downloading llama.cpp build for
echo   CUDA %CUDA_REC% (~250 MB)...
echo ============================================================
echo.

set "EXTRA_DIR=%HERMES_ROOT%\runtime\cuda\%CUDA_REC%"
if not exist "%EXTRA_DIR%" mkdir "%EXTRA_DIR%" >nul 2>&1

REM Map CUDA version -> ggml-org release asset suffix.
if /i "%CUDA_REC%"=="11.8" set "CUDA_ASSET=cuda-11.8"
if /i "%CUDA_REC%"=="13.0" set "CUDA_ASSET=cuda-12.6"
set "EXTRA_URL=https://github.com/ggml-org/llama.cpp/releases/download/%LLAMA_VERSION%/llama-%LLAMA_VERSION%-bin-win-%CUDA_ASSET%-x64.zip"
set "EXTRA_ZIP=%TEMP%\hermes-llama-%CUDA_REC%.zip"

call :download "%EXTRA_URL%" "%EXTRA_ZIP%"
if errorlevel 1 (
    echo [setup-portable] FAIL: CUDA %CUDA_REC% download failed.
    echo [setup-portable]   Will fall back to bundled CUDA 12.4 runtime.
    goto :check_node
)

echo   Extracting to %EXTRA_DIR% ...
powershell -NoProfile -Command "Expand-Archive -Path '%EXTRA_ZIP%' -DestinationPath '%TEMP%\hermes-llama-extract' -Force" >nul 2>&1
for %%F in ("%TEMP%\hermes-llama-extract\*") do (
    set "FNAME=%%~nxF"
    echo "%FNAME%" | findstr /I /R "cublas cudart cublasLt ggml-cuda llama-server-cuda" >nul 2>&1
    if not errorlevel 1 (
        move /Y "%%F" "%EXTRA_DIR%\" >nul 2>&1
    )
)
rmdir /S /Q "%TEMP%\hermes-llama-extract" 2>nul
del "%EXTRA_ZIP%" 2>nul
echo   OK: CUDA %CUDA_REC% runtime in %EXTRA_DIR%
echo.

REM ============================================================
REM 3. runtime/node23/  (Node.js 23.11.1 -- webui runtime)
REM ============================================================
:check_node
set "NODE_DIR=%HERMES_ROOT%\runtime\node23"
set "NODE_EXE=%NODE_DIR%\node.exe"
if "%MODE%"=="python" goto :check_model
if "%MODE%"=="model"  goto :check_model
if "%MODE%"=="status" (
    if not exist "%NODE_EXE%" (
        echo [setup-portable] status: runtime/node23/ MISSING
        set "MISSING=1"
    ) else (
        echo [setup-portable] status: runtime/node23/ present
    )
    goto :check_model
)

if exist "%NODE_EXE%" goto :check_model
echo.
echo ============================================================
echo   [3/4] runtime/node23/  ^(?^)
echo   Node.js 23.11.1 is missing (required for the webui module).
echo   Downloading from nodejs.org (~30 MB)...
echo ============================================================
echo.

set "NODE_VERSION=23.11.1"
set "NODE_URL=https://nodejs.org/dist/v%NODE_VERSION%/node-v%NODE_VERSION%-win-x64.zip"
set "NODE_ZIP=%TEMP%\hermes-node-%NODE_VERSION%.zip"
set "NODE_EXTRACT=%TEMP%\hermes-node-extract"

if not exist "%HERMES_ROOT%\runtime" mkdir "%HERMES_ROOT%\runtime" 2>nul
if not exist "%NODE_DIR%" mkdir "%NODE_DIR%" 2>nul

call :download "%NODE_URL%" "%NODE_ZIP%"
if errorlevel 1 (
    echo [setup-portable] FAIL: Node.js download failed.
    echo [setup-portable]   webui will be unable to start; other modules OK.
    set "MISSING=1"
    goto :check_model
)

echo   Extracting to %NODE_DIR% ...
powershell -NoProfile -Command "Expand-Archive -Path '%NODE_ZIP%' -DestinationPath '%NODE_EXTRACT%' -Force" >nul 2>&1
if errorlevel 1 (
    echo [setup-portable] FAIL: Node.js extract failed.
    set "MISSING=1"
    goto :check_model
)

REM Move node-v23.11.1-win-x64\* into runtime/node23\.
for %%F in ("%NODE_EXTRACT%\node-v%NODE_VERSION%-win-x64\*") do (
    move /Y "%%F" "%NODE_DIR%\" >nul 2>&1
)
rmdir /S /Q "%NODE_EXTRACT%" 2>nul
del "%NODE_ZIP%" 2>nul

if not exist "%NODE_EXE%" (
    echo [setup-portable] FAIL: node.exe still missing after extract.
    set "MISSING=1"
    goto :check_model
)
echo   OK: %NODE_EXE%
echo.

REM Warn if the hermes-web-ui npm global install is missing.
REM We retired the .\hermes-web-ui\ dev-source fallback in 2026-06-15
REM (see AGENTS.md §0.7a); the npm global install is the only supported path.
if not exist "%NODE_DIR%\node_modules\hermes-web-ui\bin\hermes-web-ui.mjs" (
    echo   [WARN] hermes-web-ui npm global install NOT detected at
    echo          %NODE_DIR%\node_modules\hermes-web-ui\
    echo          The webui module will fail unless you install it:
    echo            cd runtime\node23 ^&^& npm install -g hermes-web-ui
    echo.
)

REM ============================================================
REM 4. data/models/ (any .gguf)
REM ============================================================
:check_model
if "%MODE%"=="status" (
    if "%HAS_MODEL%"=="0" (
        echo [setup-portable] status: no .gguf model found in data\models^
        set "MISSING=1"
    ) else (
        echo [setup-portable] status: model present
    )
    goto :summary
)

if "%HAS_MODEL%"=="1" goto :summary
echo.
echo ============================================================
echo   [4/4] data/models/  ^(?^)
echo   No .gguf files found in data\models^. Use hermes-models.py
echo   or the WebUI model manager to download a model.
echo ============================================================
echo.
set "MISSING=1"

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
