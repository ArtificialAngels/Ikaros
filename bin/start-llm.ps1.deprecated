# Hermes - LLM launcher (PowerShell)
# Detaches llama-server as an independent process via .NET Process.Start,
# so it survives the parent bat exiting. Mirrors the model ComfyUI-aki uses
# in its WPF launcher (matsu.exe).
#
# Usage:
#     powershell -NoProfile -ExecutionPolicy Bypass -File start-llm.ps1 `
#         -Binary 'runtime\llama-server-cuda-12.4.exe' `
#         -Model 'data\models\Qwen2.5-7B-Instruct-Q4_K_M.gguf' `
#         -NGpuLayers 99 `
#         -Alias 'qwen2.5-7b-instruct' `
#         -ListenHost '127.0.0.1' -Port 8080 `
#         -CtxSize 4096 -Threads 4 `
#         -LogDir 'data\logs'
#
# All parameters default to the Qwen2.5-7B config (matches hermes-all.bat).

[CmdletBinding()]
param(
    [string]$Binary      = 'runtime\llama-server-cuda-12.4.exe',
    [string]$Model       = 'data\models\Qwen2.5-7B-Instruct-Q4_K_M.gguf',
    [int]   $NGpuLayers  = 99,
    [string]$Alias       = '',
    [string]$ListenHost  = '127.0.0.1',  # NOTE: not -Host, that's a read-only PS var
    [int]   $Port        = 8080,
    [int]   $CtxSize     = 65536,
    [int]   $Threads     = 4,
    [string]$LogDir      = 'data\logs',
    [string]$RootDir     = ''  # auto-detected from script location
)

if (-not $RootDir) {
    $RootDir = Split-Path -Parent $PSCommandPath
    $RootDir = Split-Path -Parent $RootDir   # bin\.. = project root
}

# Accept either absolute or relative paths for -Binary / -Model
function Resolve-HermesPath {
    param([string]$Path, [string]$Root)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return (Join-Path $Root $Path)
}

$BinFull    = Resolve-HermesPath $Binary  $RootDir
$ModelFull  = Resolve-HermesPath $Model   $RootDir
$LogDirFull = Resolve-HermesPath $LogDir  $RootDir

# Auto-derive alias from model filename if not provided
if (-not $Alias) {
    $modelFileName = [System.IO.Path]::GetFileNameWithoutExtension($ModelFull)
    # Clean: replace dots/hyphens with underscores for a clean alias
    $Alias = $modelFileName -replace '[.\s-]+', '_'
}
if (-not (Test-Path $LogDirFull)) {
    New-Item -ItemType Directory -Path $LogDirFull -Force | Out-Null
}

Write-Host "============================================================"
Write-Host "  Hermes - LLM Launcher (PowerShell)"
Write-Host ""
Write-Host "  Binary:  $BinFull"
Write-Host "  Model:   $ModelFull"
Write-Host "  NGL:     $NGpuLayers"
Write-Host "  Endpoint: http://$ListenHost`:$Port"
Write-Host "  Logs:    $LogDirFull\llm-server.{log,err}"
Write-Host "============================================================"
Write-Host ""

# Compute a relative model path from the runtime dir (avoids the
# "E:\Hermes Agent\..." space-split bug in llama-server's argv parser).
# PowerShell 5.1 (Windows PS) doesn't have [System.IO.Path]::GetRelativePath,
# so we compute it manually with Uri relative paths.
$RuntimeDir = Join-Path $RootDir 'runtime'
$runtimeUri = New-Object System.Uri(($RuntimeDir + '\'))
$modelUri   = New-Object System.Uri($ModelFull)
$ModelRel   = [System.Uri]::UnescapeDataString(
    $runtimeUri.MakeRelativeUri($modelUri).ToString()
).Replace('/', '\')
Write-Host "  [start] cd $RuntimeDir  model=$ModelRel"

# Build arg list (no quoting issues with arrays)
$argList = @(
    '--model',          $ModelRel
    '--alias',          $Alias
    '--host',           $ListenHost
    '--port',           $Port
    '--ctx-size',       $CtxSize
    '--n-gpu-layers',   $NGpuLayers
    '--threads',        $Threads
    '--jinja'
)

# Detach via Start-Process. PowerShell's Start-Process uses ShellExecuteEx
# by default (UseShellExecute=$true), so the child process is NOT attached
# to the calling PowerShell session's job object — it survives the parent
# bat / ps1 exiting. That makes the chain hermes-all.bat → cmd /c →
# powershell -File start-llm.ps1 → Start-Process → llama-server stable
# in practice (validated 2026-06-08: server stays up across PS exits).
#
# Use .NET Process.Start with UseShellExecute=$false + CreateNoWindow=$true
# instead of PowerShell's Start-Process. Start-Process internally calls
# ShellExecuteEx which, even with -WindowStyle Hidden, still attaches the
# new process to the calling PowerShell's console. When the user closes
# the parent cmd window, Windows broadcasts CTRL_CLOSE_EVENT to every
# process attached to that console — and llama-server, being a console
# application, responds by exiting. Net effect: the "detached" process
# wasn't actually detached, and closing the launcher cmd kills the LLM.
#
# .NET Process.Start with these flags goes through CreateProcess directly
# with CREATE_NO_WINDOW. The new process is created without a console of
# its own, and crucially is NOT attached to the parent's console — so
# CTRL_CLOSE_EVENT from the parent cmd's close never reaches it.
#
# The output/error redirects to FILE (not pipe), so the .NET handle's
# lifetime is decoupled from the OS process: even if we Dispose() the
# handle, the child keeps writing to the file via its own handles.
$logPath = Join-Path $LogDirFull 'llm-server.log'
$errPath = Join-Path $LogDirFull 'llm-server.err'

# Truncate logs from any previous run so a fresh server boot is unambiguous.
# We do this in the script (not via ProcessStartInfo.RedirectStandardOutput
# which only opens in Write/Append at the time the child starts) so the
# "log is empty" state is visible to anyone tail'ing before the child
# actually opens the file.
'' | Set-Content -Path $logPath -Encoding UTF8
'' | Set-Content -Path $errPath -Encoding UTF8

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $BinFull
# ArgumentList is a string[]. Joining with single spaces is safe because
# individual args containing spaces are already quoted in the array entries
# (see $argList construction at the top of this script).
$psi.Arguments = ($argList -join ' ')
$psi.WorkingDirectory = $RuntimeDir
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.WindowStyle = 'Hidden'
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true

$proc = [System.Diagnostics.Process]::Start($psi)

# Wire the redirected stdout/stderr to the on-disk log files. BeginXxxReadLine
# is async — it drains the pipes into the file on a background thread, so
# the powershell doesn't block on full-pipe backpressure. The pipes die with
# the child process, not with this script, so we don't need to manage them
# after the script exits.
$logWriter = [System.IO.StreamWriter]::new($logPath, $true, [System.Text.UTF8Encoding]::new($false))
$logWriter.AutoFlush = $true
$errWriter = [System.IO.StreamWriter]::new($errPath, $true, [System.Text.UTF8Encoding]::new($false))
$errWriter.AutoFlush = $true
$proc.add_OutputDataReceived({ if ($null -ne $_.Data) { $logWriter.WriteLine($_.Data) } })
$proc.add_ErrorDataReceived({  if ($null -ne $_.Data) { $errWriter.WriteLine($_.Data) } })
$proc.BeginOutputReadLine()
$proc.BeginErrorReadLine()

# NOTE: avoid the name `$pid` - that is a read-only automatic variable in
# PowerShell (the current process's own PID). Assigning to it throws
# `VariableNotWritable` and aborts the script after the child has already
# been spawned, leaving an orphan llama-server.
$llamaPid = $proc.Id
Write-Host "  [pid]   $llamaPid"
Write-Host ""
Write-Host "  llama-server started detached. To stop:"
Write-Host "    Stop-Process -Id $llamaPid"
Write-Host "  (closing the launcher cmd window will NOT kill the server.)"

# Dispose our .NET handle and exit. The OS process keeps running because
# it has its own console (none) and was created with CREATE_NO_WINDOW +
# UseShellExecute=$false. The script ending does NOT propagate to the
# child — the child is fully detached.
$proc.Dispose()
$logWriter.Dispose()
$errWriter.Dispose()
exit 0
}
