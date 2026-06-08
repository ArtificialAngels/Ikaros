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

    This file is designed to be BOTH:
      - Dot-sourced (`. .\bin\hermes-health.ps1`) from another script
        which then calls `Wait-LlamaReady` directly. This is how
        `hermes-console.ps1` consumes the probe.
      - Run directly (`.\hermes-health.ps1 -Url ...`) for a one-off
        readiness check from the command line.

    The top-level `param()` block sits BELOW the function so that
    dot-sourcing the file evaluates the function definition (visible
    to the caller) without trying to parse top-level cmdlet-binding
    parameters (which PowerShell would otherwise turn into a single
    big script-cmdlet, hiding everything in the file's own scope).

.PARAMETER Url
    Base URL of the llama-server, e.g. http://127.0.0.1:8080.

.PARAMETER TimeoutSec
    Hard cap on the whole probe. Defaults to 180s.

.PARAMETER PollIntervalMs
    How often to hit /health while waiting. Defaults to 500ms.

.PARAMETER SkipCompletion
    Set to skip layer 3 (the model warm-up probe).

.EXAMPLE
    .\hermes-health.ps1
.EXAMPLE
    .\hermes-health.ps1 -Url http://127.0.0.1:8080 -TimeoutSec 60
.EXAMPLE
    . .\bin\hermes-health.ps1
    if (Wait-LlamaReady -Url "http://127.0.0.1:8080") { ... }
#>

$ErrorActionPreference = "Continue"

function Get-Now {
    return (Get-Date).ToString("HH:mm:ss.fff")
}

function Write-Probe {
    param([string]$Msg, [string]$Color = "Gray")
    $ts = Get-Now
    Write-Host "  [probe $ts] $Msg" -ForegroundColor $Color
}

function Wait-LlamaReady {
    [CmdletBinding()]
    param(
        [string]$Url = "http://127.0.0.1:8080",
        [int]$TimeoutSec = 180,
        [int]$PollIntervalMs = 500,
        [switch]$SkipCompletion
    )

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
}

# ---- Independent-run mode ----
# When this file is run directly (`.\hermes-health.ps1 -Url ...`), the
# top-level scope is a normal script scope and we should call the
# function with whatever the user passed on the command line. When it
# is dot-sourced (`. .\hermes-health.ps1`) the caller's scope is the
# top-level scope, so we MUST skip the call -- otherwise we'd run the
# probe here too, polluting the caller's output.
#
# We can't use `[CmdletBinding()] param(...)` at the bottom of the file
# (PowerShell refuses to parse a second param block outside a function
# or top-of-file position), so we parse $args by hand. The accepted
# flags mirror the function signature exactly.
if ($MyInvocation.InvocationName -ne '.') {
    $argUrl = "http://127.0.0.1:8080"
    $argTimeoutSec = 180
    $argPollMs = 500
    $argSkip = $false
    for ($i = 0; $i -lt $args.Count; $i++) {
        switch ($args[$i]) {
            '-Url'            { $argUrl = $args[++$i] }
            '-TimeoutSec'     { $argTimeoutSec = [int]$args[++$i] }
            '-PollIntervalMs' { $argPollMs = [int]$args[++$i] }
            '-SkipCompletion' { $argSkip = $true }
            '-h' { Write-Host "usage: hermes-health.ps1 [-Url U] [-TimeoutSec N] [-PollIntervalMs N] [-SkipCompletion]"; exit 0 }
            default {
                Write-Host "  [probe] unknown arg: $($args[$i]) (use -h for help)" -ForegroundColor Yellow
            }
        }
    }
    Wait-LlamaReady -Url $argUrl -TimeoutSec $argTimeoutSec -PollIntervalMs $argPollMs -SkipCompletion:$argSkip | Out-Null
}
