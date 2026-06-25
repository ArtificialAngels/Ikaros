# modules/agent_bridge_stub/health.ps1 — TCP-level health check.
#
# The stub speaks a custom JSON line protocol (no HTTP), so we do a raw
# TCP connect AND a one-line ping round-trip to confirm it answers the
# protocol webui uses. Mirrors modules/llm_engine/health.ps1 (HTTP) for
# the response shape, but uses System.Net.Sockets directly.
[CmdletBinding()]
param([int]$Port = 18765)

. $PSScriptRoot\..\..\deps\hermes-env.ps1

try {
    $client = New-Object System.Net.Sockets.TcpClient
    $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
    $ok = $iar.AsyncWaitHandle.WaitOne(2000, $false)
    if (-not $ok) {
        $client.Close()
        Write-Output "FAIL: connect timeout"
        exit 1
    }
    $client.EndConnect($iar)
    $stream = $client.GetStream()
    $writer = New-Object System.IO.StreamWriter($stream, [System.Text.UTF8Encoding]::new($false))
    $reader = New-Object System.IO.StreamReader($stream, [System.Text.UTF8Encoding]::new($false))
    $writer.NewLine = "`n"
    $writer.WriteLine('{"action":"ping"}')
    $writer.Flush()
    $resp = $reader.ReadLine()
    $client.Close()
    if ($resp -and ($resp -match '"ok"\s*:\s*true')) {
        Write-Output "OK"
        exit 0
    } else {
        Write-Output "FAIL: bad response: $resp"
        exit 1
    }
} catch {
    Write-Output "FAIL: $_"
    exit 1
}
