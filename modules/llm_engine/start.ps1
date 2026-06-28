# modules/llm_engine/start.ps1 — Launch llama-server in router mode
[CmdletBinding()]
param([int]$Port = 8080)

. $PSScriptRoot\..\..\deps\hermes-env.ps1

$ModelsDir  = Join-Path $HERMES_ROOT 'data\models'
$PresetPath = Join-Path $ModelsDir 'router-preset.ini'
$LogDir     = Join-Path $HERMES_ROOT 'data\logs'
$CudaBase   = Join-Path $HERMES_ROOT 'runtime\cuda'

# ---- Detect recommended CUDA version (multi-version: 11.8 / 12.4 / 13.0) ----
$CudaVer = 'cpu'
try {
    $CudaVer = (& $PYTHON -m modules.env_bootstrap.gpu_detect recommend 2>$null).Trim()
    if (-not $CudaVer) { $CudaVer = 'cpu' }
} catch {
    $CudaVer = 'cpu'
}
Write-Host "[llm-engine] recommended CUDA: $CudaVer"

# ---- Auto-pick the best llama-server binary ----
# 2026-06-27: b9826 (9bebfcb4b) 已下载, CPU + CUDA 双 backend 完整.
# runtime/llama-server.exe 现在是 b9826 CUDA 版 (完整 backend + GPU 加速).
# runtime/cuda/12.4/ 下的旧 b9538 已被 b9826 DLLs 覆盖.
# 优先用 runtime/llama-server.exe (单目录, worker CWD 直接找到 DLL).
$Bin = $null

# 1. runtime/llama-server.exe (b9826 CUDA, 同目录有所有 DLL)
$rootCandidate = Join-Path $LLAMACPP_BIN 'llama-server.exe'
if (Test-Path $rootCandidate) { $Bin = $rootCandidate }

# 2. b9826 CUDA sub-directory (fallback 如果将来想独立部署)
if (-not $Bin -and $CudaVer -ne 'cpu') {
    $candidates = @(
        (Join-Path $CudaBase "$CudaVer\b9826\llama-server.exe"),
        (Join-Path $CudaBase "$CudaVer\llama-server-cuda-$CudaVer.exe")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $Bin = $c; break }
    }
}

# 3. b9826 CPU build (无条件可用)
if (-not $Bin) {
    foreach ($name in @('llama-server.exe')) {
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

# ---- Read preferred model from last launch ----
$lastLaunchPath = Join-Path $LogDir 'llm-engine-last-launch.json'
$preferredModel = $null
if (Test-Path $lastLaunchPath) {
    try {
        $lastLaunch = Get-Content $lastLaunchPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($lastLaunch.preferred_model) { $preferredModel = $lastLaunch.preferred_model }
    } catch {}
}

# ---- Read ctx-size per model from router-preset.ini ----
$presetCtxMap = @{}
if (Test-Path $PresetPath) {
    try {
        $iniLines = Get-Content $PresetPath -Encoding UTF8
        $curSection = $null
        foreach ($line in $iniLines) {
            $trimmed = $line.Trim()
            if ($trimmed -match '^\[(.+?)\]$') { $curSection = $Matches[1]; continue }
            if ($trimmed -match '^\s*ctx-size\s*=\s*(\d+)') {
                if ($curSection) { $presetCtxMap[$curSection] = [int]$Matches[1] }
            }
        }
    } catch {}
}

# ---- Compute --models-max: continuous VRAM budget (no tiered steps) ----
# Reserve 1GB for OS/compositor via --fit-target. Use total VRAM (not free)
# because other GPU consumers may release memory when we load models.
$modelsMax = 0
$usableVRAM_MB = 0
$vramTotalMB = 0
$vramFreeMB = 0
$reserveMB = 1024
try {
    $smiTotal = (& nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null)
    $smiFree  = (& nvidia-smi --query-gpu=memory.free  --format=csv,noheader,nounits 2>$null)
    if ($smiTotal) {
        $vramTotalMB = [int]($smiTotal -split "`n" | Select-Object -First 1)
        $vramFreeMB  = [int]($smiFree  -split "`n" | Select-Object -First 1)
        $usableVRAM_MB = $vramTotalMB - $reserveMB
    }
} catch {}

if ($usableVRAM_MB -le 0) {
    # Fallback: single model if nvidia-smi unavailable
    $modelsMax = 1
    Write-Host "[llm-engine] VRAM query failed, defaulting models-max=1"
} else {
    $vramUsed = 0
    # Priority: preferred model first (it will be auto-loaded by router on first request)
    if ($preferredModel) {
        $preferredExists = $ggufFiles | Where-Object { $_.Name -eq $preferredModel }
        if (-not $preferredExists) { $preferredModel = $null }
    }
    # Helper: estimate VRAM for one model
    function _EstimateModelVRAM($file, $ctxMap) {
        $sizeMB = [math]::Round($file.Length / 1MB)
        $ctx = 32768
        if ($ctxMap.ContainsKey($file.Name)) { $ctx = $ctxMap[$file.Name] }
        $kvMB = [math]::Round($ctx * 0.0004)
        return ($sizeMB + $kvMB)
    }
    # Try preferred model FIRST (gets the best VRAM slot)
    if ($preferredModel) {
        $pf = $ggufFiles | Where-Object { $_.Name -eq $preferredModel } | Select-Object -First 1
        $modelVRAM = _EstimateModelVRAM $pf $presetCtxMap
        if ($modelVRAM -le $usableVRAM_MB) {
            $vramUsed += $modelVRAM
            $modelsMax += 1
        }
    }
    # Fill remaining budget with other models (sorted by size desc)
    foreach ($g in $ggufFiles) {
        if ($g.Name -eq $preferredModel) { continue }  # already placed
        $modelVRAM = _EstimateModelVRAM $g $presetCtxMap
        if ($vramUsed + $modelVRAM -le $usableVRAM_MB) {
            $vramUsed += $modelVRAM
            $modelsMax += 1
        }
    }
    if ($modelsMax -lt 1) { $modelsMax = 1 }
    Write-Host "[llm-engine] VRAM: total=$vramTotalMB MB, usable=$usableVRAM_MB MB (reserve $reserveMB MB)"
    Write-Host "[llm-engine] Models-max: $modelsMax (estimated VRAM budget: $([math]::Round($vramUsed)) MB)"
}
if ($preferredModel) {
    Write-Host "[llm-engine] Preferred model (from last launch): $preferredModel"
}

Write-Host "============================================================"
Write-Host "  Hermes - llm-engine (llama-server router mode)"
Write-Host ""
Write-Host "  Binary:      $Bin"
Write-Host "  Models dir:  $ModelsDir"
Write-Host "  Discovered:  $($ggufFiles.Count) GGUF model(s)"
foreach ($g in $ggufFiles) {
    $szGB = [math]::Round($g.Length / 1GB, 2)
    $ctx = if ($presetCtxMap.ContainsKey($g.Name)) { $presetCtxMap[$g.Name] } else { 32768 }
    $kvMB = [math]::Round($ctx * 0.0004)
    $pref = if ($g.Name -eq $preferredModel) { ' ★ preferred' } else { '' }
    Write-Host ("    [{0,6} GB] ctx={1,6} kv~{2,4}MB  {3}{4}" -f $szGB, $ctx, $kvMB, $g.Name, $pref)
}
# ---- Auto-generate router-preset.ini from config/models.yaml ----
$presetGenerated = $false
$configYamlPath = Join-Path $HERMES_ROOT 'config\models.yaml'
if (-not (Test-Path $PresetPath) -and (Test-Path $configYamlPath)) {
    try {
        $yamlContent = Get-Content $configYamlPath -Raw -Encoding UTF8
        # Extract router_preset defaults
        $defaultCtx = 32768; $defaultNgl = 16; $defaultTemp = 0.7
        if ($yamlContent -match 'default_ctx_size:\s*(\d+)') { $defaultCtx = [int]$matches[1] }
        if ($yamlContent -match 'default_ngl:\s*(\d+)') { $defaultNgl = [int]$matches[1] }
        if ($yamlContent -match 'default_temp:\s*([\d.]+)') { $defaultTemp = [float]$matches[1] }

        # Build preset lines: one [section] per discovered GGUF, keyed by filename
        $presetLines = @("# Auto-generated by llm_engine/start.ps1 — $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
        $presetLines += "# Override: edit config/models.yaml → router_preset section, or delete this file to regenerate."
        $presetLines += ""

        # Try to map discovered GGUF files to config profiles
        foreach ($g in $ggufFiles) {
            $modelName = $g.Name
            $ctx = $defaultCtx; $ngl = $defaultNgl; $temp = $defaultTemp

            # Look for a matching profile in models.yaml by GGUF path
            $profilePattern = "$([regex]::Escape($modelName))"
            if ($yamlContent -match "chat:\s*.*$profilePattern") {
                # Extract profile section (hacky but works for YAML subset)
                $ctxMatch = [regex]::Match($yamlContent, "(?s)chat:\s*.*$profilePattern.*?context_size:\s*(\d+)")
                if ($ctxMatch.Success) { $ctx = [int]$ctxMatch.Groups[1].Value }
                $nglMatch = [regex]::Match($yamlContent, "(?s)chat:\s*.*$profilePattern.*?n_gpu_layers:\s*(\d+)")
                if ($nglMatch.Success) { $ngl = [int]$nglMatch.Groups[1].Value }
            }

            $presetLines += "[$modelName]"
            $presetLines += "ctx-size = $ctx"
            $presetLines += "n-gpu-layers = $ngl"
            $presetLines += "temperature = $temp"
            $presetLines += ""
        }

        [System.IO.File]::WriteAllLines($PresetPath, $presetLines, [System.Text.UTF8Encoding]::new($false))
        $presetGenerated = $true
        Write-Host "  Preset:      auto-generated → $PresetPath ($($ggufFiles.Count) models)"
    } catch {
        Write-Host "  Preset:      generation failed ($_), using global defaults"
    }
}

if (-not $presetGenerated) {
    Write-Host "  Preset:      $(if (Test-Path $PresetPath) { $PresetPath } else { '(none -- using global defaults)' })"
}
Write-Host "  Models-max:  $modelsMax (LRU eviction, continuous VRAM)"
Write-Host "  VRAM:        total=$vramTotalMB usable=$usableVRAM_MB reserve=$reserveMB MB"
Write-Host ("  Preferred:   {0}" -f $(if ($preferredModel) { $preferredModel } else { '(none)' }))
Write-Host "  Endpoint:    http://127.0.0.1`:$Port"
Write-Host "  Hot-switch:  portable-python\python.exe bin\hermes-models.py switch <name>"
Write-Host "============================================================"
Write-Host ""

# ---- Build launch args ----
# Global defaults act as fallback for child processes spawned by the router.
# Per-model presets in router-preset.ini override ctx-size / n-gpu-layers / temp.
# --fit on --fit-target 1024: llama-server auto-adjusts ngl and ctx per model
# to fit in VRAM with 1GB margin. No need for global --n-gpu-layers.
$argList = @(
    '--models-dir',   $ModelsDir
    '--models-max',   "$modelsMax"
    '--host',         '127.0.0.1'
    '--port',         "$Port"
    '--jinja'
    '--fit',          'on'
    '--fit-target',   "$reserveMB"
    '--fit-ctx',      '4096'
    '-ctk',           'q4_0'
    '-ctv',           'q4_0'
    '--flash-attn',   'on'
    '--cont-batching'
    '--mlock'
    '--reasoning',    'auto'
    '--temp',         '0.7'
)
if (Test-Path $PresetPath) {
    # ── Fix router mode: --models-dir 在 b9826 中不被 model= 路径解析使用 ──
    # child process CWD = $LLAMACPP_BIN (runtime\), 所以相对路径找不到.
    # 策略: 把 model=xxx.gguf 替换为绝对路径 $ModelsDir\xxx.gguf
    $presetContent = Get-Content $PresetPath -Encoding UTF8
    $fixedContent = $presetContent | ForEach-Object {
        if ($_ -match '^model\s*=\s*(.+)$') {
            $rawPath = $Matches[1].Trim()
            if ($rawPath -notmatch '^[A-Za-z]:\\') {
                "model = $ModelsDir\$rawPath"
            } else {
                $_
            }
        } else {
            $_
        }
    }
    $fixedPresetPath = Join-Path $LogDir 'router-preset-abs.ini'
    # MUST be UTF8 without BOM — llama-server's config parser rejects BOM
    [System.IO.File]::WriteAllText($fixedPresetPath, ($fixedContent -join "`r`n"), [System.Text.UTF8Encoding]::new($false))
    $argList = @('--models-preset', $fixedPresetPath) + $argList
}

# ---- Truncate log files ----
$logPath = Join-Path $LogDir 'llm-engine.log'
$errPath = Join-Path $LogDir 'llm-engine.err'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# NOTE: we DO NOT truncate $logPath/$errPath here. The supervisor's
# start_module() already truncated them and holds them open in append mode
# (see bin/hermes-supervisor.py: log_f = open(path, "a", buffering=1)).
# Trying to WriteAllText (truncate) while the supervisor's handle is open
# on Windows raises IOException("access denied"). Best practice: append-only.

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
$psi.RedirectStandardOutput = $false
$psi.RedirectStandardError  = $false

# See modules/webui_proxy/start.ps1 for the rationale: we deliberately do
# NOT use $psi.RedirectStandardOutput/Error = $true + add_OutputDataReceived.
# Letting the child inherit PowerShell's stdio (captured by the supervisor
# to the per-module log files) avoids the PowerShell host crash that
# happens when the Runspace is disposed after start.ps1 exits.

$proc = [System.Diagnostics.Process]::Start($psi)

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
    $proc.Dispose()
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
    mode            = 'router'
    binary          = $Bin
    models_dir      = $ModelsDir
    models_max      = $modelsMax
    preset          = if (Test-Path $PresetPath) { $PresetPath } else { $null }
    vram_total_mb   = $vramTotalMB
    vram_free_mb    = $vramFreeMB
    vram_usable_mb  = $usableVRAM_MB
    vram_reserve_mb = $reserveMB
    preferred_model = $preferredModel
    port            = $Port
    pid             = $llamaPid
    cuda_version    = $CudaVer
    fit_enabled     = $true
    kv_cache_type   = 'q4_0'
    flash_attn      = $true
    mlock           = $true
    reasoning       = 'auto'
    discovered      = @($ggufFiles | ForEach-Object { $_.Name })
} | ConvertTo-Json -Compress
$launchInfo | Set-Content -Path (Join-Path $LogDir 'llm-engine-last-launch.json') -Encoding UTF8

$proc.Dispose()
exit 0
