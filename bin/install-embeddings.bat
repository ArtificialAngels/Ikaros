@echo off
REM ============================================================
REM Hermes - Install real embedding model
REM
REM Optional: improves RAG / semantic search quality.
REM Adds sentence-transformers + all-MiniLM-L6-v2 (~80MB) to portable-python.
REM
REM Without this, Hermes uses hash-based pseudo-embeddings (UI works, search quality is poor).
REM
REM Re-run safe: idempotent.
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

REM ---- Single source of truth: deps\hermes-env.bat ----
call "%~dp0..\deps\hermes-env.bat"
if errorlevel 1 (
    echo [FATAL] could not resolve HERMES_ROOT.
    exit /b 2
)
set "CACHE_DIR=%HERMES_ROOT%\hermes\data\models\embedding"

if not exist "%CACHE_DIR%" mkdir "%CACHE_DIR%"

echo ============================================================
echo   Hermes - Embedding Model Installer
echo.
echo   This will:
echo     1. pip install sentence-transformers (~250MB with torch)
echo     2. Pre-download all-MiniLM-L6-v2 model (~80MB)
echo.
echo   Total: ~330MB download. Skip if you only use Hermes offline.
echo ============================================================
echo.
set /p "CONFIRM=Proceed? (y/N) "
if /i not "%CONFIRM%"=="y" (
    echo Cancelled.
    pause
    exit /b 0
)

echo.
echo [1/2] Installing sentence-transformers (this can take 1-2 min)...
"%HERMES_PYTHON%" -m pip install --quiet sentence-transformers
if errorlevel 1 (
    echo   [WARN] pip install failed - trying with output
    "%HERMES_PYTHON%" -m pip install sentence-transformers
)

echo.
echo [2/2] Pre-downloading model to %CACHE_DIR%...
set "HF_HOME=%CACHE_DIR%"
"%HERMES_PYTHON%" -c "import os; os.environ['HF_HOME'] = r'%CACHE_DIR%'; from sentence_transformers import SentenceTransformer; m = SentenceTransformer('all-MiniLM-L6-v2', cache_folder=r'%CACHE_DIR%'); print('OK dim=', m.get_sentence_embedding_dimension())"

echo.
echo ============================================================
echo   Done!
echo.
echo   To verify:
echo     "%HERMES_PYTHON%" -c "from sentence_transformers import SentenceTransformer; print(SentenceTransformer('all-MiniLM-L6-v2', cache_folder=r'%CACHE_DIR%').encode(['test']).shape)"
echo.
echo   To use it, restart bin\hermes-all.bat. The v1/embeddings
echo   endpoint will return real semantic vectors.
echo ============================================================
echo.
pause
endlocal
