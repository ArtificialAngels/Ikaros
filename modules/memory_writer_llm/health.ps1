# modules/memory_writer_llm/health.ps1 — HTTP /health probe on :8589
$Port = 8589
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1`:$Port/health" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    if ($r.StatusCode -eq 200) {
        $body = $r.Content | ConvertFrom-Json -ErrorAction SilentlyContinue
        if ($body.status -eq 'ok') {
            Write-Output 'OK'
            exit 0
        }
    }
    Write-Output "FAIL: status=$($r.StatusCode)"
} catch {
    Write-Output "FAIL: $_"
}
exit 1
