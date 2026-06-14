# Smoke test: dot-source deps/hermes-env.ps1 and verify the resolved
# vars match the canonical runtime/* paths (see AGENTS.md §3, 2026-06-13
# junction audit for why the legacy deps/* junctions are gone).

. "E:\Hermes Agent\deps\hermes-env.ps1"

Write-Host "HERMES_RUNTIME = $env:HERMES_RUNTIME"
Write-Host "HERMES_PYTHON  = $env:HERMES_PYTHON"
Write-Host "HERMES_DEPS    = $env:HERMES_DEPS"
Write-Host ""
Write-Host "Script-scope aliases:"
Write-Host "  PYTHON       = $PYTHON"
Write-Host "  LLAMACPP_BIN = $LLAMACPP_BIN"
Write-Host "  NODE_BIN_DIR = $NODE_BIN_DIR"
Write-Host "  NODE         = $NODE"

# These are the critical assertions: NODE must resolve to runtime/node23/node.exe
# (NOT deps/node/node.exe, which would be a stale junction path).
$expected = @{
    "PYTHON"       = "E:\Hermes Agent\portable-python\python.exe"
    "LLAMACPP_BIN" = "E:\Hermes Agent\runtime"
    "NODE_BIN_DIR" = "E:\Hermes Agent\runtime\node23"
    "NODE"         = "E:\Hermes Agent\runtime\node23\node.exe"
}

$fail = $false
foreach ($k in $expected.Keys) {
    $actual = (Get-Variable -Name $k -ValueOnly -Scope Script)
    $want   = $expected[$k]
    if ($actual -eq $want) {
        Write-Host "  [OK]   $k = $actual"
    } else {
        Write-Host "  [FAIL] $k = $actual  (expected: $want)"
        $fail = $true
    }
}

if ($fail) {
    Write-Host ""
    Write-Host "[FAIL] one or more aliases resolve to the wrong path." -ForegroundColor Red
    exit 1
}
Write-Host ""
Write-Host "[OK] all canonical paths verified" -ForegroundColor Green
exit 0
