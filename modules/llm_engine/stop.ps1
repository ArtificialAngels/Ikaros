# modules/llm_engine/stop.ps1 — Stop llama-server
. $PSScriptRoot\..\..\deps\hermes-env.ps1

$LogDir   = Join-Path $HERMES_ROOT 'data\logs'
$pidFile  = Join-Path $LogDir 'llm-engine-last-launch.json'

# Method 1: kill by recorded PID
if (Test-Path $pidFile) {
    $info = Get-Content $pidFile -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
    if ($info -and $info.pid) {
        Write-Host "Stopping llm-engine (pid $($info.pid))..."
        Stop-Process -Id $info.pid -Force -ErrorAction SilentlyContinue
    }
}

# Method 2: kill by image name (catches stragglers)
$names = @('llama-server.exe', 'llama-server-cuda-12.4.exe', 'llama-server-cuda-11.8.exe', 'llama-server-vulkan.exe')
foreach ($n in $names) {
    Get-Process -Name $n -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "Stopping stray $n (pid $($_.Id))..."
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
}

exit 0
