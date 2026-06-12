# modules/model_manager/start.ps1 — Model management CLI
. $PSScriptRoot\..\..\deps\hermes-env.ps1

Write-Host "============================================================"
Write-Host "  Hermes - model_manager"
Write-Host ""
Write-Host "  Model directory: $HERMES_MODELS_DIR"
Write-Host "============================================================"
Write-Host ""

$models = Get-ChildItem $HERMES_MODELS_DIR -Filter '*.gguf' -ErrorAction SilentlyContinue | Sort-Object Length -Descending
if ($models) {
    Write-Host "  Discovered $($models.Count) model(s):"
    foreach ($m in $models) {
        $szGB = [math]::Round($m.Length / 1GB, 2)
        Write-Host ("    [{0,6} GB] {1}" -f $szGB, $m.Name)
    }
} else {
    Write-Host "  No .gguf models found."
}

Write-Host ""
Write-Host "  model_manager: OK"

# Run the unified CLI's list subcommand to give a one-shot summary of the
# current model set (matches what bin/hermes-models.py shows). We capture
# its output and echo it so the operator can see what's installed.
$listOut = & $PYTHON -m modules.model_manager.manager list 2>&1
if ($LASTEXITCODE -eq 0) {
    $listOut | ForEach-Object { Write-Host "    $_" }
} else {
    Write-Host "    [WARN] manager list failed; see data\logs\model-manager.log" -ForegroundColor Yellow
    $listOut | Out-File -FilePath (Join-Path $HERMES_ROOT 'data\logs\model-manager.log') -Encoding utf8
}
