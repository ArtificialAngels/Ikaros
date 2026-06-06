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
    [string]$Alias       = 'qwen3.5-35b-a3b',
    [string]$ListenHost  = '127.0.0.1',  # NOTE: not -Host, that's a read-only PS var
    [int]   $Port        = 8080,
    [int]   $CtxSize     = 4096,
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
    '--log-disable'
)

$proc = Start-Process `
    -FilePath $BinFull `
    -ArgumentList $argList `
    -WorkingDirectory $RuntimeDir `
    -RedirectStandardOutput (Join-Path $LogDirFull 'llm-server.log') `
    -RedirectStandardError  (Join-Path $LogDirFull 'llm-server.err') `
    -WindowStyle Hidden `
    -PassThru

Write-Host "  [pid]   $($proc.Id)"
Write-Host ""
Write-Host "  llama-server started in background. To stop:"
Write-Host "    Stop-Process -Id $($proc.Id)"
