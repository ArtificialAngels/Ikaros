@echo off
REM ============================================================
REM Hermes - Smart llama-server launcher
REM
REM Auto-calculates --n-gpu-layers based on:
REM   - Model file size
REM   - Free VRAM (nvidia-smi)
REM
REM Strategy:
REM   model < vram*0.7  -> 99 (all GPU, full speed)
REM   model < vram*1.2  -> 99 (all GPU + KV cache)
REM   model > vram*1.2  -> calculate partial offload
REM   model > vram*3    -> 0 (all CPU, too big for hybrid)
REM
REM Override:
REM   set LLAMA_NGL=0    force CPU
REM   set LLAMA_NGL=99   force GPU
REM   set LLAMA_NGL=N    force N layers on GPU
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "RUNTIME=%HERMES_ROOT%\runtime"

REM Auto-pick binary
set "LLAMA_BIN=%RUNTIME%\llama-server.exe"
if exist "%RUNTIME%\llama-server-cuda-12.4.exe" set "LLAMA_BIN=%RUNTIME%\llama-server-cuda-12.4.exe"
if exist "%RUNTIME%\llama-server-cuda-11.8.exe" if not exist "%RUNTIME%\llama-server-cuda-12.4.exe" set "LLAMA_BIN=%RUNTIME%\llama-server-cuda-11.8.exe"
if exist "%RUNTIME%\llama-server-vulkan.exe" if "%LLAMA_BIN%"=="%RUNTIME%\llama-server.exe" set "LLAMA_BIN=%RUNTIME%\llama-server-vulkan.exe"

REM Default model (overridden by arg or env)
set "MODEL=%~1"
if "%MODEL%"=="" set "MODEL=%HERMES_ROOT%\data\models\Qwen3.5-35B-A3B-Q4_K_M.gguf"
if not "%LLAMA_MODEL%"=="" set "MODEL=%LLAMA_MODEL%"

if not exist "%MODEL%" (
    echo [ERROR] Model not found: %MODEL%
    exit /b 1
)

REM Get model size in MB via PowerShell (handles >4GB files; set /a is 32-bit)
for /f "tokens=*" %%S in ('powershell -NoProfile -Command "$f = (Get-Item -LiteralPath '%MODEL%').Length; [int][math]::Floor($f / 1MB)"') do set "MODEL_MB=%%S"
echo ============================================================
echo   Hermes - Smart LLM Launcher
echo.
echo   Binary: %LLAMA_BIN%
echo   Model:  %MODEL%
echo   Size:   %MODEL_MB% MB
echo ============================================================
echo.

REM Get free VRAM in MB (NVIDIA only; 0 if not available)
set "VRAM_FREE_MB=0"
where nvidia-smi >nul 2>&1
if not errorlevel 1 (
    REM Use PowerShell to invoke nvidia-smi - avoids cmd for /f quote parsing issues
    for /f "usebackq tokens=*" %%V in (`powershell -NoProfile -Command "$f = (& nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>$null) -split '\n' | Select-Object -First 1; [int]$f"`) do (
        set "VRAM_FREE_MB=%%V"
    )
)
echo   Free VRAM: %VRAM_FREE_MB% MB

REM Calculate NGL
set "NGL=0"
set "MODE=CPU"

if "%LLAMA_NGL%"=="0" (
    set "NGL=0"
    set "MODE=CPU (forced)"
) else if "%LLAMA_NGL%"=="99" (
    set "NGL=99"
    set "MODE=GPU (forced, all layers)"
) else if not "%LLAMA_NGL%"=="" (
    set "NGL=%LLAMA_NGL%"
    set "MODE=GPU (forced, %LLAMA_NGL% layers)"
) else (
    REM Auto-calculate
    if %VRAM_FREE_MB% GTR 0 (
        if %MODEL_MB% LEQ %VRAM_FREE_MB% (
            REM Model fits entirely in VRAM
            set "NGL=99"
            set "MODE=GPU (full offload, model fits in VRAM)"
        ) else (
            REM Calculate partial offload
            REM Use ~70% of free VRAM for weights, reserve 30% for KV cache + overhead
            set /a "VRAM_FOR_MODEL=VRAM_FREE_MB*7/10"
            REM Estimate layer count: most decoder models have ~80-120 layers
            REM Assume model is dense, Q4_K_M ~= 4.5 bits/param, 1B ~= 562MB
            REM So total params ~= model_mb / 0.562
            set /a "TOTAL_PARAMS_B=MODEL_MB*100/56"
            REM Average layer size in MB
            set /a "AVG_LAYER_MB=MODEL_MB/80"
            REM NOTE: Must use bracketed if form, not single-line "if X LSS 1 set Y=1".
            REM The single-line form breaks inside a delayed-expansion else
            REM block, surfacing as "1 was unexpected at this time."
            if !AVG_LAYER_MB! LSS 1 (
                set "AVG_LAYER_MB=1"
            )
            set /a "NGLCALC=VRAM_FOR_MODEL/AVG_LAYER_MB"
            if !NGLCALC! LEQ 0 (
                set "NGLCALC=0"
            )

            REM If too big for hybrid (would only fit 1-2 layers), just go CPU
            if !NGLCALC! LEQ 5 (
                set "NGL=0"
                set "MODE=CPU (model too large for hybrid offload: %MODEL_MB%MB / %VRAM_FREE_MB%MB free)"
            ) else (
                set "NGL=!NGLCALC!"
                set "MODE=Hybrid (estimated !NGLCALC! layers on GPU, rest on CPU)"
            )
        )
    ) else (
        set "NGL=0"
        set "MODE=CPU (no NVIDIA GPU detected)"
    )
)

echo.
echo   NGL (GPU layers):  %NGL%
echo   Strategy:          %MODE%
echo ============================================================
echo.

REM Extract just the filename from the full binary path (used below)
for %%F in ("%LLAMA_BIN%") do set "LLAMA_BIN_NAME=%%~nxF"

REM IMPORTANT: cd to the runtime dir and use a relative model path.
REM llama-server's argv parser doesn't handle Windows paths with spaces
REM (e.g. "E:\Hermes Agent\..." gets split at the space). The
REM absolute path gets dropped to "Agent\data\models\...".
REM Workaround: cd to runtime/ and pass the model via a relative path.
pushd "%RUNTIME%"
REM Compute relative path via a small PowerShell helper file (avoids for/f
REM parsing of paths with spaces).
"%HERMES_ROOT%\portable-python\python.exe" -c "import os,sys; m=os.path.abspath(r'''%MODEL%'''); r=os.path.relpath(m, r'''%RUNTIME%'''); print(r)" > "%TEMP%\hermes_model_rel.txt"
set /p "MODEL_REL=" < "%TEMP%\hermes_model_rel.txt"
del "%TEMP%\hermes_model_rel.txt" 2>nul
echo   [start] cd %CD%  model=!MODEL_REL!
REM IMPORTANT: delegate the actual Start-Process call to a PowerShell script
REM (bin\start-llm.ps1). PowerShell's Start-Process wraps .NET's Process.Start,
REM which gives us a properly detached child process — the same model
REM ComfyUI-aki uses in its matsu.exe WPF launcher. Trying to inline the PS
REM command in this bat (via `start "..." powershell -Command "Start-Process..."`)
REM is fragile due to nested-quote escaping. Having a real .ps1 file keeps the
REM launch logic clean and testable.
start "Hermes-LLM" /MIN "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%HERMES_ROOT%\bin\start-llm.ps1" -Binary "%LLAMA_BIN%" -Model "%MODEL%" -NGpuLayers %NGL% -RootDir "%HERMES_ROOT%"
popd

endlocal
