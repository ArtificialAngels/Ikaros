@echo off
REM ============================================================
REM Ikaros Memory - LLM Service (for memory extract)
REM Model: Qwen3-8B (reasoning model, use temperature=0 for direct output)
REM Port:  :8589
REM ============================================================

REM Load Ikaros environment
call "%~dp0..\..\Ikaros-environment\ikaros-env.bat"
if errorlevel 1 (
    echo [FATAL] Ikaros-environment\ikaros-env.bat failed.
    pause
    exit /b 1
)

set "MODEL=%IKAROS_MODEL_LLM%"
set "LLAMA=%IKAROS_LLAMA_SERVER%"
set "PORT=%IKAROS_PORT_LLM%"
set "HOST=127.0.0.1"
set "CTX=8192"
set "GPU_LAYERS=auto"
set "ALIAS=Qwen3-8B"

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

echo [Ikaros Memory] Starting LLM service on %HOST%:%PORT%
echo [Ikaros Memory] Model: qwen3-8b.gguf (context: %CTX%)
"%LLAMA%" -m "%MODEL%" --host %HOST% --port %PORT% -c %CTX% -ngl %GPU_LAYERS% --jinja --alias %ALIAS% --flash-attn auto --cont-batching
