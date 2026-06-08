@echo off
REM ============================================================
REM Hermes - Smart llama-server Launcher (v3)
REM
REM Features:
REM   - Auto-detects CUDA runtime DLLs (cudart, cublas)
REM   - Auto-calculates --n-gpu-layers intelligently
REM   - Falls back to CPU clearly with warnings
REM   - Outputs JSON status for programmatic use
REM
REM Override:
REM   set LLAMA_NGL=0/99/N to force
REM   set LLAMA_MODEL=path to override model
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "RUNTIME=%HERMES_ROOT%\runtime"
set "LLAMA_BIN=%RUNTIME%\llama-server.exe"
set "GPU_AVAILABLE=0"
set "NGL=0"
set "MODE=CPU"

REM ---- Step 1: Auto-pick the best binary ----
if exist "%RUNTIME%\llama-server-cuda-12.4.exe" set "LLAMA_BIN=%RUNTIME%\llama-server-cuda-12.4.exe"
if exist "%RUNTIME%\llama-server-cuda-11.8.exe" if not exist "%RUNTIME%\llama-server-cuda-12.4.exe" set "LLAMA_BIN=%RUNTIME%\llama-server-cuda-11.8.exe"
if exist "%RUNTIME%\llama-server-vulkan.exe" if "%LLAMA_BIN%"=="%RUNTIME%\llama-server.exe" set "LLAMA_BIN=%RUNTIME%\llama-server-vulkan.exe"

REM ---- Step 2: Check CUDA runtime DLLs (the real gating factor) ----
set "CUDA_RUNTIME_OK=0"
if exist "%RUNTIME%\cudart64_12.dll" if exist "%RUNTIME%\cublas64_12.dll" set "CUDA_RUNTIME_OK=1"
if exist "%RUNTIME%\cudart64_11.dll" if exist "%RUNTIME%\cublas64_11.dll" set "CUDA_RUNTIME_OK=1"

REM ---- Step 3: Determine model ----
set "MODEL=%~1"
if "%MODEL%"=="" set "MODEL=%HERMES_ROOT%\data\models\Qwen2.5-3B-Instruct-Q4_K_M.gguf"
if not "%LLAMA_MODEL%"=="" set "MODEL=%LLAMA_MODEL%"

if not exist "%MODEL%" (
    echo [ERROR] Model not found: %MODEL%
    exit /b 1
)

REM ---- Step 4: Get model size and VRAM ----
for /f "tokens=*" %%S in ('powershell -NoProfile -Command "$f = (Get-Item -LiteralPath '%MODEL%').Length; [int][math]::Floor($f / 1MB)"') do set "MODEL_MB=%%S"

set "VRAM_FREE_MB=0"
where nvidia-smi >nul 2>&1
if not errorlevel 1 (
    for /f "usebackq tokens=*" %%V in (`powershell -NoProfile -Command "$f = (& nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>$null) -split '\n' | Select-Object -First 1; [int]$f"`) do (
        set "VRAM_FREE_MB=%%V"
    )
)

REM ---- Step 5: Smart NGL calculation ----
REM NOTE: every variable read inside these if-blocks uses !VAR! (delayed
REM expansion), not %VAR%, because VRAM_FREE_MB and MODEL_MB are set by
REM a `for /f` block above and the immediate-expansion form reads them
REM as the empty string at this point. Also: we use nested if/else
REM rather than `if A else if B else if C` because cmd.exe's parser
REM chokes on the chained form inside a parenthetical block on some
REM Windows builds (e.g. "Windows 10 22H2" reports `'else' is not
REM recognized as an internal or external command`).
if "%LLAMA_NGL%"=="0" (
    set "NGL=0"
    set "MODE=CPU (forced by LLAMA_NGL=0)"
) else (
    if not "%LLAMA_NGL%"=="" (
        set "NGL=%LLAMA_NGL%"
        set "MODE=GPU (forced %LLAMA_NGL% layers)"
    ) else (
        if !VRAM_FREE_MB! GTR 0 if !CUDA_RUNTIME_OK!==1 (
            REM CUDA runtime exists + NVIDIA GPU detected = real GPU acceleration
            set "GPU_AVAILABLE=1"
            if !MODEL_MB! LEQ !VRAM_FREE_MB! (
                REM Model fits entirely in VRAM
                set "NGL=99"
                set "MODE=GPU (full offload, !MODEL_MB!MB / !VRAM_FREE_MB!MB free VRAM)"
            ) else (
                REM Partial offload: use 70% of free VRAM for model layers
                set /a "VRAM_FOR_MODEL=VRAM_FREE_MB*7/10"
                set /a "AVG_LAYER_MB=MODEL_MB/80"
                if !AVG_LAYER_MB! LSS 1 set "AVG_LAYER_MB=1"
                set /a "NGLCALC=VRAM_FOR_MODEL/AVG_LAYER_MB"
                if !NGLCALC! LSS 1 (
                    set "NGL=0"
                    set "MODE=CPU (model too large: !MODEL_MB!MB needs !VRAM_FREE_MB!MB)"
                ) else (
                    if !NGLCALC! GTR 99 (
                        set "NGL=99"
                        set "MODE=GPU (all !NGLCALC! layers fit)"
                    ) else (
                        set "NGL=!NGLCALC!"
                        set "MODE=Hybrid (!NGL! GPU layers, rest CPU)"
                    )
                )
            )
        ) else (
            if !VRAM_FREE_MB! GTR 0 if !CUDA_RUNTIME_OK!==0 (
                REM NVIDIA GPU detected but CUDA DLLs missing
                set "NGL=0"
                set "MODE=CPU (CUDA runtime DLLs missing - run bin\hermes-firstrun.bat install first)"
            ) else (
                set "NGL=0"
                set "MODE=CPU (no NVIDIA GPU detected)"
            )
        )
    )
)

REM ---- Step 6: Derive model alias ----
for %%F in ("%MODEL%") do set "MODEL_FILENAME=%%~nF"
set "MODEL_ALIAS=!MODEL_FILENAME:.=_!"
set "MODEL_ALIAS=!MODEL_ALIAS:-=!"

echo ============================================================
echo   Hermes - Smart LLM Launcher (v3)
echo.
echo   Binary:       %LLAMA_BIN%
echo   CUDA DLLs:    %CUDA_RUNTIME_OK% (1=OK, 0=MISSING)
echo   Model:        !MODEL_FILENAME! ^(!MODEL_MB! MB^)
echo   Free VRAM:    %VRAM_FREE_MB% MB
echo   NGL:          %NGL%
echo   Mode:         %MODE%
echo ============================================================
echo.

REM ---- Step 7: Warn if CUDA runtime missing ----
if !VRAM_FREE_MB! GTR 0 if !CUDA_RUNTIME_OK!==0 (
    echo [WARNING] ==============================================
    echo   NVIDIA GPU detected but CUDA runtime DLLs are MISSING!
    echo   Your GPU WILL NOT be used. Model runs on CPU only.
    echo.
    echo   To fix, run:
    echo       bin\hermes-firstrun.bat install
    echo.
    echo   This will download ~391MB CUDA runtime from GitHub.
    echo ==========================================================
    echo.
)

REM ---- Step 8: Launch llama-server ----
pushd "%RUNTIME%"
"%HERMES_ROOT%\portable-python\python.exe" -c "import os,sys; m=os.path.abspath(r'''%MODEL%'''); r=os.path.relpath(m, r'''%RUNTIME%'''); print(r)" > "%TEMP%\hermes_model_rel.txt"
set /p "MODEL_REL=" < "%TEMP%\hermes_model_rel.txt"
del "%TEMP%\hermes_model_rel.txt" 2>nul
echo   [launch] model=!MODEL_REL! ngl=%NGL% alias=!MODEL_ALIAS!

start "Hermes-LLM" /MIN "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%HERMES_ROOT%\bin\start-llm.ps1" -Binary "%LLAMA_BIN%" -Model "%MODEL%" -NGpuLayers %NGL% -CtxSize 65536 -Alias "!MODEL_ALIAS!" -RootDir "%HERMES_ROOT%"
popd

REM ---- Output JSON status for programmatic use ----
echo {"model":"!MODEL_FILENAME!","alias":"!MODEL_ALIAS!","ngl":%NGL%,"cuda_dlls":%CUDA_RUNTIME_OK%,"vram_free_mb":%VRAM_FREE_MB%,"mode":"!MODE!"} > "%HERMES_ROOT%\hermes\data\logs\last-launch.json"

endlocal
