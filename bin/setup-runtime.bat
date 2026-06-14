@echo off
REM ============================================================
REM Hermes - Setup Runtime (download ALL llama.cpp variants)
REM
REM Downloads every GPU variant + multi-threaded downloader,
REM so the USB stick works on ANY GPU out of the box.
REM
REM Variants:
REM   - CPU           (~10MB)   Already shipped (you have it)
REM   - CUDA 12.4     (~250MB)  NVIDIA RTX 20/30/40/50 + GTX 10+ (driver >= 525)
REM   - CUDA 11.8     (~250MB)  Older NVIDIA: GTX 900/P100/older drivers
REM   - Vulkan        (~32MB)   AMD / Intel / NVIDIA fallback (driver-agnostic)
REM
REM Uses aria2 (16 connections) for parallel download.
REM Falls back to curl on slow / broken links.
REM
REM Re-runnable: resumes partial downloads.
REM Updated 2026-06-06: bump to b9538 (Qwen3 MoE / Qwen3.5 MoE support).
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "HERMES_ROOT=%~dp0.."
set "RUNTIME=%HERMES_ROOT%\runtime"
set "VER=b9538"
set "CONNECTIONS=16"

echo ============================================================
echo   Hermes - Runtime Setup (portable across all GPUs)
echo ============================================================
echo.

REM === Step 1: aria2c (multi-threaded downloader) ===
set "ARIA2C=%RUNTIME%\aria2c.exe"
set "ARIA2_URL=https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip"
set "ARIA2_ZIP=%RUNTIME%\aria2.zip"

if exist "!ARIA2C!" (
    echo [1/5] aria2c already present
) else (
    echo [1/5] Downloading aria2c - approx 5MB
    if not exist "!ARIA2_ZIP!" (
        curl -L -C - -o "!ARIA2_ZIP!" "!ARIA2_URL!" --connect-timeout 30 --max-time 600
    )
    if not exist "!ARIA2_ZIP!" (
        echo   [FAIL] aria2 download failed
        echo   Tip: install aria2 from https://aria2.github.io/
        pause
        exit /b 1
    )
    if not exist "!RUNTIME!\aria2_extracted" (
        powershell -NoProfile -Command "try { Expand-Archive -Path '!ARIA2_ZIP!' -DestinationPath '!RUNTIME!\aria2_extracted' -Force; 'OK' } catch { 'FAIL: ' + $_.Exception.Message }" >nul
    )
    if exist "!RUNTIME!\aria2_extracted\aria2-1.37.0-win-64bit-build1\aria2c.exe" (
        move /Y "!RUNTIME!\aria2_extracted\aria2-1.37.0-win-64bit-build1\aria2c.exe" "!ARIA2C!" >nul
        copy /Y "!RUNTIME!\aria2_extracted\aria2-1.37.0-win-64bit-build1\*.dll" "!RUNTIME!\" >nul 2>&1
        rmdir /S /Q "!RUNTIME!\aria2_extracted"
        del "!ARIA2_ZIP!" 2>nul
        echo   [OK] Installed aria2c
    ) else (
        echo   [FAIL] Could not find aria2c.exe in extracted archive
        pause
        exit /b 1
    )
)

REM === Step 2-4: Download each variant ===
set "BASE_URL=https://github.com/ggerganov/llama.cpp/releases/download/!VER!"
REM Order matters: hermes-all.bat picks the FIRST one that exists.
REM CUDA 12.4 first (modern NVIDIA), then 11.8 (older), then Vulkan (AMD/Intel).
set "TARGETS=cuda-12.4 cuda-11.8 vulkan"

set "DONE_COUNT=0"
set "TOTAL_COUNT=0"
for %%V in (!TARGETS!) do set /a "TOTAL_COUNT+=1"

for %%V in (!TARGETS!) do (
    set "ZIP_NAME=llama-!VER!-bin-win-%%V-x64.zip"
    set "ZIP_PATH=!RUNTIME!\llama-%%V.zip"
    set "EXTRACT_DIR=!RUNTIME!\extract-%%V"
    set "BIN_NAME=llama-server-%%V.exe"
    set "URL=!BASE_URL!/!ZIP_NAME!"

    echo.
    echo ============================================================
    echo   Variant: %%V
    echo   URL: !URL!
    echo ============================================================

    REM === Check 1: binary already installed? ===
    if exist "!RUNTIME!\!BIN_NAME!" (
        for %%I in ("!RUNTIME!\!BIN_NAME!") do set "BIN_SIZE=%%~zI"
        echo   [SKIP] !BIN_NAME! already installed (!BIN_SIZE! bytes)
        set /a "DONE_COUNT+=1"
        goto :next_variant
    )

    REM === Migrate legacy llama-cuda.zip to the current naming scheme ===
    REM      Some pre-2026-06-09 setups left an `llama-cuda.zip` with no
    REM      version suffix; rename so the resume path below can find it.
    if exist "!RUNTIME!\llama-cuda.zip" (
        if "!ZIP_NAME!"=="llama-!VER!-bin-win-cuda-12.4-x64.zip" (
            echo   [MIGRATE] found legacy llama-cuda.zip, renaming to new name for resume
            move /Y "!RUNTIME!\llama-cuda.zip" "!ZIP_PATH!" >nul
        )
    )

    REM === Check 2: zip exists? ===
    REM Let aria2 decide if it's complete or partial - it does the right thing automatically.
    REM If file is fully downloaded, aria2 verifies with Content-Length from server.
    REM If partial, it resumes from byte offset.
    if exist "!ZIP_PATH!" (
        for %%I in ("!ZIP_PATH!") do set "ZIP_SIZE=%%~zI"
        if !ZIP_SIZE! GTR 1000000 (
            echo   [FOUND] !ZIP_PATH! (!ZIP_SIZE! bytes) - aria2 will verify + resume if needed
        ) else (
            echo   [RE-DOWNLOAD] !ZIP_PATH! too small, deleting and re-downloading
            del "!ZIP_PATH!" 2>nul
        )
    )

    REM === Download (aria2 -c handles complete/partial/no-zip transparently) ===
    echo   Downloading with !CONNECTIONS! parallel connections...
    "!ARIA2C!" -x !CONNECTIONS! -s !CONNECTIONS! -c --dir="!RUNTIME!" --out="llama-%%V.zip" "!URL!" 2>nul
    if !ERRORLEVEL! NEQ 0 (
        echo   [WARN] aria2 failed, falling back to curl with resume...
        curl -L -C - -o "!ZIP_PATH!" "!URL!" --connect-timeout 30 --max-time 7200
    )

    REM === Verify ===
    if not exist "!ZIP_PATH!" (
        echo   [FAIL] download failed
        goto :next_variant
    )
    for %%I in ("!ZIP_PATH!") do set "SIZE=%%~zI"
    echo   Downloaded: !SIZE! bytes

    if !SIZE! LSS 1000000 (
        echo   [FAIL] file too small, skipping
        goto :next_variant
    )

    REM === Extract ===
    echo   Extracting...
    if exist "!EXTRACT_DIR!" rmdir /S /Q "!EXTRACT_DIR!"
    powershell -NoProfile -Command "try { Expand-Archive -Path '!ZIP_PATH!' -DestinationPath '!EXTRACT_DIR!' -Force; 'OK' } catch { 'FAIL' }" >nul

    if not exist "!EXTRACT_DIR!" (
        echo   [FAIL] extraction failed (zip may be corrupt - try deleting it)
        goto :next_variant
    )

    REM Find llama-server.exe in extracted dir and rename
    for /R "!EXTRACT_DIR!" %%F in (llama-server.exe) do (
        if not exist "!RUNTIME!\!BIN_NAME!" (
            move /Y "%%F" "!RUNTIME!\!BIN_NAME!" >nul
            echo   [OK] Installed !BIN_NAME!
        )
    )

    REM Move DLLs (cudart, vulkan-1, etc.)
    for /R "!EXTRACT_DIR!" %%F in (*.dll) do (
        copy /Y "%%F" "!RUNTIME!\" >nul 2>&1
    )

    REM Cleanup extracted dir + zip
    rmdir /S /Q "!EXTRACT_DIR!" 2>nul
    REM Keep the zip for resume if user wants to reinstall later, but it's not needed at runtime
    REM Actually no - we don't need it. Delete to save space.
    del "!ZIP_PATH!" 2>nul

    if exist "!RUNTIME!\!BIN_NAME!" (
        set /a "DONE_COUNT+=1"
    ) else (
        echo   [FAIL] !BIN_NAME! not found after extraction
    )

    :next_variant
)

echo.
echo ============================================================
echo   Runtime setup complete: !DONE_COUNT!/!TOTAL_COUNT! variants installed
echo ============================================================
echo.
echo Installed binaries:
for %%F in ("!RUNTIME!\llama-server*.exe") do (
    set "SZ=%%~zF"
    echo   - %%~nxF  (!SZ! bytes)
)
echo.
echo heremes-all.bat auto-picks: CUDA ^> Vulkan ^> CPU
echo ============================================================
echo.
REM pause  REM disabled for headless / non-interactive runs (cron, scripts)
endlocal
