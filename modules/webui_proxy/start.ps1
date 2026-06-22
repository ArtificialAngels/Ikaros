# modules/webui_proxy/start.ps1 — Launch the webui_proxy (Python) on :8648
# Forwards to upstream hermes-web-ui on :8649 except the corrected
# `/api/hermes/usage/stats` endpoint. See webui_proxy.py for the why.
[CmdletBinding()]
param([int]$Port = 8648, [string]$Upstream = 'http://127.0.0.1:8649')

. $PSScriptRoot\..\..\deps\hermes-env.ps1

$LogDir   = Join-Path $HERMES_ROOT 'data\logs'
$logPath  = Join-Path $LogDir 'webui_proxy.log'
$errPath  = Join-Path $LogDir 'webui_proxy.err'
$Script   = Join-Path $PSScriptRoot 'webui_proxy.py'

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# NOTE: we DO NOT truncate $logPath/$errPath here. The supervisor's
# start_module() already truncated them and holds them open in append mode
# (supervisor's start_module creates log_f = open(path, "a", buffering=1) and
# closes them only after the module's port-health check passes). Trying to
# WriteAllText (truncate) while the supervisor's handle is open on Windows
# raises IOException("access denied"); the StreamWriter ctor then fails too,
# leaving $logWriter == $null and the cleanup at the bottom throws
# "InvokeMethodOnNull". Best practice: append-only, no truncate from here.

if (-not (Test-Path $PYTHON)) {
    Write-Host "[ERROR] Python missing: $PYTHON" -ForegroundColor Red
    exit 1
}

Write-Host "============================================================"
Write-Host "  Hermes - webui_proxy (Python thin proxy in front of webui)"
Write-Host ""
Write-Host "  Port:     $Port"
Write-Host "  Upstream: $Upstream"
Write-Host "  Script:   $Script"
Write-Host "============================================================"
Write-Host ""

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName               = $PYTHON
$psi.Arguments              = "-u `"$Script`" --port $Port --upstream $Upstream --state-db data/hermes-agent/state.db"
# NOTE 2026-06-22: WorkingDirectory in ProcessStartInfo only applies to the
# .NET Process.Start() call — but the child INHERITS powershell's OWN cwd,
# which was set by supervisor to modules/webui_proxy/. We override it by
# passing the working directory via the START /D switch in cmd, which
# Windows applies to the process before its first user-mode instruction.
$psi.Arguments              = "/c cd /d `"$HERMES_ROOT`" && `"$PYTHON`" -u `"$Script`" --port $Port --upstream $Upstream --state-db data/hermes-agent/state.db"
$psi.FileName               = "cmd.exe"
$psi.WorkingDirectory       = $HERMES_ROOT
$psi.UseShellExecute        = $false
$psi.CreateNoWindow         = $true
$psi.WindowStyle            = 'Hidden'
$psi.RedirectStandardInput  = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError  = $true
$psi.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
$psi.EnvironmentVariables['PYTHONUTF8']       = '1'
$psi.EnvironmentVariables['HERMES_ROOT']       = $HERMES_ROOT

# IMPORTANT: We do NOT use $psi.RedirectStandardOutput/Error = $true. That
# makes PowerShell host a *pipe* between itself and the python child, then
# install an add_OutputDataReceived callback to drain the pipe. The callback
# is a PowerShell script block which needs a Runspace to execute -- and
# when PowerShell exits (which start.ps1 does immediately after launching
# the child) the Runspace is disposed. The next data event on the
# background thread then throws `PSInvalidOperationException: There is no
# Runspace available` and the entire PowerShell host process crashes, which
# in turn takes the python child with it (the broken stdout pipe raises
# BrokenPipeError in python on its next stderr write).
#
# Instead we let the python child INHERIT PowerShell's own stdio. Supervisor
# already captured PowerShell's stdout/stderr into the per-module log files
# (see bin/hermes-supervisor.py:313-314: `log_f = open(log_path, "a", ...)`
# passed as `stdout=log_f, stderr=err_f` to Popen). The child inherits those
# file handles (Windows: handles are duplicated on CreateProcess when the
# child doesn't redirect its own stdio), so python's writes end up in the
# same log files -- without any in-PowerShell buffering / capture layer.
# No Runspace, no PowerShell crash, no broken pipe.

$psi.UseShellExecute        = $false
$psi.CreateNoWindow         = $true
$psi.WindowStyle            = 'Hidden'
$psi.RedirectStandardInput  = $false
$psi.RedirectStandardOutput = $false
$psi.RedirectStandardError  = $false

$proc = [System.Diagnostics.Process]::Start($psi)

# Brief settle, then health probe
Start-Sleep -Seconds 2
if ($proc.HasExited) {
    $rc = $proc.ExitCode
    $errTail = ''
    if (Test-Path $errPath) {
        $lines = Get-Content $errPath -ErrorAction SilentlyContinue
        if ($lines) { $errTail = ($lines | Select-Object -Last 12) -join "`n" }
    }
    Write-Host "  [FAIL] webui_proxy exited with code $rc" -ForegroundColor Red
    if ($errTail) { foreach ($l in $errTail -split "`n") { Write-Host "    | $l" -ForegroundColor DarkYellow } }
    if ($null -ne $proc)    { $proc.Dispose() }
    exit 1
}

# Recover server PID via netstat (Python prints no banner)
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
Write-Host "  webui_proxy started."

$launchInfo = @{
    kind         = 'webui_proxy'
    script       = $Script
    python       = $PYTHON
    port         = $Port
    upstream     = $Upstream
    launcher_pid = $proc.Id
    server_pid   = $serverPid
    hermes_root  = $HERMES_ROOT
} | ConvertTo-Json -Compress
$launchInfo | Set-Content -Path (Join-Path $LogDir 'webui_proxy-last-launch.json') -Encoding UTF8

if ($null -ne $proc)    { $proc.Dispose() }
exit 0
