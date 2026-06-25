# modules/agent_bridge_stub/stop.ps1 — Stop the agent bridge stub.
#
# Kills the python process recorded in *-last-launch.json. If that file
# is missing, falls back to scanning the port via netstat (same approach
# used by modules/llm_engine/stop.ps1).
[CmdletBinding()]
param([int]$Port = 18765)

. $PSScriptRoot\..\..\deps\hermes-env.ps1

$LogDir   = Join-Path $HERMES_ROOT 'data\logs'
$pidFile  = Join-Path $LogDir 'agent_bridge_stub-last-launch.json'

$killed = $false

# Method 1: recorded PID
if (Test-Path $pidFile) {
    try {
        $info = Get-Content $pidFile -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
        if ($info) {
            foreach ($pid in @($info.server_pid, $info.launcher_pid)) {
                if ($pid) {
                    Write-Host "Stopping agent_bridge_stub (pid $pid)..."
                    Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                    $killed = $true
                }
            }
        }
    } catch {}
}

# Method 2: catch stragglers by image name (any python process binding
# the stub port at this point is a zombie we own)
try {
    $netstat = & netstat -aon -p tcp 2>$null
    foreach ($line in $netstat) {
        if ($line -match "TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)$") {
            $pid = [int]$matches[1]
            Write-Host "Stopping stray stub holder (pid $pid)..."
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            $killed = $true
        }
    }
} catch {}

if (-not $killed) {
    Write-Host "agent_bridge_stub: nothing to stop"
}
exit 0
