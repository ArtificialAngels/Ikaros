<#
.SYNOPSIS
Hermes Console - persistent model management shell.
#>
param()

$ErrorActionPreference = "Continue"
$Host.UI.RawUI.WindowTitle = "Hermes Console"
$HERMES_ROOT = Split-Path -Parent $PSScriptRoot
$MODELS_DIR = Join-Path $HERMES_ROOT "data\models"
$LLAMA_PORT = 8080
$API_URL = "http://127.0.0.1:" + $LLAMA_PORT + "/v1"
$API_KEY = "not-needed"

function Write-Banner {
    Clear-Host
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  Hermes Console - Model Management" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Get-CurrentModel {
    try {
        $modelsUrl = "http://127.0.0.1:" + $LLAMA_PORT + "/v1/models"
        $r = Invoke-RestMethod -Uri $modelsUrl -TimeoutSec 3
        if ($r.data -and $r.data.Count -gt 0) {
            return $r.data[0].id
        }
    } catch {
        return $null
    }
    return $null
}

function Get-ModelFiles {
    Get-ChildItem -Path $MODELS_DIR -Filter "*.gguf" -ErrorAction SilentlyContinue |
        Sort-Object Length -Descending |
        ForEach-Object {
            [PSCustomObject]@{
                Name = $_.Name
                SizeGB = [math]::Round($_.Length / 1GB, 2)
                Path = $_.FullName
            }
        }
}

function Show-Status {
    $model = Get-CurrentModel
    Write-Host "  [Connection Info]" -ForegroundColor Yellow
    Write-Host "  URL:        $API_URL"
    Write-Host "  API Key:    $API_KEY"
    if ($model) {
        Write-Host "  Model:      $model" -ForegroundColor Green
    } else {
        Write-Host "  Model:      (llama-server not running)" -ForegroundColor Red
    }
    Write-Host ""
}

function Switch-Model {
    param([string]$ModelPath, [string]$ModelName)

    Write-Host ""
    Write-Host "  ============================================================" -ForegroundColor Cyan
    Write-Host "  Switching to: $ModelName" -ForegroundColor Yellow
    Write-Host "  Path: $ModelPath" -ForegroundColor Gray
    Write-Host "  ============================================================" -ForegroundColor Cyan
    Write-Host ""

    # Step 1: Stop old llama-server
    Write-Host "  [1/5] Stopping llama-server..." -ForegroundColor Gray
    try {
        Get-Process -Name "llama-server*" -ErrorAction SilentlyContinue | Stop-Process -Force
    } catch {}
    Start-Sleep -Seconds 2
    try {
        Get-Process -Name "llama-server*" -ErrorAction SilentlyContinue | Stop-Process -Force
    } catch {}
    Start-Sleep -Seconds 1
    Write-Host "  Stopped." -ForegroundColor Green

    # Step 2: Start new model
    # Use Start-Process with -FilePath + -ArgumentList. The PowerShell
    # array-form argument list handles the spaces-in-paths quoting
    # automatically, and ShellExecuteEx (Start-Process's default) keeps
    # the child bat (and the llama-server it spawns) detached from this
    # Console's PowerShell session — so closing Console / pressing [Q]
    # won't kill the server.
    #
    # We pass the model path on the bat's argv (`-ArgumentList $ModelPath`).
    # start-llm-smart.bat's %~1 already supports this.
    Write-Host "  [2/5] Starting llama-server with new model..." -ForegroundColor Gray
    $startBat = Join-Path $HERMES_ROOT "bin\start-llm-smart.bat"
    $logDir = Join-Path $HERMES_ROOT "data\logs"
    $logPath = Join-Path $logDir "llm-server.log"
    $errPath = Join-Path $logDir "llm-server.err"
    # Truncate the previous run's logs so the new server's startup is unambiguous.
    "" | Set-Content $logPath -Encoding UTF8
    "" | Set-Content $errPath -Encoding UTF8

    $proc = Start-Process `
        -FilePath $startBat `
        -ArgumentList @($ModelPath) `
        -WorkingDirectory $HERMES_ROOT `
        -WindowStyle Hidden `
        -PassThru
    Write-Host "  Launched bat (pid=$($proc.Id)). Waiting for server to be ready..." -ForegroundColor Gray

    # Layered liveness probe — /health, /v1/models, /v1/completions.
    # Reports each milestone with a wall-clock timestamp so the user can
    # see exactly how long model load vs warm-up actually took.
    # . .\bin\hermes-health.ps1
    . (Join-Path $HERMES_ROOT "bin\hermes-health.ps1")
    $serverBase = "http://127.0.0.1:" + $LLAMA_PORT
    $ready = Wait-LlamaReady -Url $serverBase -TimeoutSec 180 -PollIntervalMs 500

    if (-not $ready) {
        Write-Host "  ERROR: llama-server did not become ready within 180s" -ForegroundColor Red
        Write-Host "  Tail of data\logs\llm-server.err:" -ForegroundColor Yellow
        if (Test-Path $errPath) {
            Get-Content $errPath -Tail 8 -ErrorAction SilentlyContinue | ForEach-Object {
                Write-Host "    $_" -ForegroundColor DarkYellow
            }
        }
        return $false
    }
    $waitMsg = "  llama-server is ready"
    Write-Host $waitMsg -ForegroundColor Green

    # Step 3: Verify model from server
    Write-Host "  [3/5] Verifying model from server..." -ForegroundColor Gray
    $displayModel = [System.IO.Path]::GetFileNameWithoutExtension($ModelName)
    $serverModel = ""
    $expectedAlias = $displayModel -replace '[.\s-]+', '_'
    $modelMatches = $true
    try {
        $modelsUrl = "http://127.0.0.1:" + $LLAMA_PORT + "/v1/models"
        $r = Invoke-RestMethod -Uri $modelsUrl -TimeoutSec 3
        if ($r.data -and $r.data.Count -gt 0) {
            $serverModel = $r.data[0].id
            $modelMsg = "  Server reports model ID: " + $serverModel
            Write-Host $modelMsg -ForegroundColor Cyan
            if ($serverModel -ne $expectedAlias) {
                $modelMatches = $false
                $mm1 = "  MISMATCH: requested alias '" + $expectedAlias + "' but server has '" + $serverModel + "'"
                Write-Host $mm1 -ForegroundColor Red
                Write-Host "  This usually means a previous llama-server was not killed," -ForegroundColor Red
                Write-Host "  or the LLAMA_MODEL env var from a parent process overrode the requested model." -ForegroundColor Red
            } else {
                Write-Host "  Alias matches requested model." -ForegroundColor Green
            }
        }
    } catch {
        Write-Host "  WARNING: Could not query /v1/models" -ForegroundColor Yellow
    }

    # Step 4: Update config.yaml
    Write-Host "  [4/5] Updating config.yaml..." -ForegroundColor Gray
    $configPath = Join-Path $HERMES_ROOT "data\hermes-agent\config.yaml"

    if (Test-Path $configPath) {
        # Context length to declare to hermes-agent in data/hermes-agent/config.yaml.
        # The agent enforces a 64000 minimum for tool-calling workflows (see
        # hermes-agent-source/agent/model_metadata.py MINIMUM_CONTEXT_LENGTH).
        # 3B's n_ctx_train is only 32K, so the actual server will warn+cap to
        # 32K, but declaring 65536 here passes the pre-flight check and lets
        # the user override. Larger models use their full training context.
        $ctxLen = 65536
        if ($ModelName -match "35B") { $ctxLen = 131072 }

        $lines = Get-Content $configPath -Encoding UTF8
        $newLines = @()
        $inModelSection = $false
        $inCustomProviders = $false

        foreach ($line in $lines) {
            if ($line -match "^\s*model:\s*$") {
                $inModelSection = $true
                $inCustomProviders = $false
                $newLines = $newLines + $line
                continue
            }
            if ($line -match "^custom_providers:") {
                $inModelSection = $false
                $inCustomProviders = $true
                $newLines = $newLines + $line
                continue
            }
            if ($line -match "^[a-z_]+:" -and $line -notmatch "^\s+") {
                if ($inModelSection) { $inModelSection = $false }
                if ($inCustomProviders) { $inCustomProviders = $false }
            }

            if ($inModelSection -and $line -match "^\s+default:\s*") {
                $newLines = $newLines + ("  default: " + $displayModel)
                continue
            }
            if ($inModelSection -and $line -match "^\s+provider:\s*") {
                $newLines = $newLines + "  provider: custom:localhost"
                continue
            }
            if ($inModelSection -and $line -match "^\s+context_length:\s*") {
                $newLines = $newLines + ("  context_length: " + $ctxLen)
                continue
            }
            if ($inCustomProviders -and $line -match "model:\s*") {
                $newLines = $newLines + ("    model: " + $displayModel)
                continue
            }

            $newLines = $newLines + $line
        }

        Set-Content $configPath -Value $newLines -Encoding UTF8
        $updateMsg = "  config.yaml updated (model: " + $displayModel + ", context: " + $ctxLen + ")"
        Write-Host $updateMsg -ForegroundColor Green
    } else {
        Write-Host "  WARNING: config.yaml not found at $configPath" -ForegroundColor Yellow
    }

    # Step 5: Verify by sending test request
    Write-Host "  [5/5] Verification: Sending test request to model..." -ForegroundColor Gray
    $verifyOk = $false
    $chatUrl = "http://127.0.0.1:" + $LLAMA_PORT + "/v1/chat/completions"
    $completionsUrl = "http://127.0.0.1:" + $LLAMA_PORT + "/v1/completions"

    $reqModel = $displayModel
    if ($serverModel) { $reqModel = $serverModel }

    try {
        $msgText = "Please briefly introduce yourself, including your parameter count (e.g. 3B, 7B, 35B). Reply in one short sentence in English."
        $msg = @{ role = "user"; content = $msgText }
        $bodyObj = @{
            model = $reqModel
            messages = @($msg)
            max_tokens = 200
            temperature = 0.7
        }
        $body = $bodyObj | ConvertTo-Json

        $resp = Invoke-RestMethod -Uri $chatUrl -Method POST -Body $body -ContentType "application/json" -TimeoutSec 60

        if ($resp.choices -and $resp.choices.Count -gt 0) {
            $reply = $resp.choices[0].message.content.Trim()
            Write-Host ""
            Write-Host "  --- Model Response ---" -ForegroundColor Cyan
            Write-Host "  $reply" -ForegroundColor Green
            Write-Host "  ---------------------" -ForegroundColor Cyan
            Write-Host ""
            Write-Host "  VERIFICATION OK: Model is responding." -ForegroundColor Green
            $verifyOk = $true

            # Show memory info for cross-check
            try {
                $procList = Get-Process -Name "llama-server*" -ErrorAction SilentlyContinue
                if ($procList -and $procList.Count -gt 0) {
                    $memSum = ($procList | Measure-Object WorkingSet64 -Sum).Sum
                    $memMB = [math]::Round($memSum / 1MB, 0)
                    $memMsg = "  llama-server memory usage: " + $memMB + " MB"
                    Write-Host $memMsg -ForegroundColor Gray
                }
            } catch {}
        } else {
            Write-Host "  WARNING: Empty response from model" -ForegroundColor Yellow
        }
    } catch {
        $errMsg = "  Test request failed: " + $_.Exception.Message
        Write-Host $errMsg -ForegroundColor Yellow

        # Try direct completion as fallback
        try {
            $body2Obj = @{
                model = $reqModel
                prompt = "Who are you? Answer in one sentence."
                max_tokens = 100
            }
            $body2 = $body2Obj | ConvertTo-Json
            $resp2 = Invoke-RestMethod -Uri $completionsUrl -Method POST -Body $body2 -ContentType "application/json" -TimeoutSec 60
            if ($resp2.choices -and $resp2.choices.Count -gt 0) {
                $fallbackText = $resp2.choices[0].text.Trim()
                $fbMsg = "  Fallback response: " + $fallbackText
                Write-Host $fbMsg -ForegroundColor Cyan
                $verifyOk = $true
            }
        } catch {
            $fbErr = "  Fallback also failed: " + $_.Exception.Message
            Write-Host $fbErr -ForegroundColor Red
        }
    }

    Write-Host ""
    Write-Host "  ============================================================" -ForegroundColor Cyan
    if ($verifyOk) {
        if ($modelMatches) {
            Write-Host "  SUCCESS: Switched to $displayModel (verified)" -ForegroundColor Green
        } else {
            Write-Host "  FAILED: Server is still running a different model ($serverModel)" -ForegroundColor Red
            Write-Host "  See MISMATCH message above. Try [Q] to quit console and run hermes-stop.bat to clear all server processes." -ForegroundColor Red
        }
    } else {
        Write-Host "  WARNING: Switch may have failed - model not responding correctly" -ForegroundColor Yellow
        Write-Host "  Please check the llama-server window for errors." -ForegroundColor Yellow
    }
    Write-Host "  ============================================================" -ForegroundColor Cyan
    Write-Host ""

    return $true
}

function Show-Menu {
    Write-Host "  [Available Models]" -ForegroundColor Yellow
    $models = @(Get-ModelFiles)
    if ($models.Count -eq 0) {
        Write-Host "  (no .gguf files in data\models\)" -ForegroundColor Red
        return
    }

    $currentModelName = Get-CurrentModel

    for ($i = 0; $i -lt $models.Count; $i++) {
        $m = $models[$i]
        $num = $i + 1
        $marker = ""
        if ($currentModelName -and $m.Name -like "*$currentModelName*") {
            $marker = "  [ACTIVE]"
        }
        $sizeStr = $m.SizeGB.ToString() + " GB"
        $line = "  [" + $num + "] " + $m.Name + "  (" + $sizeStr + ")" + $marker
        Write-Host $line
    }

    Write-Host ""
    Write-Host "  [R] Refresh status"
    Write-Host "  [C] Copy API URL to clipboard"
    Write-Host "  [Q] Quit"
    Write-Host "============================================================" -ForegroundColor Cyan
}

function Copy-ToClipboard {
    param([string]$Text)
    try {
        Set-Clipboard -Value $Text
        Write-Host "  Copied: $Text" -ForegroundColor Green
    } catch {
        Write-Host "  Clipboard access failed: $_" -ForegroundColor Red
    }
}

# ---- Main Loop ----
$exit = $false
while (-not $exit) {
    Write-Banner
    Show-Status
    Show-Menu

    $choice = Read-Host "  Choice"

    switch -Regex ($choice.Trim().ToUpper()) {
        "^[0-9]+$" {
            $models = @(Get-ModelFiles)
            $idx = [int]$choice - 1
            if ($idx -ge 0 -and $idx -lt $models.Count) {
                $m = $models[$idx]
                Switch-Model -ModelPath $m.Path -ModelName $m.Name
                Write-Host "  Switching complete. Refreshing..." -ForegroundColor Green
                Start-Sleep -Seconds 2
            } else {
                Write-Host "  Invalid number." -ForegroundColor Red
                Start-Sleep -Milliseconds 800
            }
        }
        "^R$" {
            # Refresh - just loops
        }
        "^C$" {
            $model = Get-CurrentModel
            if ($model) {
                $info = "URL:     $API_URL`nAPI Key: $API_KEY`nModel:   $model"
                Copy-ToClipboard -Text $info
            } else {
                Write-Host "  llama-server not running. Start a model first." -ForegroundColor Red
            }
            Read-Host "  Press Enter to continue"
        }
        "^Q$" {
            $exit = $true
        }
        default {
            Write-Host "  Unknown choice." -ForegroundColor Red
            Start-Sleep -Milliseconds 800
        }
    }
}

Write-Host "  Console closed." -ForegroundColor Cyan
