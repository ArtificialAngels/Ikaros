# See docs/scripts/core/env/scripts/detect-root.md
# 2026-08-18: 移除 HERMES_ROOT 兼容与 hermes-agent marker

function Find-IkarosRoot {
    # Priority 1: IKAROS_ROOT env var
    if ($env:IKAROS_ROOT -and (Test-Path $env:IKAROS_ROOT)) {
        $root = (Resolve-Path $env:IKAROS_ROOT).Path
        if (Test-Path "$root\runtime\portable-python\python.exe") {
            return $root
        }
    }

    # Priority 2: Script is at Ikaros-environment\scripts\, root is parent of parent
    $scriptDir = $PSScriptRoot
    $candidate = (Resolve-Path "$scriptDir\..\..").Path
    if (Test-Path "$candidate\runtime\portable-python\python.exe") {
        return $candidate
    }

    # Priority 3: Walk up from CWD looking for marker files
    $marker = "runtime\portable-python\python.exe"
    $dir = (Get-Location).Path
    while ($dir) {
        $hasPython = Test-Path "$dir\$marker"
        $hasEnv = Test-Path "$dir\core\env"
        if ($hasPython -and $hasEnv) {
            return $dir
        }
        $parent = Split-Path $dir -Parent
        if ($parent -eq $dir) { break }
        $dir = $parent
    }

    # Priority 4: Drive scan (slow, last resort)
    foreach ($letter in (Get-PSDrive -PSProvider FileSystem | Select-Object -ExpandProperty Root)) {
        $candidate = Join-Path $letter "Ikaros"
        if (Test-Path "$candidate\runtime\portable-python\python.exe") {
            return $candidate
        }
    }

    throw "IKAROS_ROOT not found. Set IKAROS_ROOT env var."
}

$root = Find-IkarosRoot
Write-Output $root
