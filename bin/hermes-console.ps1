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

    # In router mode, llama-server hosts ALL discovered GGUF files in a
    # single process. Switching "models" doesn't need a restart — we
    # just preload the requested one (warm-loads it into VRAM) and
    # update the WebUI default. LRU evicts whatever was previously
    # resident when the new model is loaded.

    $displayModel = [System.IO.Path]::GetFileNameWithoutExtension($ModelName)
    $ggufName     = [System.IO.Path]::GetFileName($ModelPath)
    # Router-mode identifiers are the GGUF filename (or a subpath within
    # --models-dir). For our flat layout, that's just the basename.
    $routerId     = $ggufName

    Write-Host ""
    Write-Host "  ============================================================" -ForegroundColor Cyan
    Write-Host "  Switching to: $ggufName" -ForegroundColor Yellow
    Write-Host "  Path: $ModelPath" -ForegroundColor Gray
    Write-Host "  (router mode \u2014 no restart, just preloading into VRAM)" -ForegroundColor DarkGray
    Write-Host "  ============================================================" -ForegroundColor Cyan
    Write-Host ""

    # Step 1: Verify the model is known to llama-server
    Write-Host "  [1/4] Verifying model is discovered by router..." -ForegroundColor Gray
    $discovered = @()
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$LLAMA_PORT/v1/models" -TimeoutSec 3
        if ($r.data) { $discovered = @($r.data | ForEach-Object { $_.id }) }
    } catch {
        Write-Host "  ERROR: Could not reach llama-server at :$LLAMA_PORT. Is hermes-all.bat running?" -ForegroundColor Red
        return $false
    }
    if ($discovered.Count -eq 0) {
        Write-Host "  ERROR: router reports 0 models. Check --models-dir and that GGUFs exist there." -ForegroundColor Red
        return $false
    }
    if ($discovered -notcontains $routerId) {
        Write-Host "  ERROR: '$routerId' not in router's discovered list: $($discovered -join ', ')" -ForegroundColor Red
        return $false
    }
    Write-Host "  OK. Router has $($discovered.Count) model(s); '$routerId' is registered." -ForegroundColor Green

    # Step 2: Preload the model via /models/load so the first chat
    # request doesn't pay the cold-start cost.
    Write-Host "  [2/4] Preloading model (POST /models/load)..." -ForegroundColor Gray
    $loadOk = $false
    try {
        $loadUrl = "http://127.0.0.1:$LLAMA_PORT/models/load"
        $loadBody = @{ model = $routerId } | ConvertTo-Json
        $loadResp = Invoke-RestMethod -Uri $loadUrl -Method POST -Body $loadBody -ContentType "application/json" -TimeoutSec 180
        Write-Host "  Preload accepted by router." -ForegroundColor Green
        $loadOk = $true
    } catch {
        Write-Host "  WARNING: /models/load failed: $($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "  (The first chat request will still work \u2014 just with cold-start latency.)" -ForegroundColor DarkYellow
    }

    # Step 3: Update data/hermes-agent/config.yaml so the WebUI's
    # default model is the one we just picked.
    Write-Host "  [3/4] Updating config.yaml..." -ForegroundColor Gray
    $configPath = Join-Path $HERMES_ROOT "data\hermes-agent\config.yaml"
    if (Test-Path $configPath) {
        # Router-mode ctx comes from data\models\router-preset.ini per
        # model; declare 65536 here to satisfy hermes-agent's 64K minimum
        # gate (the actual server will warn+cap if the model's n_ctx_train
        # is smaller).
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
                $newLines = $newLines + ("  default: " + $routerId)
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
                $newLines = $newLines + ("    model: " + $routerId)
                continue
            }

            $newLines = $newLines + $line
        }
        Set-Content $configPath -Value $newLines -Encoding UTF8
        Write-Host "  config.yaml updated (default: $routerId, context: $ctxLen)" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: config.yaml not found at $configPath" -ForegroundColor Yellow
    }

    # Step 4: Warmup test (cheap) \u2014 confirms the model is actually
    # loadable end-to-end and reports a tiny "I am X" reply.
    Write-Host "  [4/4] Warmup test (tiny chat request)..." -ForegroundColor Gray
    $verifyOk = $false
    try {
        $chatUrl = "http://127.0.0.1:$LLAMA_PORT/v1/chat/completions"
        $bodyObj = @{
            model     = $routerId
            messages  = @(@{ role = "user"; content = "Reply in 5 words: which model are you?" })
            max_tokens = 30
            temperature = 0.7
        }
        $body = $bodyObj | ConvertTo-Json
        $resp = Invoke-RestMethod -Uri $chatUrl -Method POST -Body $body -ContentType "application/json" -TimeoutSec 120
        if ($resp.choices -and $resp.choices.Count -gt 0) {
            $reply = $resp.choices[0].message.content.Trim()
            Write-Host ""
            Write-Host "  --- Model Response ---" -ForegroundColor Cyan
            Write-Host "  $reply" -ForegroundColor Green
            Write-Host "  ---------------------" -ForegroundColor Cyan
            $verifyOk = $true
        }
    } catch {
        Write-Host "  Warmup failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "  ============================================================" -ForegroundColor Cyan
    if ($verifyOk) {
        Write-Host "  SUCCESS: $ggufName is loaded and responding." -ForegroundColor Green
        Write-Host "  Next chat request will hit this model directly. No restart needed." -ForegroundColor Green
    } else {
        Write-Host "  PARTIAL: preloaded + config updated, but warmup test failed." -ForegroundColor Yellow
        Write-Host "  The model is still selected; the next real chat request may take longer (cold start)." -ForegroundColor Yellow
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
