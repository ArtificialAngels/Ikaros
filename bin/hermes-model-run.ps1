<#
.SYNOPSIS
Hermes Model Running — real-time llama-server reasoning viewer.

Tail data\logs\llm-server.log + llm-server.err with smart highlighting:
  - model load progress (loaded meta data, offloading layers, ...)
  - HTTP requests (srv: request: POST /v1/chat/completions)
  - inference timing (prompt eval time, eval time, total time, tokens/s)
  - errors / warnings in red

Window title: "Hermes Model Running"
Press Ctrl+C to close.
#>

$ErrorActionPreference = "Continue"
try { $Host.UI.RawUI.WindowTitle = "Hermes Model Running" } catch { }
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$HERMES_ROOT = Split-Path -Parent $PSScriptRoot
$LLM_DIR = Join-Path $HERMES_ROOT "data\logs"

# Source definitions: label, path, color, prefix
$sources = @(
    @{
        Label  = "LLM   "
        Path   = (Join-Path $LLM_DIR "llm-server.log")
        Color  = "Cyan"
        Prefix = "[LLM]    "
    },
    @{
        Label  = "LLMERR"
        Path   = (Join-Path $LLM_DIR "llm-server.err")
        Color  = "DarkYellow"
        Prefix = "[LLMERR] "
    }
)

function Write-Banner {
    # Clear-Host uses $RawUI.CursorPosition which throws SetValueInvocationException
    # in non-interactive hosts (e.g. orphan detached powershell processes).
    # Swallow any handle error and just print the banner.
    try { Clear-Host } catch { }
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  Hermes Model Running  -  Live LLM Backend"               -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  Tailing:"                                                 -ForegroundColor Yellow
    Write-Host "    - $LLM_DIR\llm-server.log"                             -ForegroundColor Gray
    Write-Host "    - $LLM_DIR\llm-server.err"                             -ForegroundColor Gray
    Write-Host ""
    Write-Host "  What you'll see:"                                         -ForegroundColor Yellow
    Write-Host "    model load  /  layer offload  /  HTTP request"          -ForegroundColor Gray
    Write-Host "    prompt eval  /  generation  /  tokens-per-second"        -ForegroundColor Gray
    Write-Host ""
    Write-Host "  Ctrl+C to close  (also closed by bin\hermes-stop.bat)"    -ForegroundColor DarkGray
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
}

# Color the line based on content
function Format-Line {
    param([string]$Text)

    if ($Text -match "^\s*llama_model_loader|llm_load_tensors|loaded meta data|load_tensors") {
        return @{ Color = "Magenta"; Text = $Text }
    }
    if ($Text -match "offloading|offloaded|to GPU|to CPU") {
        return @{ Color = "DarkMagenta"; Text = $Text }
    }
    if ($Text -match "main:.*HTTP server listening|server is listening") {
        return @{ Color = "Green"; Text = $Text }
    }
    if ($Text -match "srv: request:") {
        return @{ Color = "Cyan"; Text = $Text }
    }
    if ($Text -match "prompt eval time|^\s*eval time|total time|tokens per second|model load time") {
        return @{ Color = "Yellow"; Text = $Text }
    }
    if ($Text -match "^\s*\d+ tokens generated|generated \d+ tokens") {
        return @{ Color = "Green"; Text = $Text }
    }
    if ($Text -match "error|ERROR|fatal|FATAL|failed|FAILED|exception|EXCEPTION|abort|ABORT") {
        return @{ Color = "Red"; Text = $Text }
    }
    if ($Text -match "warn|WARN|warning") {
        return @{ Color = "DarkYellow"; Text = $Text }
    }
    if ($Text -match "build info|system info|main:.*build=|main:.*system") {
        return @{ Color = "DarkCyan"; Text = $Text }
    }

    return @{ Color = "Gray"; Text = $Text }
}

# ---- Boot banner ----
Write-Banner

# Initial dump: last 5 lines of each existing log
foreach ($src in $sources) {
    $src | Add-Member -NotePropertyName Pos -NotePropertyValue 0 -Force
    if (Test-Path $src.Path) {
        $content = Get-Content $src.Path -Tail 5 -Encoding UTF8 -ErrorAction SilentlyContinue
        $src.Pos = (Get-Item $src.Path -ErrorAction SilentlyContinue).Length
        Write-Host "  $($src.Prefix) $($src.Path) (last 5 lines)" -ForegroundColor $src.Color
        foreach ($line in $content) {
            $f = Format-Line -Text $line
            Write-Host "  $($src.Prefix) $($f.Text)" -ForegroundColor $f.Color
        }
    } else {
        Write-Host "  $($src.Prefix) (waiting for file to appear...)" -ForegroundColor DarkGray
    }
}

Write-Host ""
Write-Host "  Watching for new lines..." -ForegroundColor DarkGray
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

$lastLines = @{}

# ---- Main polling loop ----
while ($true) {
    foreach ($src in $sources) {
        if (-not (Test-Path $src.Path)) { continue }

        try {
            $currentLen = (Get-Item $src.Path).Length
            if ($currentLen -gt $src.Pos) {
                $stream = [System.IO.File]::Open(
                    $src.Path,
                    [System.IO.FileMode]::Open,
                    [System.IO.FileAccess]::Read,
                    [System.IO.FileShare]::ReadWrite
                )
                $stream.Seek($src.Pos, [System.IO.SeekOrigin]::Begin) | Out-Null
                $reader = New-Object System.IO.StreamReader($stream, [Text.Encoding]::UTF8)

                $line = $reader.ReadLine()
                while ($line -ne $null) {
                    $trimmed = $line.Trim()
                    if ($trimmed -and $trimmed -ne $lastLines[$src.Label]) {
                        $ts = Get-Date -Format "HH:mm:ss"
                        $f = Format-Line -Text $trimmed
                        Write-Host "$($src.Prefix) $ts " -NoNewline -ForegroundColor $src.Color
                        Write-Host $f.Text -ForegroundColor $f.Color
                        $lastLines[$src.Label] = $trimmed
                    }
                    $line = $reader.ReadLine()
                }

                $src.Pos = $stream.Position
                $reader.Close()
                $stream.Close()
            }
        } catch {
            # File may be locked by writer; skip this round
        }
    }
    Start-Sleep -Milliseconds 400
}
