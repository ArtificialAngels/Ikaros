# modules/webui/health.ps1 — Health check
. $PSScriptRoot\..\..\deps\hermes-env.ps1

$m = Get-Content "$PSScriptRoot\module.json" | ConvertFrom-Json
$net = $m.network

try {
    $uri = "http://$($net.host):$($net.port)$($net.health_endpoint)"
    $r = Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec ($net.health_timeout_ms / 1000) -ErrorAction Stop
    if ($r.StatusCode -eq 200) {
        Write-Output "OK"
        exit 0
    }
} catch {
    Write-Output "FAIL: $_"
}
exit 1
