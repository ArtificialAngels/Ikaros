# modules/agent_bridge_stub/health.ps1 — HTTP health check for the router.
#
# The stub router is now a FastAPI app (v2.0.0) with a /health endpoint.
# Mirrors modules/bridge/health.ps1 conventions.
[CmdletBinding()]
param([int]$Port = 18765)

. $PSScriptRoot\..\..\deps\hermes-env.ps1

try {
    $uri = "http://127.0.0.1:$Port/health"
    $response = Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 3
    if ($response.StatusCode -eq 200) {
        $json = $response.Content | ConvertFrom-Json
        if ($json.status -eq 'ok') {
            Write-Output "OK: router up (bridge=$($json.bridge), broker=$($json.broker))"
            exit 0
        }
    }
    Write-Output "FAIL: unexpected response (status=$($response.StatusCode))"
    exit 1
} catch {
    Write-Output "FAIL: $_"
    exit 1
}
