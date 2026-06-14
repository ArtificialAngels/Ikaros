# modules/webui/start.ps1 — Launch hermes-web-ui
[CmdletBinding()]
param([int]$Port = 8648)

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

# Truncate logs (use .NET to avoid file locks)
[System.IO.File]::WriteAllText($logPath, '', [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText($errPath, '', [System.Text.UTF8Encoding]::new($false))

Write-Host "============================================================"
Write-Host "  Hermes - webui (hermes-web-ui)"
Write-Host ""
Write-Host "  Source:     $Source"
Write-Host "  Node:       $NODE"
Write-Host "  Endpoint:   http://127.0.0.1`:$Port"
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
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError  = $true

$psi.EnvironmentVariables['PORT']                                    = "$Port"
$psi.EnvironmentVariables['HERMES_WEB_UI_HOME']                      = $WebuiHome
$psi.EnvironmentVariables['HERMES_HOME']                             = $HermesHome
$psi.EnvironmentVariables['HERMES_WEB_UI_DISABLE_GATEWAY_AUTOSTART'] = '1'
$psi.EnvironmentVariables['CORS_ORIGINS']                            = '*'
$psi.EnvironmentVariables['PYTHONIOENCODING']                        = 'utf-8'
$psi.EnvironmentVariables['PYTHONUTF8']                              = '1'
$psi.EnvironmentVariables['HERMES_AGENT_BRIDGE_PYTHON']              = $PYTHON

# Pin the python interpreter for hermes-web-ui's hermes-cli.ts.
# hermes-web-ui's bundledCliPythonForWindows() short-circuits on this
# env var. Without it, the function falls back to basename(HERMES_BIN)
# === 'hermes.exe', which fails whenever HERMES_BIN is a directory
# (e.g. a stale user-level `setx HERMES_BIN=E:\Hermes Agent\bin` from
# the old supervisor.bat era, which surfaces as "spawn ... ENOENT"
# on every API call that touches the Hermes CLI -- including the Logs
# page). Pinning the portable-python here means webui can never spawn
# a directory by accident, regardless of what HERMES_BIN is.
$psi.EnvironmentVariables['HERMES_AGENT_CLI_PYTHON']                 = $PYTHON

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
    $proc.Dispose(); $logWriter.Dispose(); $errWriter.Dispose()
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

$proc.Dispose(); $logWriter.Dispose(); $errWriter.Dispose()
exit 0
