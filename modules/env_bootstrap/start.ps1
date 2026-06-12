# modules/env_bootstrap/start.ps1 — Environment bootstrap
. $PSScriptRoot\..\..\deps\hermes-env.ps1

Write-Host "============================================================"
Write-Host "  Hermes - env_bootstrap"
Write-Host ""
Write-Host "  Checking environment..."
Write-Host "============================================================"
Write-Host ""

# Check Python
if (Test-Path $PYTHON) {
    $ver = & $PYTHON --version 2>&1
    Write-Host "  Python:     $ver ($PYTHON)" -ForegroundColor Green
} else {
    Write-Host "  [MISSING] Python not found at $PYTHON" -ForegroundColor Red
}

# Check Node
if (Test-Path $NODE) {
    $ver = & $NODE --version 2>&1
    Write-Host "  Node:       $ver ($NODE)" -ForegroundColor Green
} else {
    Write-Host "  [MISSING] Node not found at $NODE" -ForegroundColor Red
}

# Check llama-server
$llamaServer = Join-Path $LLAMACPP_BIN 'llama-server.exe'
if (Test-Path $llamaServer) {
    Write-Host "  llama-server: present" -ForegroundColor Green
} else {
    Write-Host "  [MISSING] llama-server not found" -ForegroundColor Red
}

# Check GGUF models
$models = Get-ChildItem "$HERMES_ROOT\data\models" -Filter '*.gguf' -ErrorAction SilentlyContinue
if ($models) {
    Write-Host "  Models:     $($models.Count) GGUF file(s)" -ForegroundColor Green
} else {
    Write-Host "  [MISSING] No .gguf models in data/models" -ForegroundColor Yellow
}

# Invoke the canonical Python GPU detection (status subcommand).
# We always run it; status is cheap and surfaces a hard error if Python or
# nvidia-smi is missing. Output goes to data\logs\env-bootstrap.log so
# the operator can grep for problems later.
$logFile = Join-Path $HERMES_ROOT 'data\logs\env-bootstrap.log'
$statusLog = & $PYTHON -m modules.env_bootstrap status 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "  env_bootstrap: GPU detection OK" -ForegroundColor Green
    $statusLog | ForEach-Object { Write-Host "    $_" }
} else {
    Write-Host ""
    Write-Host "  [WARN] env_bootstrap status check failed; falling back to CPU/Vulkan." -ForegroundColor Yellow
    $statusLog | ForEach-Object { Write-Host "    $_" }
}
$statusLog | Out-File -FilePath $logFile -Encoding utf8
