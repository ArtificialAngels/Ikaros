# modules/agent_bridge_stub/start.ps1 — Launch the agent bridge reverse-proxy router.
#
# The stub router occupies :18765 and routes requests by path prefix:
#   /v1/reach, /v1/notebooklm, /v1/ikaros, /v1/llama, /v1/models -> :7860 bridge
#   everything else                                                -> :18766 broker
#
# The webui-managed broker is moved to :18766 via HERMES_AGENT_BRIDGE_ENDPOINT
# in modules/webui/start.ps1.  This lets the router sit at :18765 (the port
# the webui frontend expects) and transparently dispatch to the right upstream.
#
# Spawns agent_bridge_stub.py as a detached child so this launcher can
# return immediately and the supervisor can run the port check on :18765.
# The python process is recorded into a *-last-launch.json for stop.ps1
# to pick up later (mirrors modules/llm_engine/start.ps1 conventions).
[CmdletBinding()]
param([int]$Port = 18765)

. $PSScriptRoot\..\..\deps\hermes-env.ps1

$LogDir  = Join-Path $HERMES_ROOT 'data\logs'
$logPath = Join-Path $LogDir 'agent_bridge_stub.log'
$errPath = Join-Path $LogDir 'agent_bridge_stub.err'
$launchJson = Join-Path $LogDir 'agent_bridge_stub-last-launch.json'

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# If a previous instance is still on :18765, kill it first. (Defensive:
# hermes-stop.bat should have done this, but if it crashed mid-stop the
# port can still be held by a zombie. Don't trust the supervisor order.)
$existing = $null
try {
    $netstat = & netstat -aon -p tcp 2>$null
    foreach ($line in $netstat) {
        if ($line -match "TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)$") {
            $existing = [int]$matches[1]; break
        }
    }
} catch {}
if ($existing) {
    Write-Host "  [WARN] :$Port already held by pid $existing; killing before launch"
    Stop-Process -Id $existing -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
}

Write-Host "============================================================"
Write-Host "  Hermes - agent_bridge_stub (reverse-proxy router)"
Write-Host ""
Write-Host "  Port:       $Port (http://127.0.0.1`:$Port)"
Write-Host "  Script:     modules\agent_bridge_stub\agent_bridge_stub.py"
Write-Host "  Bridge:     http://127.0.0.1:7860  (/v1/reach, /v1/ikaros, ...)"
Write-Host "  Broker:     tcp://127.0.0.1:18766  (webui chat-run, etc.)"
Write-Host "============================================================"
Write-Host ""

# Launch the python process DETACHED so this script can exit cleanly.
# We deliberately do NOT redirect stdio here: the supervisor already
# owns the log files (log_f = open(path, "a", buffering=1) in start_module)
# and redirects child stdio into them. See modules/webui_proxy/start.ps1
# for the full rationale on avoiding add_OutputDataReceived (the
# Runspace-after-exit crash trap documented in AGENTS.md §0.8).
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName               = (Join-Path $HERMES_ROOT 'portable-python\python.exe')
$psi.Arguments              = '-u modules\agent_bridge_stub\agent_bridge_stub.py'
$psi.WorkingDirectory       = $HERMES_ROOT
$psi.UseShellExecute        = $false
$psi.CreateNoWindow         = $true
$psi.WindowStyle            = 'Hidden'
$psi.RedirectStandardInput  = $false
$psi.RedirectStandardOutput = $false
$psi.RedirectStandardError  = $false

# Tell the router where the bridge and broker live.
$psi.EnvironmentVariables['BRIDGE_URL'] = 'http://127.0.0.1:7860'
$psi.EnvironmentVariables['BROKER_URL'] = 'http://127.0.0.1:18766'

$proc = [System.Diagnostics.Process]::Start($psi)
Start-Sleep -Milliseconds 800

if ($proc.HasExited) {
    Write-Host "  [FAIL] python exited immediately (rc=$($proc.ExitCode))" -ForegroundColor Red
    $proc.Dispose()
    exit 1
}

# Recover the real PID by binding to the port: netstat shows the python
# child holding :18765. (CreateProcess gives us the launcher wrapper PID;
# the actual socket-owning PID is the one netstat reports.)
$serverPid = $null
try {
    $netstat = & netstat -aon -p tcp 2>$null
    foreach ($line in $netstat) {
        if ($line -match "TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)$") {
            $serverPid = [int]$matches[1]; break
        }
    }
} catch {}
if (-not $serverPid) { $serverPid = $proc.Id }

Write-Host "  [pid]   launcher=$($proc.Id)  server=$serverPid"
Write-Host "  agent_bridge_stub started."

$launchInfo = @{
    launcher_pid = $proc.Id
    server_pid   = $serverPid
    port         = $Port
    script       = 'modules\agent_bridge_stub\agent_bridge_stub.py'
    python       = $psi.FileName
} | ConvertTo-Json -Compress
$launchInfo | Set-Content -Path $launchJson -Encoding UTF8

# Keep start.ps1 alive so the supervisor's poll() sees it running.
# When the supervisor sends SIGTERM (via stop.ps1), this WaitForSingleObject
# returns and we exit cleanly.
try {
    $proc.WaitForExit()
} catch {}
$rc = if ($proc.HasExited) { $proc.ExitCode } else { 0 }
$proc.Dispose()
exit $rc
