# modules/bridge/start.ps1 — Launch hermes-bridge-rs (Rust bridge)
#
# Phase 1 (2026-06-28): Replace Python uvicorn bridge with Rust bridge.
# The Rust binary is at bridge-rs/target/release/hermes-bridge-rs.exe.
# It listens on :7860 (same as before), proxies to llama-server :8080.
#
# Rust bridge ONLY — Python bridge removed 2026-06-28 (see commit history).
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
    # Add Phi-4-Mini dedicated (:19934) + Qwen3.5-9B (:8081) as upstreams for voice chat
    # 2026-06-29 哥哥拍板: 桥支持多端口并联 (:8080 phi4 + :8081 qwen3.5-9b + :19934 phi4-voice)
    # HERMES_LLAMA_UPSTREAMS 逗号分隔, 桥自动 health check 跟 supervisor 监控
    $existing = $psi.EnvironmentVariables['HERMES_LLAMA_UPSTREAMS']
    if (-not $existing) {
        # 默认 3 个 upstreams (哥哥 6-29 Plan A: phi4 全 GPU + qwen35-9b CPU 混合 + phi4-voice 专用)
        $psi.EnvironmentVariables['HERMES_LLAMA_UPSTREAMS'] = 'http://127.0.0.1:8080,http://127.0.0.1:8081,http://127.0.0.1:19934'
    }

    $hermesFallbacks = [Environment]::GetEnvironmentVariable('HERMES_LLAMA_FALLBACKS')
    if ($hermesFallbacks) {
        $existing2 = $psi.EnvironmentVariables['HERMES_LLAMA_UPSTREAMS']
        if ($existing2) {
            $psi.EnvironmentVariables['HERMES_LLAMA_UPSTREAMS'] = "$existing2,$hermesFallbacks"
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

# ─── End of Rust bridge launcher ───
# Python bridge (bridge/server.py + voice_server.py) was removed 2026-06-28 (see git log)
# Rust bridge fully replaces it. See bridge-rs/src/main.rs
