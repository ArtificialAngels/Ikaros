# modules/bridge/start.ps1 — Launch FastAPI bridge server
[CmdletBinding()]
param([int]$Port = 7860)

. $PSScriptRoot\..\..\deps\hermes-env.ps1

$LogDir       = Join-Path $HERMES_ROOT 'data\logs'
$logPath      = Join-Path $LogDir 'bridge.log'
$errPath      = Join-Path $LogDir 'bridge.err'
$HermesHome   = Join-Path $HERMES_ROOT 'data\hermes-agent'
$ModelsDir    = Join-Path $HERMES_ROOT 'data\models'

if (-not (Test-Path $PYTHON)) {
    Write-Host "[ERROR] Python missing: $PYTHON" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# NOTE: we DO NOT truncate $logPath/$errPath here. The supervisor's
# start_module() already truncated them and holds them open in append mode
# (see bin/hermes-supervisor.py: log_f = open(path, "a", buffering=1)).
# Trying to WriteAllText (truncate) while the supervisor's handle is open
# on Windows raises IOException("access denied"). Best practice: append-only,
# no truncate from here.

Write-Host "============================================================"
Write-Host "  Hermes - bridge (FastAPI)"
Write-Host ""
Write-Host "  Python:     $PYTHON"
Write-Host "  Module:     bridge.server:app"
Write-Host "  Endpoint:   http://127.0.0.1`:$Port"
Write-Host "  HERMES_HOME: $HermesHome"
Write-Host "============================================================"
Write-Host ""

$argList = @('-m', 'bridge.server', '--host', '127.0.0.1', '--port', "$Port", '--log-level', 'info')

# Launch via cmd /c wrapper (detached via CREATE_NEW_PROCESS_GROUP in supervisor)
$innerCmd = '"' + ($PYTHON -replace '"','\"') + '"'
$innerArgs = ($argList | ForEach-Object {
    if ($_ -match '\s|"') { '"' + ($_ -replace '"','\"') + '"' }
    else { $_ }
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

# Env vars
foreach ($k in @('HERMES_HOME','HERMES_LLAMA_URL','HERMES_MODELS_DIR','HERMES_BRIDGE_PORT')) {
    $v = [Environment]::GetEnvironmentVariable($k)
    if ($v) { $psi.EnvironmentVariables[$k] = $v }
}
$psi.EnvironmentVariables['HERMES_HOME']        = $HermesHome
$psi.EnvironmentVariables['HERMES_MODELS_DIR']  = $ModelsDir
$psi.EnvironmentVariables['HERMES_BRIDGE_PORT'] = "$Port"
$psi.EnvironmentVariables['PYTHONPATH']         = "$HERMES_ROOT;$HERMES_ROOT\hermes-agent"

# See modules/webui_proxy/start.ps1 for the rationale: we deliberately do
# NOT use $psi.RedirectStandardOutput/Error = $true + add_OutputDataReceived.
# The callback is a PowerShell script block that needs a Runspace, and
# when start.ps1 exits the Runspace is disposed, causing a host crash that
# breaks the child's stdio pipe. Letting the child inherit PowerShell's
# stdio (already captured by the supervisor) avoids the whole problem.

$proc = [System.Diagnostics.Process]::Start($psi)

Start-Sleep -Seconds 2

if ($proc.HasExited) {
    $rc = $proc.ExitCode
    $errTail = ''
    if (Test-Path $errPath) {
        $lines = Get-Content $errPath -ErrorAction SilentlyContinue
        if ($lines) { $errTail = ($lines | Select-Object -Last 12) -join "`n" }
    }
    Write-Host "  [FAIL] bridge exited immediately with code $rc" -ForegroundColor Red
    if ($errTail) { foreach ($l in $errTail -split "`n") { Write-Host "    | $l" -ForegroundColor DarkYellow } }
    $proc.Dispose()
    exit 1
}

# Recover PID from netstat
$bridgePid = $null
try {
    $netstat = & netstat -aon -p tcp 2>$null
    foreach ($line in $netstat) {
        if ($line -match "TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)$") {
            $bridgePid = [int]$matches[1]; break
        }
    }
} catch {}
if (-not $bridgePid) { $bridgePid = $proc.Id }

Write-Host "  [pid]   $bridgePid"
Write-Host "  bridge started."

$launchInfo = @{
    kind        = 'bridge'
    python      = $PYTHON
    port        = $Port
    pid         = $bridgePid
    hermes_home = $HermesHome
    models_dir  = $ModelsDir
} | ConvertTo-Json -Compress
$launchInfo | Set-Content -Path (Join-Path $LogDir 'bridge-last-launch.json') -Encoding UTF8

$proc.Dispose()
exit 0
