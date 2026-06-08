<#
.SYNOPSIS
Hermes - llama-server Router Mode launcher.

.DESCRIPTION
Launches a SINGLE llama-server process in router mode (b9538+) that
auto-discovers all GGUF files in data\models\. Models are loaded
on-demand when an API request arrives with `model="<filename>"`. With
--models-max 1, only the most-recently-used model is resident in
VRAM at a time; older models are evicted automatically via LRU.

Switching models is now a no-op: just send a chat request with a
different `model` field. No more kill+restart cycle, no more bat
file gymnastics.

Per-model NGL / ctx-size / sampling defaults go in
data\models\router-preset.ini (INI sections keyed by GGUF filename).
#>

[CmdletBinding()]
param(
    [string]$RootDir = '',
    [int]$Port       = 8080
)

if (-not $RootDir) {
    $RootDir = Split-Path -Parent $PSCommandPath
    $RootDir = Split-Path -Parent $RootDir   # bin\.. = project root
}

$RuntimeDir = Join-Path $RootDir 'runtime'
$ModelsDir  = Join-Path $RootDir 'data\models'
$PresetPath = Join-Path $ModelsDir  'router-preset.ini'
$LogDir     = Join-Path $RootDir 'data\logs'
$Port       = 8080

# ---- Auto-pick the best llama-server binary ----
$Bin = $null
foreach ($name in @('llama-server-cuda-12.4.exe', 'llama-server-cuda-11.8.exe', 'llama-server-vulkan.exe', 'llama-server.exe')) {
    $candidate = Join-Path $RuntimeDir $name
    if (Test-Path $candidate) { $Bin = $candidate; break }
}
if (-not $Bin) {
    Write-Host "[ERROR] No llama-server binary found in $RuntimeDir" -ForegroundColor Red
    exit 1
}

# ---- Discover GGUF models ----
$ggufFiles = @(Get-ChildItem $ModelsDir -Filter '*.gguf' -ErrorAction SilentlyContinue | Sort-Object Length -Descending)
if ($ggufFiles.Count -eq 0) {
    Write-Host "[ERROR] No .gguf files in $ModelsDir" -ForegroundColor Red
    exit 1
}

# ---- Compute --models-max based on free VRAM (rough) ----
# 8 GB or less -> 1 resident model. 12 GB -> 2. 16 GB -> 3. 24 GB+ -> 4.
# These are conservative; LRU eviction always kicks in when the GPU
# actually runs out of room regardless of --models-max.
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
Write-Host "  Hermes - llama-server Router Mode"
Write-Host ""
Write-Host "  Binary:      $Bin"
Write-Host "  Models dir:  $ModelsDir"
Write-Host "  Discovered:  $($ggufFiles.Count) GGUF model(s)"
foreach ($g in $ggufFiles) {
    $szGB = [math]::Round($g.Length / 1GB, 2)
    Write-Host ("    [{0,6} GB] {1}" -f $szGB, $g.Name)
}
Write-Host "  Preset:      $(if (Test-Path $PresetPath) { $PresetPath } else { '(none \u2014 using global defaults)' })"
Write-Host "  Models-max:  $modelsMax (LRU eviction)"
Write-Host "  Free VRAM:   $vramFreeMB MB"
Write-Host "  Endpoint:    http://127.0.0.1`:$Port"
Write-Host "============================================================"
Write-Host ""

# ---- Build launch args ----
$argList = @(
    '--models-dir',   $ModelsDir
    '--models-max',   "$modelsMax"
    '--host',         '127.0.0.1'
    '--port',         "$Port"
    '--jinja'
)
if (Test-Path $PresetPath) {
    $argList = @('--models-preset', $PresetPath) + $argList
}

# ---- Truncate log files for a fresh boot ----
$logPath = Join-Path $LogDir 'llm-server.log'
$errPath = Join-Path $LogDir 'llm-server.err'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
'' | Set-Content -Path $logPath -Encoding UTF8
'' | Set-Content -Path $errPath -Encoding UTF8

# ---- Launch detached via .NET Process.Start (true console detachment) ----
# UseShellExecute=$false + CreateNoWindow=$true + CREATE_NO_WINDOW \u2192
# the new process has no console of its own, isn't attached to the
# parent PowerShell's console, and survives the parent closing.
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName               = $Bin
$psi.Arguments              = ($argList -join ' ')
$psi.WorkingDirectory       = $RuntimeDir
$psi.UseShellExecute        = $false
$psi.CreateNoWindow         = $true
$psi.WindowStyle            = 'Hidden'
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError  = $true

$proc = [System.Diagnostics.Process]::Start($psi)

# Drain redirected streams to log files asynchronously.
$logWriter = [System.IO.StreamWriter]::new($logPath, $true, [System.Text.UTF8Encoding]::new($false))
$logWriter.AutoFlush = $true
$errWriter = [System.IO.StreamWriter]::new($errPath, $true, [System.Text.UTF8Encoding]::new($false))
$errWriter.AutoFlush = $true
$proc.add_OutputDataReceived({ if ($null -ne $_.Data) { $logWriter.WriteLine($_.Data) } })
$proc.add_ErrorDataReceived({  if ($null -ne $_.Data) { $errWriter.WriteLine($_.Data) } })
$proc.BeginOutputReadLine()
$proc.BeginErrorReadLine()

$llamaPid = $proc.Id
Write-Host "  [pid]   $llamaPid"
Write-Host ""
Write-Host "  llama-server (router) started detached."
Write-Host "  Switch models by sending requests with model='<filename>' \u2014 no restart needed."
Write-Host "  Endpoints:"
Write-Host "    GET  /v1/models                 \u2014 list discovered models"
Write-Host "    POST /models/load  {model:...}  \u2014 preload a specific model"
Write-Host "    POST /models/unload{model:...}  \u2014 evict a model from VRAM"
Write-Host ""
Write-Host "  To stop:  Stop-Process -Id $llamaPid"

# ---- Persist last-launch info ----
$launchInfo = @{
    mode        = 'router'
    binary      = $Bin
    models_dir  = $ModelsDir
    models_max  = $modelsMax
    preset      = if (Test-Path $PresetPath) { $PresetPath } else { $null }
    vram_free_mb= $vramFreeMB
    port        = $Port
    pid         = $llamaPid
    discovered  = @($ggufFiles | ForEach-Object { $_.Name })
} | ConvertTo-Json -Compress
$launchInfo | Set-Content -Path (Join-Path $LogDir 'last-launch.json') -Encoding UTF8

# Dispose handles and exit. The child process is fully detached.
$proc.Dispose()
$logWriter.Dispose()
$errWriter.Dispose()
exit 0
