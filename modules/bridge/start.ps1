# modules/bridge/start.ps1 — Launch hermes-bridge-rs (Rust bridge)
#
# Phase 1 (2026-06-28): Replace Python uvicorn bridge with Rust bridge.
# The Rust binary is at bridge-rs/target/release/hermes-bridge-rs.exe.
# It listens on :7860 (same as before), proxies to llama-server :8080.
#
# Python bridge preserved at bridge/ (renamed from bridge/server.py).
# To rollback: revert this file to the Python launch version.

[CmdletBinding()]
param([int]$Port = 7860)

. $PSScriptRoot\..\..\deps\hermes-env.ps1

$LogDir     = Join-Path $HERMES_ROOT 'data\logs'
$BridgeRs   = Join-Path $HERMES_ROOT 'bridge-rs\target\release\hermes-bridge-rs.exe'
$BridgePy   = Join-Path $HERMES_ROOT 'portable-python\python.exe'

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# Choose Rust bridge if available, fallback to Python
$useRust = Test-Path $BridgeRs

if ($useRust) {
    Write-Host "============================================================"
    Write-Host "  Hermes - bridge (Rust)"
    Write-Host ""
    Write-Host "  Binary:     $BridgeRs"
    Write-Host "  Endpoint:   http://127.0.0.1`:$Port"
    Write-Host "  Upstream:   http://127.0.0.1:8080 (llama-server)"
    Write-Host "============================================================"
    Write-Host ""

    # Kill zombie on port
    Write-Host "  Checking for zombie processes on port $Port..."
    try {
        $netstat = & netstat -aon -p tcp 2>$null
        foreach ($line in $netstat) {
            if ($line -match "TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)$") {
                $zpid = [int]$matches[1]
                if ($zpid -gt 0) {
                    Write-Host "  Killing zombie PID $zpid..."
                    try { & taskkill /F /PID $zpid /T 2>$null | Out-Null } catch {}
                }
            }
        }
    } catch {}
    Start-Sleep -Seconds 1

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName               = $BridgeRs
    $psi.Arguments              = ''
    $psi.WorkingDirectory       = $HERMES_ROOT
    $psi.UseShellExecute        = $false
    $psi.CreateNoWindow         = $true
    $psi.WindowStyle            = 'Hidden'
    $psi.RedirectStandardInput  = $false
    $psi.RedirectStandardOutput = $false
    $psi.RedirectStandardError  = $false

    # Env vars
    $psi.EnvironmentVariables['RUST_LOG'] = 'hermes_bridge_rs=info'
    $psi.EnvironmentVariables['HERMES_ROOT'] = $HERMES_ROOT
    $hermesLlamaUrl = [Environment]::GetEnvironmentVariable('HERMES_LLAMA_URL')
    if ($hermesLlamaUrl) {
        $psi.EnvironmentVariables['HERMES_LLAMA_UPSTREAMS'] = $hermesLlamaUrl
    }
    $hermesFallbacks = [Environment]::GetEnvironmentVariable('HERMES_LLAMA_FALLBACKS')
    if ($hermesFallbacks) {
        $existing = $psi.EnvironmentVariables['HERMES_LLAMA_UPSTREAMS']
        if ($existing) {
            $psi.EnvironmentVariables['HERMES_LLAMA_UPSTREAMS'] = "$existing,$hermesFallbacks"
        } else {
            $psi.EnvironmentVariables['HERMES_LLAMA_UPSTREAMS'] = $hermesFallbacks
        }
    }

    $proc = [System.Diagnostics.Process]::Start($psi)
    Start-Sleep -Seconds 2

    if ($proc.HasExited) {
        Write-Host "  [FAIL] Rust bridge exited immediately with code $($proc.ExitCode)" -ForegroundColor Red
        $proc.Dispose()

        # Fallback to Python bridge
        Write-Host "  Falling back to Python bridge..." -ForegroundColor Yellow
        $useRust = $false
    } else {
        Write-Host "  [pid]   $($proc.Id)"
        Write-Host "  Rust bridge started on :$Port"

        $launchInfo = @{
            kind   = 'bridge-rs'
            binary = $BridgeRs
            port   = $Port
            pid    = $proc.Id
        } | ConvertTo-Json -Compress
        $launchInfo | Set-Content -Path (Join-Path $LogDir 'bridge-last-launch.json') -Encoding UTF8

        $proc.Dispose()
        exit 0
    }
}

if (-not $useRust) {
    # ── Python bridge fallback ──
    if (-not (Test-Path $BridgePy)) {
        Write-Host "[ERROR] Neither Rust bridge nor Python found" -ForegroundColor Red
        exit 1
    }

    Write-Host "============================================================"
    Write-Host "  Hermes - bridge (Python fallback)"
    Write-Host "  Python:     $BridgePy"
    Write-Host "  Endpoint:   http://127.0.0.1`:$Port"
    Write-Host "============================================================"

    $HermesHome  = Join-Path $HERMES_ROOT 'data\hermes-agent'
    $ModelsDir   = Join-Path $HERMES_ROOT 'data\models'
    $argList     = @('-m', 'bridge.server', '--host', '127.0.0.1', '--port', "$Port", '--log-level', 'info')

    $innerCmd  = '"' + ($BridgePy -replace '"','\"') + '"'
    $innerArgs = ($argList | ForEach-Object {
        if ($_ -match '\s|"') { '"' + ($_ -replace '"','\"') + '"' } else { $_ }
    }) -join ' '

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName               = 'cmd.exe'
    $psi.Arguments              = '/c "' + $innerCmd + ' ' + $innerArgs + '" < NUL'
    $psi.WorkingDirectory       = $HERMES_ROOT
    $psi.UseShellExecute        = $false
    $psi.CreateNoWindow         = $true
    $psi.WindowStyle            = 'Hidden'
    $psi.RedirectStandardInput  = $false
    $psi.RedirectStandardOutput = $false
    $psi.RedirectStandardError  = $false

    foreach ($k in @('HERMES_HOME','HERMES_LLAMA_URL','HERMES_MODELS_DIR','HERMES_BRIDGE_PORT')) {
        $v = [Environment]::GetEnvironmentVariable($k)
        if ($v) { $psi.EnvironmentVariables[$k] = $v }
    }
    $psi.EnvironmentVariables['HERMES_HOME']        = $HermesHome
    $psi.EnvironmentVariables['HERMES_MODELS_DIR']  = $ModelsDir
    $psi.EnvironmentVariables['HERMES_BRIDGE_PORT'] = "$Port"
    $psi.EnvironmentVariables['PYTHONPATH']         = "$HERMES_ROOT;$HERMES_ROOT\hermes-agent"

    $proc = [System.Diagnostics.Process]::Start($psi)
    Start-Sleep -Seconds 2

    if ($proc.HasExited) {
        Write-Host "  [FAIL] Python bridge exited with code $($proc.ExitCode)" -ForegroundColor Red
        $proc.Dispose()
        exit 1
    }

    Write-Host "  [pid]   $($proc.Id)"
    Write-Host "  Python bridge started on :$Port"
    $proc.Dispose()
}

exit 0
