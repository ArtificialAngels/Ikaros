<#
.SYNOPSIS
Hermes Trace — real-time backend log viewer.
Tail server, bridge, and agent logs with color-coded output.
Uses FileSystemWatcher for reliable multi-file tailing.
#>

$ErrorActionPreference = "Continue"
$Host.UI.RawUI.WindowTitle = "Hermes Trace"
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$HERMES_ROOT = Split-Path -Parent $PSScriptRoot
$WEBUI_HOME = Join-Path $HERMES_ROOT "data\webui-new\data"
$AGENT_LOGS = Join-Path $HERMES_ROOT "hermes\data\logs"

# Source definitions: label, path, color, prefix
$sources = @(
    @{Label="WEBUI "; Path=(Join-Path $WEBUI_HOME "logs\server.log"); Color="Cyan";   Prefix="[WEBUI]  "; Pos=0},
    @{Label="BRIDGE"; Path=(Join-Path $WEBUI_HOME "logs\bridge.log"); Color="Yellow";  Prefix="[BRIDGE] "; Pos=0},
    @{Label="AGENT "; Path=(Join-Path $AGENT_LOGS  "hermes.log");      Color="Green";   Prefix="[AGENT]  "; Pos=0}
)

# Show header
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Hermes Trace — Real-time Log Viewer"                      -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

foreach ($src in $sources) {
    $exists = Test-Path $src.Path
    if ($exists) {
        $content = Get-Content $src.Path -Tail 5 -Encoding UTF8 -ErrorAction SilentlyContinue
        $src.Pos = (Get-Item $src.Path -ErrorAction SilentlyContinue).Length
        Write-Host "  $($src.Prefix) $($src.Path) (last 5 lines)" -ForegroundColor $src.Color
        foreach ($line in $content) {
            Write-Host "  $($src.Prefix) $line" -ForegroundColor DarkGray
        }
    } else {
        Write-Host "  $($src.Prefix) (waiting for file...)" -ForegroundColor DarkGray
        $src.Pos = 0
    }
}

Write-Host ""
Write-Host "  Waiting... (Ctrl+C to close)" -ForegroundColor DarkGray
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Color map
$ColorMap = @{
    "Cyan"   = [ConsoleColor]::Cyan
    "Yellow" = [ConsoleColor]::Yellow
    "Green"  = [ConsoleColor]::Green
}

# Deduplication: don't re-read the same content
$lastLines = @{}

# Main polling loop
while ($true) {
    $hasOutput = $false
    foreach ($src in $sources) {
        if (-not (Test-Path $src.Path)) { continue }
        
        try {
            $currentLen = (Get-Item $src.Path).Length
            if ($currentLen -gt $src.Pos) {
                $stream = [System.IO.File]::Open($src.Path, [System.IO.FileMode]::Open, 
                    [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
                $stream.Seek($src.Pos, [System.IO.SeekOrigin]::Begin) | Out-Null
                $reader = New-Object System.IO.StreamReader($stream, [Text.Encoding]::UTF8)
                
                $line = $reader.ReadLine()
                while ($line -ne $null) {
                    $line = $line.Trim()
                    if ($line -and $line -ne $lastLines[$src.Label]) {
                        $ts = Get-Date -Format "HH:mm:ss"
                        Write-Host "$($src.Prefix) $ts " -NoNewline -ForegroundColor $ColorMap[$src.Color]
                        Write-Host $line -ForegroundColor DarkGray
                        $lastLines[$src.Label] = $line
                        $hasOutput = $true
                    }
                    $line = $reader.ReadLine()
                }
                
                $src.Pos = $stream.Position
                $reader.Close()
                $stream.Close()
            }
        } catch {
            # File may be locked - skip this round
        }
    }
    Start-Sleep -Milliseconds 500
}
