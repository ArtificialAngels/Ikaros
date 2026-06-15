@echo off
REM ============================================================
REM deps\hermes-env.bat -- Hermes unified env setup (bat entry point).
REM
REM Call this first from any .bat / .ps1 in the project:
REM   call "%~dp0hermes-env.bat"      (from bat)
REM   . "$PSScriptRoot\hermes-env.ps1" (from ps1)
REM
REM Root resolution lives in bin\hermes-root.py. This file:
REM   1. Pulls the 14-var env block from `bin\hermes-root.bat init`
REM   2. Reads cuda-active.json to pick the active CUDA bin
REM   3. Auto-heals any leftover deps\* directory junctions (see
REM      AGENTS.md §3, 2026-06-13 junction audit)
REM   4. Sets Python env vars (PYTHONIOENCODING, PYTHONPATH, ...)
REM   5. Pins HERMES_AGENT_CLI_PYTHON (prevents webui's stale
REM      HERMES_BIN-dir spawn bug; see AGENTS.md §0.4 gotcha)
REM   6. Augments PATH with active CUDA bin, runtime/, node23/,
REM      and portable-python\Scripts
REM ============================================================
REM NOTE: This file intentionally does NOT use `setlocal`. All variable
REM assignments below are exported directly to the caller. Earlier versions
REM used `setlocal enabledelayedexpansion` + `endlocal & set "X=!X!"`, but
REM that pattern is broken: cmd evaluates the !X! AFTER endlocal pops the
REM local scope, so the value is dropped. The cleanest fix is to avoid
REM setlocal entirely and use a direct `for /f "usebackq ..."` on the file
REM (no CUDA_JSON intermediate variable, no delayed expansion needed).

REM ---- Step 1: resolve HERMES_ROOT + derived paths (single source of truth) ----
REM      For-loop variable naming: tokens=1,2 means cmd assigns the FIRST token
REM      to the explicitly-named variable (%%K) and the SECOND token to the
REM      NEXT letter in alphabetical order (%%L). NOT %%V. An earlier version
REM      used `%%V`, which cmd treats as a literal %V string, so every var was
REM      set to the text "%V" instead of the actual path. This broke every
REM      downstream bat file (notably bin\hermes-supervisor.bat, which then
REM      reports `[FATAL] portable-python not found: %V`).
for /f "usebackq tokens=1,2 delims==" %%K in (`call "%~dp0..\bin\hermes-root.bat" init`) do (
    if not "%%L"=="" set "%%K=%%L"
)

REM ---- Step 2: read cuda-active.json (parse the version out) ----
REM KNOWN LIMITATION: cmd's for /f token-splitting of JSON is fragile when
REM the file has a UTF-8 BOM (cuda-active.json is written by
REM llm_engine/start.ps1 with Set-Content -Encoding UTF8 which emits a BOM).
REM We sanity-check the parsed value: if it doesn't look like "X.Y", we keep
REM the default "runtime\cuda\12.4" and skip the override. ps1 callers
REM (deps\hermes-env.ps1) handle the BOM correctly via ConvertFrom-Json.
set "CUDA_VERSION="
set "LLAMACPP_BIN_CUDA=%HERMES_ROOT%\runtime\cuda\12.4"
if exist "%HERMES_ROOT%\data\logs\cuda-active.json" (
    for /f "usebackq tokens=2 delims=:" %%K in ("%HERMES_ROOT%\data\logs\cuda-active.json") do (
        if not "%%K"=="" set "CUDA_VERSION=%%~K"
    )
    REM Sanity check: must look like "X.Y" (digits + dot). If not, leave
    REM LLAMACPP_BIN_CUDA at its default.
    echo "%CUDA_VERSION%" | findstr /R /C:"[0-9][0-9]*\.[0-9]" >nul 2>&1 && (
        if not "%CUDA_VERSION%"=="" set "LLAMACPP_BIN_CUDA=%HERMES_ROOT%\runtime\cuda\%CUDA_VERSION%"
    )
)

REM ---- Step 3: Auto-heal any stale junction under deps\ ----
REM Historical migration: earlier Hermes exposed runtime/, node23/, and
REM portable-python/ under deps\ as directory junctions (mklink /J). Those
REM junctions store absolute NTFS reparse-point targets, so when the project
REM is moved to a new drive letter (E: -> F:) the junctions still point at
REM E:\... and break any consumer that touches %HERMES_DEPS%\node etc. The
REM 2026-06-13 refactor removed all dependency on those junctions (this file
REM now resolves the real paths via %HERMES_RUNTIME% directly). As a safety
REM net, this block auto-rmdir's any stale junction that might still be
REM sitting under deps\ (rmdir /Q on a reparse point does NOT recurse into
REM the target — safe; the real content lives in runtime\, node23\,
REM portable-python\ and is untouched).
for %%J in (node tools "llamacpp\bin" "python-test") do (
    if exist "%HERMES_DEPS%\%%~J" (
        fsutil reparsepoint query "%HERMES_DEPS%\%%~J" >nul 2>&1
        if not errorlevel 1 (
            rmdir /Q "%HERMES_DEPS%\%%~J" >nul 2>&1
            if not errorlevel 1 (
                echo [hermes-env] removed stale junction: deps\%%~J
            )
        )
    )
)

REM ---- Step 4: Python env ----
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHONPATH=%HERMES_ROOT%;%HERMES_ROOT%\hermes-agent"

REM ---- Step 5: WebUI critical env vars ----
set "HERMES_WEB_UI_DISABLE_GATEWAY_AUTOSTART=1"
set "CORS_ORIGINS=*"
set "HERMES_AGENT_BRIDGE_PYTHON=%HERMES_PYTHON%"

REM ---- Step 5b: Pin HERMES_AGENT_CLI_PYTHON for hermes-web-ui's hermes-cli.ts ----
REM hermes-web-ui's bundledCliPythonForWindows() short-circuits on this env
REM var. Without it, it falls back to basename(HERMES_BIN) === 'hermes.exe',
REM which fails whenever HERMES_BIN is a directory (e.g. a stale user-level
REM setx HERMES_BIN=E:\Hermes Agent\bin from the old supervisor.bat era).
REM Pinning the portable-python here means webui can never spawn a
REM directory by accident, regardless of what HERMES_BIN happens to be.
set "HERMES_AGENT_CLI_PYTHON=%HERMES_PYTHON%"

REM ---- Step 5c: HERMES_GIT_BASH_PATH for hermes-agent's terminal backend ----
REM hermes-agent's tools/environments/local.py::_find_bash() needs bash.exe
REM for POSIX shell command execution. It checks HERMES_GIT_BASH_PATH first,
REM then %LOCALAPPDATA%\hermes\git\, then system Git. Our bundled MinGit
REM lives at runtime\git\ (installed by setup-portable.bat step 3b).
set "HERMES_GIT_BASH_PATH=%HERMES_ROOT%\runtime\git\bin\bash.exe"

REM ---- Step 6: PATH augmentation ----
REM Prepend runtime deps so they take precedence. CUDA bin goes first
REM if active so its DLLs win the DLL search order. We reference
REM %HERMES_RUNTIME% and %HERMES_RUNTIME%\node23 directly instead of
REM %HERMES_DEPS%\node / %HERMES_DEPS%\tools / %HERMES_DEPS%\llamacpp\bin
REM (which were junctions pointing at the original drive — see Step 3).
REM portable-python\Scripts is critical for hermes-web-ui (it spawns
REM `hermes gateway run --replace`).
set "PATH=%LLAMACPP_BIN_CUDA%;%HERMES_RUNTIME%;%HERMES_RUNTIME%\node23;%HERMES_ROOT%\portable-python\Scripts;%PATH%"

exit /b 0
