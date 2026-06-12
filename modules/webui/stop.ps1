# modules/webui/stop.ps1 — Stop webui server
. $PSScriptRoot\..\..\deps\hermes-env.ps1

$LogDir   = Join-Path $HERMES_ROOT 'data\logs'
$pidFile  = Join-Path $LogDir 'webui-last-launch.json'

# Method 1: kill by recorded PID
if (Test-Path $pidFile) {
    $info = Get-Content $pidFile -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
    if ($info -and $info.pid) {
        Write-Host "Stopping webui (pid $($info.pid))..."
        Stop-Process -Id $info.pid -Force -ErrorAction SilentlyContinue
    }
    if ($info -and $info.server_pid) {
        Write-Host "Stopping webui server (pid $($info.server_pid))..."
        Stop-Process -Id $info.server_pid -Force -ErrorAction SilentlyContinue
    }
}

# Method 2: kill by command line match
Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" | Where-Object {
    $_.CommandLine -match 'hermes-web-ui'
} | ForEach-Object {
    Write-Host "Stopping stray webui (pid $($_.ProcessId))..."
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}

exit 0
