# modules/memory_writer_llm/stop.ps1 — kill llama-server on :8589
$Port = 8589
$netstat = & netstat -aon -p tcp 2>$null
$killed = 0
foreach ($line in $netstat) {
    if ($line -match "TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)$") {
        $zpid = [int]$matches[1]
        if ($zpid -gt 0) {
            Write-Host "  killing PID $zpid on :$Port..."
            try { & taskkill /F /PID $zpid /T 2>$null | Out-Null; $killed++ } catch {}
        }
    }
}
if ($killed -eq 0) { Write-Host '  no process on :8589' }
