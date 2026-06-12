@echo off
REM ============================================================
REM bin/install-git-hooks.bat
REM
REM Point git at our versioned hooks in .githooks\ instead of the
REM default .git\hooks\ (which is per-clone, not in version control).
REM
REM Usage:
REM     bin\install-git-hooks.bat            (install)
REM     bin\install-git-hooks.bat uninstall  (revert)
REM
REM After install, every `git commit` will run .githooks\pre-commit
REM which calls bin\fix-eol.py --all --check to block commits that
REM introduce LF-only .bat / .ps1 files. See AGENTS.md SS7 for why.
REM ============================================================
setlocal

set "HERE=%~dp0"
set "ROOT=%HERE%.."

REM ---- Sanity: must be inside a git working tree ----
git -C "%ROOT%" rev-parse --git-dir >nul 2>&1
if errorlevel 1 (
    echo [ERROR] %ROOT% is not a git working tree.
    echo         This script must run inside the Hermes Agent repo.
    exit /b 1
)

if /i "%~1"=="uninstall" goto :uninstall

REM ---- Install: point git at .githooks\ ----
echo [install-git-hooks] Setting core.hooksPath to .githooks
git -C "%ROOT%" config core.hooksPath .githooks
if errorlevel 1 (
    echo [ERROR] git config core.hooksPath failed.
    exit /b 1
)

REM ---- Mark pre-commit executable on unix-like (best-effort) ----
if exist "%ROOT%\.githooks\pre-commit" (
    attrib -R "%ROOT%\.githooks\pre-commit" >nul 2>&1
)

echo.
echo Done. core.hooksPath is now:
git -C "%ROOT%" config --get core.hooksPath
echo.
echo Every `git commit` will now run .githooks\pre-commit which verifies
echo that all Hermes-owned .bat / .ps1 files are CRLF (cmd.exe requirement).
echo.
echo To uninstall:  bin\install-git-hooks.bat uninstall
endlocal
exit /b 0

:uninstall
echo [install-git-hooks] Removing core.hooksPath override
git -C "%ROOT%" config --unset core.hooksPath
if errorlevel 1 (
    if not errorlevel 5 (
        echo [ERROR] git config --unset failed.
        exit /b 1
    )
)
echo.
echo Done. git will now use default hooks at .git\hooks\
endlocal
exit /b 0