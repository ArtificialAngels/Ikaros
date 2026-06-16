# modules/webui_proxy/stop.ps1 — Stop webui_proxy (Python thin proxy)
. $PSScriptRoot\..\..\deps\hermes-env.ps1

$LogDir   = Join-Path $HERMES_ROOT 'data\logs'
$pidFile  = Join-Path $LogDir 'webui_proxy-last-launch.json'

# Method 1: kill by recorded PID
if (Test-Path $pidFile) {
    $info = Get-Content $pidFile -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
    if ($info -and $info.launcher_pid) {
        Write-Host "Stopping webui_proxy launcher (pid $($info.launcher_pid))..."
        Stop-Process -Id $info.launcher_pid -Force -ErrorAction SilentlyContinue
    }
    if ($info -and $info.server_pid) {
        Write-Host "Stopping webui_proxy server (pid $($info.server_pid))..."
        Stop-Process -Id $info.server_pid -Force -ErrorAction SilentlyContinue
    }
}

# Method 2: kill any stray python that is running THIS script
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
    $_.CommandLine -match 'webui_proxy\.py'
} | ForEach-Object {
    Write-Host "Stopping stray webui_proxy (pid $($_.ProcessId))..."
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}

exit 0
