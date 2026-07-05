# ============================================================
# detect-root.ps1 - Auto-detect IKAROS_ROOT
# ============================================================
#  Try multiple sources to resolve Ikaros project root.
#  Output: resolved root path, or throw error.
# ============================================================

$ErrorActionPreference = "Stop"

function Find-IkarosRoot {
    # Priority 1: IKAROS_ROOT env var
    if ($env:IKAROS_ROOT -and (Test-Path $env:IKAROS_ROOT)) {
        $root = (Resolve-Path $env:IKAROS_ROOT).Path
        if (Test-Path "$root\portable-python\python.exe") {
            return $root
        }
    }

    # Priority 2: HERMES_ROOT env var (legacy compat)
    if ($env:HERMES_ROOT -and (Test-Path $env:HERMES_ROOT)) {
        $root = (Resolve-Path $env:HERMES_ROOT).Path
        if (Test-Path "$root\portable-python\python.exe") {
            return $root
        }
    }

    # Priority 3: Derive from script location
    # Script is at Ikaros-environment\scripts\, root is parent of parent
    $scriptDir = $PSScriptRoot
    $envDir = Split-Path $scriptDir -Parent
    $candidate = Split-Path $envDir -Parent
    if (Test-Path "$candidate\portable-python\python.exe") {
        return (Resolve-Path $candidate).Path
    }

    # Priority 4: Walk up from current working directory
    $dir = Get-Location
    while ($dir -ne $null) {
        $hasPython = Test-Path "$dir\portable-python\python.exe"
        $hasHermes = Test-Path "$dir\hermes-agent"
        $hasEnv = Test-Path "$dir\Ikaros-environment"
        if ($hasPython -and $hasHermes -and $hasEnv) {
            return (Resolve-Path $dir).Path
        }
        $dir = Split-Path $dir -Parent
    }

    # Priority 5: Scan drive letters
    $drives = Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Used -ne $null }
    foreach ($drive in $drives) {
        $candidate = Join-Path $drive.Root "Ikaros"
        if (Test-Path "$candidate\portable-python\python.exe") {
            return (Resolve-Path $candidate).Path
        }
    }

    throw "IKAROS_ROOT not found. Set IKAROS_ROOT env var."
}

# Execute and output result
$root = Find-IkarosRoot
Write-Output $root
