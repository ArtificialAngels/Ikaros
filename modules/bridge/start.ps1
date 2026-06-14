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

# Truncate logs (use .NET to avoid file locks)
[System.IO.File]::WriteAllText($logPath, '', [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText($errPath, '', [System.Text.UTF8Encoding]::new($false))

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

# Launch via cmd /c wrapper
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
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError  = $true

# Env vars
foreach ($k in @('HERMES_HOME','HERMES_LLAMA_URL','HERMES_MODELS_DIR','HERMES_BRIDGE_PORT')) {
    $v = [Environment]::GetEnvironmentVariable($k)
    if ($v) { $psi.EnvironmentVariables[$k] = $v }
}
$psi.EnvironmentVariables['HERMES_HOME']        = $HermesHome
$psi.EnvironmentVariables['HERMES_MODELS_DIR']  = $ModelsDir
$psi.EnvironmentVariables['HERMES_BRIDGE_PORT'] = "$Port"
$psi.EnvironmentVariables['PYTHONPATH']         = "$HERMES_ROOT;$HERMES_ROOT\hermes-agent"

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
    $proc.Dispose(); $logWriter.Dispose(); $errWriter.Dispose()
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

$proc.Dispose(); $logWriter.Dispose(); $errWriter.Dispose()
exit 0
