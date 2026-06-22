# modules/webui/start.ps1 — Launch hermes-web-ui
[CmdletBinding()]
param([int]$Port = 8649)

. $PSScriptRoot\..\..\deps\hermes-env.ps1

$LogDir      = Join-Path $HERMES_ROOT 'data\logs'
$logPath     = Join-Path $LogDir 'webui.log'
$errPath     = Join-Path $LogDir 'webui.err'
$WebuiHome   = Join-Path $HERMES_ROOT 'data\webui'
$HermesHome  = Join-Path $HERMES_ROOT 'data\hermes-agent'

# Resolve hermes-web-ui package (npm global install only -- the dev source
# under .\hermes-web-ui\ was retired in 2026-06-15; see AGENTS.md §0.7a).
$WebuiDir  = Join-Path $HERMES_ROOT 'runtime\node23\node_modules\hermes-web-ui'
$Launcher  = Join-Path $WebuiDir  'bin\hermes-web-ui.mjs'

if (-not (Test-Path $Launcher)) {
    Write-Host "[ERROR] hermes-web-ui not found at $Launcher" -ForegroundColor Red
    Write-Host "        Run this from the project root to install it:" -ForegroundColor Red
    Write-Host "          cd runtime\node23 ^&^& npm install -g hermes-web-ui" -ForegroundColor Red
    exit 1
}
$Source    = 'runtime/node23 (npm global)'

if (-not (Test-Path $NODE)) {
    Write-Host "[ERROR] Node missing: $NODE" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $LogDir))    { New-Item -ItemType Directory -Path $LogDir    -Force | Out-Null }
if (-not (Test-Path $WebuiHome)) { New-Item -ItemType Directory -Path $WebuiHome -Force | Out-Null }

# NOTE: we DO NOT truncate $logPath/$errPath here. The supervisor's
# start_module() already truncated them and holds them open in append mode
# (supervisor's start_module creates log_f = open(path, "a", buffering=1) and
# closes them only after the module's port-health check passes). Trying to
# WriteAllText (truncate) while the supervisor's handle is open on Windows
# raises IOException("access denied"); the StreamWriter ctor then fails too,
# leaving $logWriter == $null and the cleanup at the bottom throws
# "InvokeMethodOnNull". Best practice: append-only, no truncate from here.

Write-Host "============================================================"
Write-Host "  Hermes - webui (hermes-web-ui)"
Write-Host ""
Write-Host "  Source:     $Source"
Write-Host "  Node:       $NODE"
Write-Host "  Endpoint:   http://127.0.0.1`:$Port   (proxied to :8648 via modules/webui_proxy)"
Write-Host "============================================================"
Write-Host ""

$argList = @($Launcher, 'start', "$Port")

# Build inner command
$innerCmd = '"' + ($NODE -replace '"','\"') + '"'
$innerArgs = ($argList | ForEach-Object {
    if ($_ -match '\s|"') { '"' + ($_ -replace '"','\"') + '"' }
    else { $_ }
}) -join ' '

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName               = 'cmd.exe'
$psi.Arguments              = '/c "' + $innerCmd + ' ' + $innerArgs + '" < NUL'
$psi.WorkingDirectory       = $WebuiDir
$psi.UseShellExecute        = $false
$psi.CreateNoWindow         = $true
$psi.WindowStyle            = 'Hidden'
$psi.RedirectStandardInput  = $false
$psi.RedirectStandardOutput = $false
$psi.RedirectStandardError  = $false

# Pin PATH explicitly so the webui process — and any detached restart children
# it spawns after a self-update (hermes-web-ui.mjs restart --port) — can find
# `hermes` (portable-python\Scripts) and `npm`/`node` (runtime\node23).
# Without this, the detached restart inherits whatever PATH the OS gave the
# supervisor, which typically lacks portable-python\Scripts → ENOENT when
# the new server tries `spawn hermes gateway run --replace`.
$pythonScripts  = Join-Path $HERMES_ROOT 'portable-python\Scripts'
$inheritedPath  = $psi.EnvironmentVariables['PATH']
$psi.EnvironmentVariables['PATH'] = "$NODE_BIN_DIR;$pythonScripts;$inheritedPath"

$psi.EnvironmentVariables['PORT']                                    = "$Port"
$psi.EnvironmentVariables['HERMES_WEB_UI_HOME']                      = $WebuiHome
$psi.EnvironmentVariables['HERMES_HOME']                             = $HermesHome
$psi.EnvironmentVariables['HERMES_WEB_UI_DISABLE_GATEWAY_AUTOSTART'] = '1'
$psi.EnvironmentVariables['CORS_ORIGINS']                            = '*'
$psi.EnvironmentVariables['PYTHONIOENCODING']                        = 'utf-8'
$psi.EnvironmentVariables['PYTHONUTF8']                              = '1'
$psi.EnvironmentVariables['HERMES_AGENT_BRIDGE_PYTHON']              = $PYTHON
$psi.EnvironmentVariables['HERMES_AGENT_CLI_PYTHON']                 = $PYTHON

# See modules/webui_proxy/start.ps1 for the full rationale: we deliberately
# do NOT use $psi.RedirectStandardOutput/Error = $true + add_OutputDataReceived
# to drain the child's stdio, because the callback is a PowerShell script
# block that needs a Runspace -- and when start.ps1 exits the Runspace is
# disposed, the next background data event throws PSInvalidOperationException,
# the PowerShell host process crashes, and the broken stdout pipe takes the
# child with it. Letting the child inherit PowerShell's stdio (which the
# supervisor has redirected to the per-module log files) avoids all of that.

$proc = [System.Diagnostics.Process]::Start($psi)

Start-Sleep -Seconds 3

if ($proc.HasExited) {
    $rc = $proc.ExitCode
    $errTail = ''
    if (Test-Path $errPath) {
        $lines = Get-Content $errPath -ErrorAction SilentlyContinue
        if ($lines) { $errTail = ($lines | Select-Object -Last 12) -join "`n" }
    }
    Write-Host "  [FAIL] webui exited with code $rc" -ForegroundColor Red
    if ($errTail) { foreach ($l in $errTail -split "`n") { Write-Host "    | $l" -ForegroundColor DarkYellow } }
    if ($null -ne $proc)       { $proc.Dispose() }
    exit 1
}

# Recover server PID from netstat
$serverPid = $null
try {
    $netstat = & netstat -aon -p tcp 2>$null
    foreach ($line in $netstat) {
        if ($line -match "TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)$") {
            $serverPid = [int]$matches[1]; break
        }
    }
} catch {}

Write-Host "  [pid]   launcher=$($proc.Id)  server=$serverPid"
Write-Host "  webui started."

$launchInfo = @{
    kind         = 'webui'
    source       = $Source
    webui_dir    = $WebuiDir
    launcher     = $Launcher
    node         = $NODE
    port         = $Port
    launcher_pid = $proc.Id
    server_pid   = $serverPid
    webui_home   = $WebuiHome
    hermes_home  = $HermesHome
} | ConvertTo-Json -Compress
$launchInfo | Set-Content -Path (Join-Path $LogDir 'webui-last-launch.json') -Encoding UTF8

if ($null -ne $proc)       { $proc.Dispose() }
exit 0
