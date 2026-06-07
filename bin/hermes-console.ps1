<#
.SYNOPSIS
Hermes Console — persistent model management shell.
Display current model, switch models, copy API credentials.
#>
param()

$ErrorActionPreference = "Continue"
$Host.UI.RawUI.WindowTitle = "Hermes Console"
$HERMES_ROOT = Split-Path -Parent $PSScriptRoot
$MODELS_DIR = Join-Path $HERMES_ROOT "data\models"
$LLAMA_PORT = 8080
$API_URL = "http://127.0.0.1:$LLAMA_PORT/v1"
$API_KEY = "not-needed"

function Write-Banner {
    Clear-Host
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  Hermes Console — Model Management"                      -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Get-CurrentModel {
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$LLAMA_PORT/v1/models" -TimeoutSec 3
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
    
    Write-Host "  Switching to: $ModelName" -ForegroundColor Yellow
    
    $python = Join-Path $HERMES_ROOT "portable-python\python.exe"
    $script = Join-Path $HERMES_ROOT "hermes\scripts\model_manager.py"
    
    if (-not (Test-Path $script)) {
        Write-Host "  ERROR: model_manager.py not found" -ForegroundColor Red
        return $false
    }
    
    # Phase 1: Switch the running llama-server via model_manager.py
    Write-Host "  Delegating to model_manager.py..." -ForegroundColor Gray
    $proc = Start-Process -FilePath $python -ArgumentList "`"$script`"", "switch", "`"$ModelName`"" -NoNewWindow -Wait -PassThru
    
    if ($proc.ExitCode -ne 0) {
        Write-Host "  Switch failed (exit code $($proc.ExitCode))." -ForegroundColor Red
        return $false
    }
    
    # Phase 2: Get actual model ID from llama-server
    $actualModel = $null
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$LLAMA_PORT/v1/models" -TimeoutSec 5
        if ($r.data) { $actualModel = $r.data[0].id }
    } catch {}
    
    if ($actualModel) {
        Write-Host "  Now serving: $actualModel" -ForegroundColor Cyan
    }
    
    # Phase 3: Sync config.yaml so the bridge picks up the new model
    $configPath = Join-Path $HERMES_ROOT "data\hermes-agent\config.yaml"
    if (Test-Path $configPath) {
        Write-Host "  Updating config.yaml..." -ForegroundColor Gray
        $ctxLen = 32768  # 32K for 3B
        if ($ModelName -match "7B") { $ctxLen = 65536 }
        if ($ModelName -match "35B") { $ctxLen = 131072 }
        
        # Read, update model.default and context_length
        $yaml = Get-Content $configPath -Raw -Encoding UTF8
        $displayModel = if ($actualModel) { $actualModel } else { [System.IO.Path]::GetFileNameWithoutExtension($ModelName) }
        
        # Update model section
        $yaml = $yaml -replace "(?m)^(\s*default:\s*).*$", "`${1}`"$displayModel`""
        $yaml = $yaml -replace "(?m)^(\s*provider:\s*).*$", "`${1}`"custom:本地-(127.0.0.1:8080)`""
        
        # Ensure context_length exists (Windows line endings = \r\n)
        if ($yaml -match "context_length:") {
            $yaml = $yaml -replace "(?m)^(\s*context_length:\s*)\d+", "`${1}$ctxLen"
        } else {
            $yaml = $yaml -replace "(?m)^(model:\s*\r?\n)", "`${1}  context_length: $ctxLen`r`n"
        }
        
        Set-Content $configPath -Value $yaml -Encoding UTF8 -NoNewline
        Write-Host "  config.yaml synced (context: $ctxLen)" -ForegroundColor Green
    }
    
    # Phase 4: Restart WebUI to reconnect bridge (prevents websocket errors)
    Write-Host "  Restarting WebUI..." -ForegroundColor Gray
    $webuiBat = Join-Path $HERMES_ROOT "bin\webui-new.bat"
    if (Test-Path $webuiBat) {
        & cmd /c "call `"$webuiBat`" stop" 2>$null
        Start-Sleep -Seconds 2
        & cmd /c "call `"$webuiBat`" start" 2>$null
        Write-Host "  WebUI restarted — refresh browser if needed." -ForegroundColor Green
    }
    
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
            $marker = " <-- ACTIVE"
        }
        Write-Host "  [$num] $($m.Name)  ($($m.SizeGB) GB)$marker"
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
                Start-Sleep -Seconds 1
            } else {
                Write-Host "  Invalid number." -ForegroundColor Red
                Start-Sleep -Milliseconds 800
            }
        }
        "^R$" {
            # Refresh — just loops
        }
        "^C$" {
            $model = Get-CurrentModel
            if ($model) {
                $info = @"
URL:     $API_URL
API Key: $API_KEY
Model:   $model
"@
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
