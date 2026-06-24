@echo off
REM ============================================================
REM  Install Rust toolchain for Tauri 2 desktop pet
REM ============================================================
echo Installing Rust (this will take ~5 minutes)...
echo.

REM Download and run rustup
curl -sSf -o "%TEMP%\rustup-init.exe" https://win.rustup.rs/x86_64 2>nul
if not exist "%TEMP%\rustup-init.exe" (
    echo ERROR: Could not download rustup
    pause
    exit /b 1
)

REM Install with minimal profile (no docs, just toolchain)
"%TEMP%\rustup-init.exe" -y --default-toolchain stable --profile minimal 2>&1

echo.
echo Rust installed. Setting up PATH...
set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
rustc --version
cargo --version
echo.
echo Done!
pause
