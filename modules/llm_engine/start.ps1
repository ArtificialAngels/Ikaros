# modules/llm_engine/start.ps1 — Launch llama-server in router mode
[CmdletBinding()]
param([int]$Port = 8080)

. $PSScriptRoot\..\..\deps\hermes-env.ps1

$ModelsDir  = Join-Path $HERMES_ROOT 'data\models'
$PresetPath = Join-Path $ModelsDir 'router-preset.ini'
$LogDir     = Join-Path $HERMES_ROOT 'data\logs'
$CudaBase   = Join-Path $HERMES_ROOT 'runtime\cuda'

# ---- Detect recommended CUDA version (multi-version support, Phase 8) ----
$CudaVer = 'cpu'
try {
    $CudaVer = (& $PYTHON -m modules.env_bootstrap.gpu_detect recommend 2>$null).Trim()
    if (-not $CudaVer) { $CudaVer = 'cpu' }
} catch {
    $CudaVer = 'cpu'
}
Write-Host "[llm-engine] recommended CUDA: $CudaVer"

# ---- Auto-pick the best llama-server binary ----
$Bin = $null
if ($CudaVer -ne 'cpu') {
    # Multi-version: runtime/cuda/<ver>/llama-server-cuda-<ver>.exe
    $candidates = @(
        (Join-Path $CudaBase "$CudaVer\llama-server-cuda-$CudaVer.exe"),
        (Join-Path $CudaBase "$CudaVer\llama-server.exe"),
        (Join-Path $LLAMACPP_BIN "llama-server-cuda-$CudaVer.exe")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $Bin = $c; break }
    }
}
if (-not $Bin) {
    foreach ($name in @('llama-server-vulkan.exe', 'llama-server.exe')) {
        $candidate = Join-Path $LLAMACPP_BIN $name
        if (Test-Path $candidate) { $Bin = $candidate; break }
    }
}
if (-not $Bin) {
    Write-Host "[ERROR] No llama-server binary found" -ForegroundColor Red
    exit 1
}

# ---- Inject CUDA bin into PATH (must precede LLAMACPP_BIN for DLL search order) ----
if ($CudaVer -ne 'cpu') {
    $cudaBinDir = Join-Path $CudaBase $CudaVer
    if (Test-Path $cudaBinDir) {
        $env:PATH = "$cudaBinDir;$env:PATH"
        $env:CUDA_VERSION = $CudaVer
        Write-Host "[llm-engine] PATH prepended with: $cudaBinDir"
    }
}

# Persist CUDA version for downstream consumers (bridge, supervisor).
$env:CUDA_VERSION = $CudaVer
$cudaCacheDir = Join-Path $HERMES_ROOT 'data\logs'
if (-not (Test-Path $cudaCacheDir)) { New-Item -ItemType Directory -Path $cudaCacheDir -Force | Out-Null }
'{ "cuda_version": "' + $CudaVer + '" }' | Set-Content -Path (Join-Path $cudaCacheDir 'cuda-active.json') -Encoding UTF8

# ---- Discover GGUF models ----
$ggufFiles = @(Get-ChildItem $ModelsDir -Filter '*.gguf' -ErrorAction SilentlyContinue | Sort-Object Length -Descending)
if ($ggufFiles.Count -eq 0) {
    Write-Host "[ERROR] No .gguf files in $ModelsDir" -ForegroundColor Red
    exit 1
}

# ---- Compute --models-max based on free VRAM ----
$modelsMax = 1
$vramFreeMB = 0
try {
    $smi = (& nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>$null)
    if ($smi) {
        $vramFreeMB = [int]($smi -split "`n" | Select-Object -First 1)
        if ($vramFreeMB -ge 24000) { $modelsMax = 4 }
        elseif ($vramFreeMB -ge 16000) { $modelsMax = 3 }
        elseif ($vramFreeMB -ge 12000) { $modelsMax = 2 }
        else { $modelsMax = 1 }
    }
} catch {}

Write-Host "============================================================"
Write-Host "  Hermes - llm-engine (llama-server router mode)"
Write-Host ""
Write-Host "  Binary:      $Bin"
Write-Host "  Models dir:  $ModelsDir"
Write-Host "  Discovered:  $($ggufFiles.Count) GGUF model(s)"
foreach ($g in $ggufFiles) {
    $szGB = [math]::Round($g.Length / 1GB, 2)
    Write-Host ("    [{0,6} GB] {1}" -f $szGB, $g.Name)
}
Write-Host "  Preset:      $(if (Test-Path $PresetPath) { $PresetPath } else { '(none -- using global defaults)' })"
Write-Host "  Models-max:  $modelsMax (LRU eviction)"
Write-Host "  Free VRAM:   $vramFreeMB MB"
Write-Host "  Endpoint:    http://127.0.0.1`:$Port"
Write-Host "============================================================"
Write-Host ""

# ---- Build launch args ----
# Global defaults act as fallback for child processes spawned by the router.
# Per-model presets in router-preset.ini override these for sections that
# include ctx-size / n-gpu-layers / temp.  Alias-keyed sections (no .gguf
# extension) on b9538+ ignore most preset keys, so the global defaults
# below are what actually reach those child processes.  Without these
# the child process falls back to llama-server defaults (ctx=4096,
# ngl=0/CPU), which trips HTTP 400 on any conversation > 4096 tokens.
$argList = @(
    '--models-dir',   $ModelsDir
    '--models-max',   "$modelsMax"
    '--host',         '127.0.0.1'
    '--port',         "$Port"
    '--jinja'
    '--ctx-size',     '32768'
    '--n-gpu-layers', '16'
    '--temp',         '0.7'
)
if (Test-Path $PresetPath) {
    $argList = @('--models-preset', $PresetPath) + $argList
}

# ---- Truncate log files ----
$logPath = Join-Path $LogDir 'llm-engine.log'
$errPath = Join-Path $LogDir 'llm-engine.err'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
'' | Set-Content -Path $logPath -Encoding UTF8
'' | Set-Content -Path $errPath -Encoding UTF8

# ---- Launch via cmd /c wrapper (stdin bug fix) ----
$innerCmd = '"' + ($Bin -replace '"','\"') + '"'
$innerArgs = ($argList | ForEach-Object {
    if ($_ -match '\s|"') { '"' + ($_ -replace '"','\"') + '"' }
    else { $_ }
}) -join ' '
$fullCmdLine = '/c "' + $innerCmd + ' ' + $innerArgs + '" < NUL'

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName               = 'cmd.exe'
$psi.Arguments              = $fullCmdLine
$psi.WorkingDirectory       = $LLAMACPP_BIN
$psi.UseShellExecute        = $false
$psi.CreateNoWindow         = $true
$psi.WindowStyle            = 'Hidden'
$psi.RedirectStandardInput  = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError  = $true

$proc = [System.Diagnostics.Process]::Start($psi)

# Drain streams
$logWriter = [System.IO.StreamWriter]::new($logPath, $true, [System.Text.UTF8Encoding]::new($false))
$logWriter.AutoFlush = $true
$errWriter = [System.IO.StreamWriter]::new($errPath, $true, [System.Text.UTF8Encoding]::new($false))
$errWriter.AutoFlush = $true
$proc.add_OutputDataReceived({ if ($null -ne $_.Data) { $logWriter.WriteLine($_.Data) } })
$proc.add_ErrorDataReceived({  if ($null -ne $_.Data) { $errWriter.WriteLine($_.Data) } })
$proc.BeginOutputReadLine()
$proc.BeginErrorReadLine()

Start-Sleep -Seconds 1.5

if ($proc.HasExited) {
    $rc = $proc.ExitCode
    $errTail = ''
    if (Test-Path $errPath) {
        $lines = Get-Content $errPath -ErrorAction SilentlyContinue
        if ($lines) { $errTail = ($lines | Select-Object -Last 8) -join "`n" }
    }
    Write-Host "  [FAIL] llama-server exited immediately with code $rc" -ForegroundColor Red
    if ($errTail) {
        foreach ($l in $errTail -split "`n") { Write-Host "    | $l" -ForegroundColor DarkYellow }
    }
    $proc.Dispose(); $logWriter.Dispose(); $errWriter.Dispose()
    exit 1
}

# Recover real PID from netstat
$llamaPid = $null
try {
    $netstat = & netstat -aon -p tcp 2>$null
    foreach ($line in $netstat) {
        if ($line -match "TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)$") {
            $llamaPid = [int]$matches[1]; break
        }
    }
} catch {}
if (-not $llamaPid) { $llamaPid = $proc.Id }

Write-Host "  [pid]   $llamaPid"
Write-Host "  llm-engine started."

# Persist launch info
$launchInfo = @{
    mode        = 'router'
    binary      = $Bin
    models_dir  = $ModelsDir
    models_max  = $modelsMax
    preset      = if (Test-Path $PresetPath) { $PresetPath } else { $null }
    vram_free_mb= $vramFreeMB
    port        = $Port
    pid         = $llamaPid
    cuda_version= $CudaVer
    discovered  = @($ggufFiles | ForEach-Object { $_.Name })
} | ConvertTo-Json -Compress
$launchInfo | Set-Content -Path (Join-Path $LogDir 'llm-engine-last-launch.json') -Encoding UTF8

$proc.Dispose(); $logWriter.Dispose(); $errWriter.Dispose()
exit 0
