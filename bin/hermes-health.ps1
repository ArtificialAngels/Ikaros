<#
.SYNOPSIS
    Hermes - timestamped liveness check for llama-server.

.DESCRIPTION
    Polls a llama-server endpoint at a fixed interval and reports each
    milestone with a wall-clock timestamp. The probe goes through three
    layers, in order:

        1. /health           — TCP-up + 200 OK (server is bound)
        2. /v1/models        — at least one model advertised (loader done)
        3. /v1/completions   — a tiny prompt actually returns text
                              (the model is fully wired and warm)

    Stops on the first failure and returns $false, or on full success
    returns $true with the elapsed milliseconds for each layer.

    The completion probe is opt-in (default $true) because:
      - it actually generates a few tokens (CPU or GPU time)
      - the prompt format can collide with chat template quirks on
        some models (Qwen 3.5 / 35B-A3B returns empty content with
        the default --jinja template; completions doesn't go through
        chat templating so it's safe).

.PARAMETER Url
    Base URL of the llama-server, e.g. http://127.0.0.1:8080. No trailing
    slash.

.PARAMETER TimeoutSec
    Hard cap on the whole probe. Defaults to 180s — matches the existing
    180s wait in hermes-all.bat.

.PARAMETER PollIntervalMs
    How often to hit /health while waiting. Defaults to 500ms — fast
    enough to feel "instant" once the server binds, slow enough to not
    pin a CPU.

.PARAMETER SkipCompletion
    Set to skip layer 3 (the model warm-up probe). Useful when you only
    care about "port is up" and not "model can answer".

.EXAMPLE
    if (Wait-LlamaReady -Url "http://127.0.0.1:8080") {
        Write-Host "ready"
    }
#>
[CmdletBinding()]
param(
    [string]$Url = "http://127.0.0.1:8080",
    [int]$TimeoutSec = 180,
    [int]$PollIntervalMs = 500,
    [switch]$SkipCompletion
)

$ErrorActionPreference = "Continue"

function Get-Now {
    return (Get-Date).ToString("HH:mm:ss.fff")
}

function Write-Probe {
    param([string]$Msg, [string]$Color = "Gray")
    $ts = Get-Now
    Write-Host "  [probe $ts] $Msg" -ForegroundColor $Color
}

# ---- Layer 1: /health ----
Write-Probe "waiting for $Url/health (timeout=${TimeoutSec}s, poll=${PollIntervalMs}ms)" "Cyan"
$healthDeadline = (Get-Date).AddSeconds($TimeoutSec)
$healthStart = Get-Date
$healthOk = $false
while ((Get-Date) -lt $healthDeadline) {
    try {
        $r = Invoke-WebRequest -Uri "$Url/health" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) {
            $healthOk = $true
            break
        }
    } catch { }
    Start-Sleep -Milliseconds $PollIntervalMs
}
if (-not $healthOk) {
    Write-Probe "FAIL: /health did not return 200 within ${TimeoutSec}s" "Red"
    return $false
}
$healthMs = [int]((Get-Date) - $healthStart).TotalMilliseconds
Write-Probe "OK   /health 200 in ${healthMs}ms" "Green"

# ---- Layer 2: /v1/models ----
$modelsStart = Get-Date
$modelId = $null
$modelsDeadline = (Get-Date).AddSeconds(60)  # /health is up, loader shouldn't take long
try {
    $resp = Invoke-RestMethod -Uri "$Url/v1/models" -TimeoutSec 5
    if ($resp.data -and $resp.data.Count -gt 0) {
        $modelId = $resp.data[0].id
    }
} catch {
    Write-Probe "FAIL: /v1/models threw: $($_.Exception.Message.Split([Environment]::NewLine)[0])" "Red"
    return $false
}
if (-not $modelId) {
    Write-Probe "FAIL: /v1/models returned no data" "Red"
    return $false
}
$modelsMs = [int]((Get-Date) - $modelsStart).TotalMilliseconds
Write-Probe "OK   /v1/models -> $modelId in ${modelsMs}ms" "Green"

# ---- Layer 3: /v1/completions (model warm-up) ----
if ($SkipCompletion) {
    Write-Probe "skip /v1/completions (SkipCompletion flag set)" "Yellow"
    return $true
}

$completionStart = Get-Date
$completionDeadline = (Get-Date).AddSeconds(60)
$completionOk = $false
$lastErr = $null
while ((Get-Date) -lt $completionDeadline) {
    $body = @{
        model    = $modelId
        prompt   = "ping"
        max_tokens = 4
        temperature = 0
        stop     = @("`n")
    } | ConvertTo-Json -Depth 4

    try {
        $r = Invoke-RestMethod -Uri "$Url/v1/completions" `
            -Method POST -Body $body -ContentType "application/json" -TimeoutSec 30
        if ($r.choices -and $r.choices.Count -gt 0 -and $r.choices[0].text) {
            $completionOk = $true
            $sample = $r.choices[0].text.Trim()
            break
        } else {
            $lastErr = "empty choices[0].text"
        }
    } catch {
        $lastErr = $_.Exception.Message.Split([Environment]::NewLine)[0]
    }
    Start-Sleep -Seconds 1
}
$completionMs = [int]((Get-Date) - $completionStart).TotalMilliseconds
if (-not $completionOk) {
    Write-Probe "FAIL: /v1/completions did not return text within 60s ($lastErr)" "Red"
    return $false
}
$snippet = if ($sample.Length -gt 40) { $sample.Substring(0, 40) + '...' } else { $sample }
Write-Probe "OK   /v1/completions in ${completionMs}ms (sample: '$snippet')" "Green"

# ---- Summary ----
$totalMs = [int]((Get-Date) - $healthStart).TotalMilliseconds
Write-Probe "ALL OK in ${totalMs}ms" "Cyan"
return $true
