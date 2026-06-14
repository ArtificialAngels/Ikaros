# deps/hermes-env.ps1 -- Hermes unified env setup (PowerShell entry point)
#
# Dot-source this file from any .ps1 module:
#     . "$PSScriptRoot\hermes-env.ps1"
#
# After dot-sourcing, all 14 HERMES_* env vars are set in the current
# process, and PowerShell-scope aliases ($HERMES_ROOT etc.) are
# available for terse reference in the same scope.
#
# Why this file is so small: the heavy lifting (drive-letter resolution,
# folder-name detection, drive scan) lives in bin\hermes-root.py.
# This file just consumes the env block, auto-heals stale junctions,
# and layers on cuda/PATH tweaks.
#
# CHANGELOG (2026-06-13):
#   - Replaced `Join-Path $HERMES_DEPS 'node' / 'tools' / 'llamacpp\bin'`
#     with $HERMES_RUNTIME and Join-Path $HERMES_RUNTIME 'node23'. The old
#     deps\* paths were directory junctions whose absolute targets broke
#     when the project was moved to a new drive letter (E: -> F:).
#   - Added an auto-heal step that rmdir's any leftover junction under
#     deps\ (rmdir /Q on a reparse point does NOT recurse into the
#     target — the real content in runtime\, node23\, portable-python\
#     is untouched).

# ---- Step 1: resolve via single-source-of-truth (bat subprocess) ----
$envBlock = & "$PSScriptRoot\..\bin\hermes-root.bat" init
foreach ($line in $envBlock -split "`r?`n") {
    if ($line -match '^([^=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}

# ---- Step 2: promote to script-scope variables for terse reference ----
$HERMES_ROOT         = $env:HERMES_ROOT
$HERMES_BIN          = $env:HERMES_BIN
$HERMES_DATA         = $env:HERMES_DATA
$HERMES_HOME         = $env:HERMES_HOME
$HERMES_MODELS       = $env:HERMES_MODELS
$HERMES_PYTHON       = $env:HERMES_PYTHON
$HERMES_DEPS         = $env:HERMES_DEPS
$HERMES_RUNTIME      = $env:HERMES_RUNTIME
$HERMES_CONFIG       = $env:HERMES_CONFIG
$HERMES_MODULES      = $env:HERMES_MODULES
$HERMES_LOGS         = $env:HERMES_LOGS
$HERMES_DATA_DIR     = $env:HERMES_DATA_DIR

# ---- Step 2b: short aliases used by modules\*/start.ps1 ----
# Many module scripts (bridge, webui, llm_engine, model_manager) refer to
# bare `$PYTHON` / `$NODE` / `$LLAMACPP_BIN` rather than the full `HERMES_*`
# names. We expose both forms so either style works.
#
# IMPORTANT (2026-06-13): the previous versions of these aliases pointed
# at Join-Path $HERMES_DEPS 'node' / 'tools' / 'llamacpp\bin', which
# were directory junctions whose targets were absolute paths. When the
# project is moved to a new drive letter (E: -> F:), the junctions
# still point at E:\... and break. We now resolve to $HERMES_RUNTIME
# and $HERMES_RUNTIME\node23 directly (the real on-disk locations).
$PYTHON       = $HERMES_PYTHON
$LLAMACPP_BIN = $HERMES_RUNTIME
$NODE_BIN_DIR = Join-Path $HERMES_RUNTIME 'node23'
$NODE         = Join-Path $NODE_BIN_DIR 'node.exe'

# ---- Step 2c: Auto-heal any stale junction under deps\ ----
# Historical migration: earlier Hermes exposed runtime/, node23/, and
# portable-python/ under deps\ as directory junctions (mklink /J).
# Junctions store the target path as an absolute NTFS reparse-point
# blob, so when the project is moved to a new drive letter the
# junctions still point at the old drive and break any consumer that
# touches Join-Path $HERMES_DEPS 'node' etc. The 2026-06-13 refactor
# removed all dependency on those junctions. As a safety net, this
# block auto-removes any stale junction that might still be sitting
# under deps\ ([System.IO.DirectoryInfo].Attributes detects the
# reparse point; cmd rmdir /Q does NOT recurse into the target — safe).
$staleJunctions = @('node', 'tools', 'llamacpp\bin', 'python-test')
foreach ($name in $staleJunctions) {
    $p = Join-Path $HERMES_DEPS $name
    if (-not (Test-Path -LiteralPath $p)) { continue }
    $attrs = (Get-Item -LiteralPath $p -Force).Attributes
    if ($attrs -match 'ReparsePoint') {
        & cmd.exe /c "rmdir /Q `"$p`"" | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[hermes-env] removed stale junction: $p" -ForegroundColor DarkYellow
        }
    }
}

# ---- Step 3: read cuda-active.json (so bridge/supervisor pick the right runtime) ----
$CUDA_VERSION = ''
$LLAMACPP_BIN_CUDA = Join-Path $HERMES_RUNTIME 'cuda\12.4'
$cudaActiveJson = Join-Path $HERMES_LOGS 'cuda-active.json'
if (Test-Path $cudaActiveJson) {
    try {
        $cudaObj = Get-Content -Raw -Path $cudaActiveJson | ConvertFrom-Json -ErrorAction Stop
        if ($cudaObj -and $cudaObj.cuda_version) {
            $CUDA_VERSION = [string]$cudaObj.cuda_version
            $LLAMACPP_BIN_CUDA = Join-Path $HERMES_RUNTIME ("cuda\" + $CUDA_VERSION)
        }
    } catch {}
}

# ---- Step 4: Python env ----
[Environment]::SetEnvironmentVariable('PYTHONIOENCODING', 'utf-8', 'Process')
[Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'Process')
[Environment]::SetEnvironmentVariable('PYTHONPATH', "$HERMES_ROOT;$HERMES_ROOT\hermes-agent", 'Process')

# ---- Step 5: WebUI critical env vars ----
[Environment]::SetEnvironmentVariable('HERMES_WEB_UI_DISABLE_GATEWAY_AUTOSTART', '1', 'Process')
[Environment]::SetEnvironmentVariable('CORS_ORIGINS', '*', 'Process')
[Environment]::SetEnvironmentVariable('HERMES_AGENT_BRIDGE_PYTHON', $HERMES_PYTHON, 'Process')

# ---- Step 5b: Pin HERMES_AGENT_CLI_PYTHON for hermes-web-ui's hermes-cli.ts ----
# hermes-web-ui's bundledCliPythonForWindows() short-circuits on this env
# var. Without it, it falls back to basename(HERMES_BIN) === 'hermes.exe',
# which fails whenever HERMES_BIN is a directory (e.g. a stale user-level
# setx HERMES_BIN=E:\Hermes Agent\bin from the old supervisor.bat era).
# Pinning the portable-python here means webui can never spawn a
# directory by accident, regardless of what HERMES_BIN happens to be.
[Environment]::SetEnvironmentVariable('HERMES_AGENT_CLI_PYTHON', $HERMES_PYTHON, 'Process')

# ---- Step 6: PATH augmentation ----
# Prepend the active CUDA bin first (so its cudart/cublas DLLs win the
# DLL search order) followed by the other deps.
# IMPORTANT (2026-06-13): we reference $HERMES_RUNTIME and
# $HERMES_RUNTIME\node23 directly instead of $HERMES_DEPS\llamacpp\bin /
# $HERMES_DEPS\tools / $HERMES_DEPS\node. The deps\* paths were
# junctions whose absolute targets broke across drive letters (see
# Step 2c for the auto-heal).
# NOTE: portable-python\Scripts is critical -- hermes-web-ui spawns
# `hermes gateway run --replace`, which lives there. Without it, the
# webui crashes immediately with "Error: spawn hermes ENOENT".
$pythonScripts = Join-Path $HERMES_ROOT 'portable-python\Scripts'
$currentPath   = [Environment]::GetEnvironmentVariable('PATH', 'Process')
$newPath = "$LLAMACPP_BIN_CUDA;$HERMES_RUNTIME;$NODE_BIN_DIR;$pythonScripts;$currentPath"
[Environment]::SetEnvironmentVariable('PATH', $newPath, 'Process')
