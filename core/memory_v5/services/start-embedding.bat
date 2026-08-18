@echo off
REM See docs/scripts/core/memory_v5/services/start-embedding.md

REM Load Ikaros environment
call "%~dp0..\..\core\env\ikaros-env.bat"
if errorlevel 1 (
    echo [FATAL] Ikaros-environment\ikaros-env.bat failed.
    pause
    exit /b 1
)

set "MODEL=%IKAROS_MODEL_EMBEDDING%"
set "LLAMA=%IKAROS_LLAMA_SERVER%"
set "PORT=%IKAROS_PORT_EMBEDDING%"
set "HOST=127.0.0.1"

if not exist "%LLAMA%" (
    echo [FATAL] llama-server not found: %LLAMA%
    pause
    exit /b 1
)

if not exist "%MODEL%" (
    echo [FATAL] Model not found: %MODEL%
    pause
    exit /b 1
)

echo [Ikaros Memory] Starting embedding service on %HOST%:%PORT%
echo [Ikaros Memory] Model: bge-m3-q8_0.gguf
"%LLAMA%" -m "%MODEL%" --host %HOST% --port %PORT% -ngl auto --embedding --pooling mean
