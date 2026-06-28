# modules/bridge/stop.ps1 — Stop bridge server (Rust or Python)
. $PSScriptRoot\..\..\deps\hermes-env.ps1

$LogDir   = Join-Path $HERMES_ROOT 'data\logs'
$pidFile  = Join-Path $LogDir 'bridge-last-launch.json'

# Method 1: kill by recorded PID
if (Test-Path $pidFile) {
    $info = Get-Content $pidFile -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
    if ($info -and $info.pid) {
        Write-Host "Stopping bridge (pid $($info.pid))..."
        Stop-Process -Id $info.pid -Force -ErrorAction SilentlyContinue
    }
}

# Method 2: kill Rust bridge by binary name
Get-CimInstance Win32_Process -Filter "Name = 'hermes-bridge-rs.exe'" | ForEach-Object {
    Write-Host "Stopping Rust bridge (pid $($_.ProcessId))..."
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}

# Method 3: kill Python bridge by command line match
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
    $_.CommandLine -match 'bridge\.server'
} | ForEach-Object {
    Write-Host "Stopping Python bridge (pid $($_.ProcessId))..."
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}

exit 0
